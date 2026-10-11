// 服务器模式测试：假 DOM + 假 fetch，把界面脚本真跑起来，验证
// 口令登录、读写走服务端、只读模式、409 冲突、上传失败重试、403 降级、file:// 不受影响。
// 用法: node tests/ui-server-mode.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', '作品库.html'), 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.ok(scripts.length >= 2, '没抽到两段脚本');

const OWNER_TOKEN = 'owner-token-abcdefghijklmnop';
const GUEST_TOKEN = 'guest-token-abcdefghijklmnop';
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
const failures = [];

async function t(name, fn) {
  try { await fn(); pass++; console.log('  PASS  ' + name); }
  catch (e) { fail++; failures.push(name + ' -> ' + e.message); console.log('  FAIL  ' + name + ' -> ' + e.message); }
}

/* ---------------- 假服务端 ---------------- */
function makeServer(overrides = {}) {
  return Object.assign({
    revision: 0,
    db: { kind: 'worklib-db', version: 1, settings: { insertByRule: true }, works: [], discarded: [] },
    ownerToken: OWNER_TOKEN,
    guestToken: GUEST_TOKEN,
    publicRead: false,
    forceGuest: false,          // 让 whoami 报 guest（模拟中途权限变化）
    failNextPut: null,          // { status, error }
    calls: [],
    puts: 0
  }, overrides);
}

/* ---------------- 假 DOM ---------------- */
function makeEnv(opts = {}) {
  const { server, protocol = 'http:', storedToken = null, legacyToken = null,
          mirror = null, confirmAnswers = [] } = opts;
  const cap = { downloads: [], lastBlob: null, confirms: [], prompts: [], copied: [], reloads: 0 };

  function makeEl(id) {
    return {
      id, _listeners: {}, style: {}, className: '', textContent: '', innerHTML: '',
      value: '', checked: false, hidden: false, open: false, href: '', download: '', type: '',
      addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
      removeEventListener() {},
      fire(type, ev) { (this._listeners[type] || []).forEach(fn => fn(ev || {})); },
      hasListener(type) { return !!this._listeners[type]; },
      focus() {}, blur() {}, select() {},
      click() {
        this.fire('click', {});
        if (this.download || this.href) {
          cap.downloads.push({ filename: this.download, content: cap.lastBlob ? cap.lastBlob.parts.join('') : '' });
        }
      },
      showModal() { this.open = true; },
      close() { this.open = false; },
      querySelector(sel) { return id === 'dlgSimilar' && /cand/.test(sel) ? { value: '0' } : null; },
      closest() { return null; },
      getAttribute() { return null; },
      setAttribute() {},
      appendChild() {}, removeChild() {}
    };
  }

  const els = {};
  const document = {
    getElementById: id => (els[id] = els[id] || makeEl(id)),
    createElement: tag => makeEl('created-' + tag),
    body: makeEl('body')
  };

  const lsData = {};
  const ssData = {};
  const winListeners = {};
  // 新版：口令放 sessionStorage（关页面即失效）；localStorage 里的算「老版本残留」
  if (storedToken) ssData['worklib.token'] = storedToken;
  if (legacyToken) lsData['worklib.token'] = legacyToken;
  if (mirror) lsData['worklib.v1'] = JSON.stringify(mirror);

  const confirmQueue = confirmAnswers.slice();
  const sandbox = {
    console, document,
    location: {
      protocol,
      href: protocol === 'file:' ? 'file:///D:/dsh/作品库.html' : 'http://127.0.0.1/',
      reload() { cap.reloads++; }
    },
    localStorage: {
      getItem: k => (k in lsData ? lsData[k] : null),
      setItem: (k, v) => { lsData[k] = String(v); },
      removeItem: k => { delete lsData[k]; }
    },
    sessionStorage: {
      getItem: k => (k in ssData ? ssData[k] : null),
      setItem: (k, v) => { ssData[k] = String(v); },
      removeItem: k => { delete ssData[k]; }
    },
    addEventListener: (type, fn) => { (winListeners[type] = winListeners[type] || []).push(fn); },
    removeEventListener: () => {},
    Blob: class { constructor(parts, o) { this.parts = parts; this.type = (o || {}).type; } },
    // 用 Node 真实的 URL（页面里 new URL(...) 要用），再挂上应用需要的两个静态方法
    URL: Object.assign(URL, {
      createObjectURL: b => { cap.lastBlob = b; return 'blob:x'; },
      revokeObjectURL: () => {}
    }),
    FileReader: class { readAsText() { this.result = sandbox.__fileContent; if (this.onload) this.onload(); } },
    navigator: { clipboard: { writeText: x => { cap.copied.push(x); return Promise.resolve(); } } },
    setTimeout, clearTimeout,
    fetch: (url, init) => fakeFetch(server, url, init)
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window.confirm = () => (confirmQueue.length ? confirmQueue.shift() : false);
  sandbox.window.prompt = () => (cap.prompts.length ? cap.prompts.shift() : null);
  vm.createContext(sandbox);

  const fireWindow = (type, ev) => (winListeners[type] || []).forEach(fn => fn(ev || {}));
  return { sandbox, els, cap, lsData, ssData, server, confirmQueue, fireWindow };
}

function resp(status, obj) {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(obj) };
}

function fakeFetch(server, url, init) {
  const method = (init && init.method) || 'GET';
  const headers = (init && init.headers) || {};
  const token = headers['X-Worklib-Token'] || '';
  const body = init && init.body ? JSON.parse(init.body) : null;
  server.calls.push({ method, url, token, body, revision: body && body.revision });

  if (!token && !server.publicRead) return Promise.resolve(resp(401, { ok: false, error: '需要口令' }));
  let role = null;
  if (token === server.ownerToken) role = 'owner';
  else if (token === server.guestToken) role = 'guest';
  else if (!token && server.publicRead) role = 'guest';
  if (!role) return Promise.resolve(resp(401, { ok: false, error: '口令不对' }));
  if (server.forceGuest) role = 'guest';

  const path = String(url).replace(/^.*\//, '');            // 取最后一段
  if ((path === 'whoami' || /whoami/.test(url)) && method === 'GET') {
    return Promise.resolve(resp(200, { ok: true, role, canWrite: role === 'owner', publicRead: !!server.publicRead }));
  }
  if (/api\/db/.test(url)) {
    if (method === 'GET') {
      return Promise.resolve(resp(200, { ok: true, revision: server.revision, db: server.db }));
    }
    if (method === 'PUT' || method === 'POST') {
      if (server.failNextPut) {
        const f = server.failNextPut; server.failNextPut = null;
        return Promise.resolve(resp(f.status, { ok: false, error: f.error }));
      }
      if (role !== 'owner') return Promise.resolve(resp(403, { ok: false, error: '只读口令不能修改（PUT 需要 owner 口令）' }));
      if (body && typeof body.revision === 'number' && body.revision !== server.revision) {
        return Promise.resolve(resp(409, { ok: false, error: '库已被别处修改，请重新载入后再保存' }));
      }
      server.db = body.db;
      server.revision += 1;
      server.puts += 1;
      return Promise.resolve(resp(200, { ok: true, revision: server.revision }));
    }
  }
  return Promise.resolve(resp(404, { ok: false, error: '没有这个接口' }));
}

function boot(env) {
  env.cap.__errors = [];
  scripts.forEach((code, i) => vm.runInContext(code, env.sandbox, { filename: 'srv-' + i + '.js' }));
}

const work = (name, uuid) => ({
  uuid, name, aliases: [], note: '', addedAt: '2026-01-01T00:00:00.000Z'
});
const U1 = 'xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd';
const U2 = 'xey0192f0d5-2b3c-7d4e-9f50-6b7c8d9e0f10cmd';

console.log('[服务器模式] 口令与登录');

await t('无口令：服务端 401 → 弹出登录卡片，不渲染数据', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server });
  boot(env);
  await sleep(30);
  assert.equal(env.els.loginCard.hidden, false, '登录卡片没弹出来');
  assert.equal(env.els.roleBadge.textContent, '服务器 · 只读', '未登录应显示只读');
  assert.equal(env.els.saveState.textContent, '等待口令');
  assert.doesNotMatch(env.els.tbody.innerHTML, /来自深渊/, '没口令却把数据画出来了');
});

await t('输入 owner 口令：读到服务端数据、渲染；但本机不留任何数据副本', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server });
  boot(env);
  await sleep(30);
  env.els.inpToken.value = OWNER_TOKEN;
  env.els.btnLogin.fire('click');
  await sleep(30);
  assert.equal(env.els.loginCard.hidden, true, '登录后卡片应收起');
  assert.equal(env.els.roleBadge.textContent, '服务器 · 可编辑');
  assert.equal(env.els.roleBadge.className, 'server');
  assert.match(env.els.tbody.innerHTML, /来自深渊/, '没渲染服务端数据');
  assert.match(env.els.saveState.textContent, /^已连接服务器（可编辑）/);
  // 关掉页面就该什么都不剩：localStorage 里不能有作品，也不能有口令
  assert.equal(env.lsData['worklib.v1'], undefined, '服务器模式不该把作品写进 localStorage');
  assert.equal(env.lsData['worklib.token'], undefined, '口令不该写进 localStorage');
  // 口令只活在 sessionStorage 里（关标签页即失效）
  assert.equal(env.ssData['worklib.token'], OWNER_TOKEN, '口令没放进 sessionStorage');
});

await t('口令错：明确提示重输，且不渲染服务端数据', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server, storedToken: '错的-口令' });
  boot(env);
  await sleep(30);
  assert.equal(env.els.loginCard.hidden, false);
  assert.match(env.els.loginHint.textContent, /口令不对/);
  assert.doesNotMatch(env.els.tbody.innerHTML, /来自深渊/);
});

console.log('[服务器模式] 读写走服务端');

await t('owner 改动 → 自动 PUT 到服务端，带版本号，服务端 revision 递增', async () => {
  const server = makeServer();
  const env = makeEnv({ server, storedToken: OWNER_TOKEN });
  boot(env);
  await sleep(30);
  assert.equal(server.revision, 0);

  env.els.inpName.value = '葬送的芙莉莲';
  env.els.btnAdd.fire('click');
  assert.match(env.els.saveState.textContent, /待上传/, '改动后应先显示待上传');
  await sleep(900);                                  // 等 700ms 防抖
  assert.equal(server.puts, 1, '没有 PUT 到服务端');
  assert.equal(server.revision, 1, '服务端版本号没涨');
  assert.equal(server.db.works.length, 1);
  assert.equal(server.db.works[0].name, '葬送的芙莉莲');
  const put = server.calls.filter(c => c.method === 'PUT')[0];
  assert.equal(put.revision, 0, 'PUT 里应该带旧版本号 0');
  assert.equal(put.token, OWNER_TOKEN, 'PUT 没带口令');
  assert.equal(put.body.db.revision, undefined, '不该把 revision 塞进 db 里');
  assert.match(env.els.saveState.textContent, /^已保存到服务器/);
});

await t('以服务端为准；顺手把老版本留在本机的那份镜像清掉', async () => {
  const server = makeServer({ db: { works: [work('服务端的作品', U1)], discarded: [], settings: { insertByRule: true } }, revision: 3 });
  const stale = { kind: 'worklib-db', works: [work('本机旧镜像', U2)], discarded: [], settings: { insertByRule: true } };
  const env = makeEnv({ server, storedToken: OWNER_TOKEN, mirror: stale });
  boot(env);
  await sleep(30);
  assert.match(env.els.tbody.innerHTML, /服务端的作品/, '没以服务端为准');
  assert.doesNotMatch(env.els.tbody.innerHTML, /本机旧镜像/, '把本机旧镜像当成了真数据');
  assert.match(env.els.saveState.textContent, /已连接服务器/);
  assert.equal(env.lsData['worklib.v1'], undefined, '老版本留在 localStorage 的镜像没被清掉');
});

console.log('[服务器模式] 未授权时不留痕（用户报过的问题）');

await t('本机有旧镜像 + 没口令 → 一条作品都不显示，且旧镜像被清掉', async () => {
  const server = makeServer({ db: { works: [work('别人的秘密作品', U1)], discarded: [], settings: { insertByRule: true } } });
  const stale = { kind: 'worklib-db', works: [work('上次下载的作品', U2)], discarded: [], settings: { insertByRule: true } };
  const env = makeEnv({ server, mirror: stale, legacyToken: 'old-token-from-last-version' });
  boot(env);
  await sleep(30);
  assert.equal(env.els.loginCard.hidden, false, '应该要求输口令');
  assert.equal(env.els.stat.textContent, '共 0 部作品', '没输口令却显示出了作品数');
  assert.doesNotMatch(env.els.tbody.innerHTML, /上次下载的作品/, '没输口令就把缓存里的作品画出来了');
  assert.doesNotMatch(env.els.tbody.innerHTML, /别人的秘密作品/);
  assert.equal(env.lsData['worklib.v1'], undefined, '旧镜像还留在 localStorage');
  assert.equal(env.lsData['worklib.token'], undefined, '老版本的口令还留在 localStorage');
  assert.match(env.els.loginHint.textContent, /不会显示任何作品/);
  assert.match(env.els.emptyHint.textContent, /请先输入访问口令/);
});

await t('连不上服务器 → 不拿本机数据充数，并给出可自证的排查信息 + 重连按钮', async () => {
  const server = makeServer({ db: { works: [work('服务端作品', U1)], discarded: [], settings: { insertByRule: true } } });
  const stale = { kind: 'worklib-db', works: [work('本机缓存作品', U2)], discarded: [], settings: { insertByRule: true } };
  const env = makeEnv({ server, storedToken: OWNER_TOKEN, mirror: stale });
  env.sandbox.fetch = () => Promise.reject(Object.assign(
    new Error('NetworkError when attempting to fetch resource.'), { name: 'TypeError' }));
  boot(env);
  await sleep(30);
  assert.equal(env.els.stat.textContent, '共 0 部作品', '连不上服务器却显示了本机缓存');
  assert.doesNotMatch(env.els.tbody.innerHTML, /本机缓存作品/);
  assert.match(env.els.banner.textContent, /连不上服务器/);
  assert.match(env.els.banner.textContent, /不显示任何本机缓存数据/);
  // 排查信息：请求地址 + 页面地址 + 具体错误名
  assert.match(env.els.issueText.textContent, /请求地址：http:\/\/127\.0\.0\.1\/api\/whoami/);
  assert.match(env.els.issueText.textContent, /当前页面：http:\/\/127\.0\.0\.1\//);
  assert.match(env.els.issueText.textContent, /NetworkError/);
  assert.equal(env.els.btnIssueAction.textContent, '重新连接');

  // 点「重新连接」重新走一遍（这次服务可用）
  env.sandbox.fetch = (url, init) => fakeFetch(server, url, init);
  env.els.btnIssueAction.fire('click');
  await sleep(30);
  assert.match(env.els.tbody.innerHTML, /服务端作品/, '重连后没拿到数据');
});

await t('关掉页面（pagehide）就清掉口令；只是进 bfcache 返回则保留', async () => {
  const server = makeServer();
  const env = makeEnv({ server, storedToken: OWNER_TOKEN });
  boot(env);
  await sleep(30);
  assert.equal(env.ssData['worklib.token'], OWNER_TOKEN);

  env.fireWindow('pagehide', { persisted: true });          // 进 bfcache：不清
  assert.equal(env.ssData['worklib.token'], OWNER_TOKEN, '只是返回缓存，不该清口令');

  env.fireWindow('pagehide', { persisted: false });         // 真的离开/关闭：清
  assert.equal(env.ssData['worklib.token'], undefined, '关掉页面后口令还在');
});

await t('每次打开都要求重新输口令（口令不在 localStorage，只在本次会话）', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1)], discarded: [], settings: { insertByRule: true } } });
  const first = makeEnv({ server });
  boot(first);
  await sleep(30);
  first.els.inpToken.value = OWNER_TOKEN;
  first.els.btnLogin.fire('click');
  await sleep(30);
  assert.match(first.els.tbody.innerHTML, /来自深渊/);

  // 模拟「关掉页面再重新打开」：新环境、localStorage 可能还在，但 sessionStorage 一定是空的
  const second = makeEnv({ server, mirror: null });
  second.lsData['worklib.v1'] = '{"works":[{"name":"残留"}]}';     // 就算有残留
  boot(second);
  await sleep(30);
  assert.equal(second.els.loginCard.hidden, false, '新会话应该要求重新输口令');
  assert.equal(second.els.stat.textContent, '共 0 部作品');
});

console.log('[服务器模式] 只读（朋友）');

await t('guest 口令：界面没有任何写入口，UUID 相关功能全不可见', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server, storedToken: GUEST_TOKEN });
  boot(env);
  await sleep(30);

  assert.equal(env.els.roleBadge.textContent, '服务器 · 只读');
  assert.match(env.sandbox.document.body.className, /readonly/, 'body 没有 readonly 类');
  assert.equal(env.els.roNote.hidden, false, '没有只读提示');
  assert.match(env.els.tbody.innerHTML, /只读/, '行内操作应该只剩「只读」');
  assert.doesNotMatch(env.els.tbody.innerHTML, /data-act=/, '只读模式还有操作按钮');
  assert.doesNotMatch(env.els.tbody.innerHTML, /显示UUID/, '只读模式还能显示 UUID');
  assert.doesNotMatch(env.els.tbody.innerHTML, /xey/, '只读模式列表里出现了 UUID');

  // 写操作即使被程序调用也要拦住
  env.els.btnExportUuid.fire('click');
  assert.match(env.els.toast.textContent, /只读模式/, '导出 UUID 对应表没被拦住');
  assert.equal(env.cap.downloads.length, 0, '只读模式居然导出成功了');

  env.els.btnResort.fire('click');
  assert.match(env.els.toast.textContent, /只读模式/);

  env.els.inpName.value = '偷偷加一个';
  env.els.btnAdd.fire('click');
  await sleep(900);
  assert.equal(server.puts, 0, 'guest 竟然发出了 PUT');
  assert.equal(server.db.works.length, 1, '服务端数据被改了');
  assert.doesNotMatch(env.els.tbody.innerHTML, /偷偷加一个/);
});

await t('只读模式仍可搜索与导出作品列表（不含 UUID）', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1), work('葬送的芙莉莲', U2)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server, storedToken: GUEST_TOKEN });
  boot(env);
  await sleep(30);
  env.els.inpSearch.value = '芙莉莲';
  env.els.inpSearch.fire('input', { target: env.els.inpSearch });
  assert.equal((env.els.tbody.innerHTML.match(/<tr/g) || []).length, 1, '搜索没生效');
  env.els.btnExportList.fire('click');
  assert.equal(env.cap.downloads.length, 1, '只读模式不能导出作品列表？');
  assert.match(env.cap.downloads[0].content, /葬送的芙莉莲/);
  assert.doesNotMatch(env.cap.downloads[0].content, /xey/, '作品列表里漏出 UUID');
});

console.log('[服务器模式] 冲突与失败');

await t('409：别处改过 → 提示「已被别处修改」+ 一键重新载入服务器版本', async () => {
  const server = makeServer({ db: { works: [work('旧数据', U1)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server, storedToken: OWNER_TOKEN, confirmAnswers: [true] });
  boot(env);
  await sleep(30);

  // 模拟另一台设备改过了
  server.db = { works: [work('别处改过的数据', U1)], discarded: [], settings: { insertByRule: true } };
  server.revision = 7;

  env.els.inpName.value = '我加的作品';
  env.els.btnAdd.fire('click');
  await sleep(900);
  assert.match(env.els.saveState.textContent, /未保存/, '实际: ' + env.els.saveState.textContent);
  assert.equal(env.els.issueBox.style.display, 'block', '冲突提示没出来');
  assert.match(env.els.issueText.textContent, /已经被别处修改/);
  assert.equal(env.els.btnIssueAction.textContent, '重新载入服务器版本');
  assert.equal(server.db.works[0].name, '别处改过的数据', '本地改动竟然覆盖了服务端');

  // 一键重新载入
  env.els.btnIssueAction.fire('click');
  await sleep(30);
  assert.match(env.els.tbody.innerHTML, /别处改过的数据/);
  assert.doesNotMatch(env.els.tbody.innerHTML, /我加的作品/, '重新载入后还留着本地未上传的改动');
  assert.equal(env.els.issueBox.style.display, 'none', '问题框没收起');
  assert.match(env.els.saveState.textContent, /已连接服务器/);
});

await t('上传失败：改动不丢，可点「重试上传」', async () => {
  const server = makeServer();
  const env = makeEnv({ server, storedToken: OWNER_TOKEN });
  boot(env);
  await sleep(30);
  server.failNextPut = { status: 500, error: '服务器打了个嗝' };

  env.els.inpName.value = '待重试的作品';
  env.els.btnAdd.fire('click');
  await sleep(900);
  assert.equal(env.els.btnIssueAction.textContent, '重试上传', '没有重试入口');
  assert.equal(server.puts, 0);
  assert.match(env.els.tbody.innerHTML, /待重试的作品/, '本地改动应该还在页面上');

  env.els.btnIssueAction.fire('click');
  await sleep(30);
  assert.equal(server.puts, 1, '重试没有真的上传');
  assert.equal(server.db.works[0].name, '待重试的作品');
  assert.equal(env.els.issueBox.style.display, 'none', '重试成功后问题框应收起');
  assert.match(env.els.saveState.textContent, /已保存到服务器/);
});

await t('403：服务端说不能写 → 自动切成只读', async () => {
  const server = makeServer({ db: { works: [work('来自深渊', U1)], discarded: [], settings: { insertByRule: true } } });
  const env = makeEnv({ server, storedToken: OWNER_TOKEN });
  boot(env);
  await sleep(30);
  assert.equal(env.els.roleBadge.textContent, '服务器 · 可编辑');

  server.forceGuest = true;                     // 服务端中途把权限降了
  env.els.inpName.value = '写不进去的作品';
  env.els.btnAdd.fire('click');
  await sleep(900);
  assert.equal(env.els.roleBadge.textContent, '服务器 · 只读', '没有降级成只读');
  assert.match(env.sandbox.document.body.className, /readonly/);
  assert.match(env.els.banner.textContent, /只能看不能改/);
  assert.equal(env.els.saveState.textContent, '只读模式');
});

console.log('[服务器模式] 初始化与边界');

await t('服务器空库 + 本机有数据 → 问过才上传（点确定）', async () => {
  const server = makeServer();
  const mirror = { kind: 'worklib-db', works: [work('本机甲', U1), work('本机乙', U2)], discarded: [], settings: { insertByRule: true } };
  const env = makeEnv({ server, storedToken: OWNER_TOKEN, mirror, confirmAnswers: [true] });
  boot(env);
  await sleep(60);
  assert.equal(server.puts, 1, '没有把本机数据传上去');
  assert.equal(server.db.works.length, 2);
  assert.match(env.els.tbody.innerHTML, /本机甲/);
});

await t('服务器空库 + 本机有数据 → 点取消则用服务端空库（本机镜像不丢）', async () => {
  const server = makeServer();
  const mirror = { kind: 'worklib-db', works: [work('本机甲', U1)], discarded: [], settings: { insertByRule: true } };
  const env = makeEnv({ server, storedToken: OWNER_TOKEN, mirror, confirmAnswers: [false] });
  boot(env);
  await sleep(60);
  assert.equal(server.puts, 0, '点取消却上传了');
  assert.match(env.els.stat.textContent, /共 0 部作品/);
  assert.ok(env.lsData['worklib.v1'].includes('本机甲'), '答应过不动本机数据，结果被空库覆盖了');
  assert.match(env.els.banner.textContent, /仍保留在这个浏览器里/, '没有告诉用户本机数据还在、怎么找回');
});

await t('file:// 模式：完全不碰网络，行为与双击本地文件一致', async () => {
  const server = makeServer();
  const env = makeEnv({ server, protocol: 'file:' });
  boot(env);
  await sleep(30);
  assert.equal(server.calls.length, 0, 'file:// 模式竟然发了请求');
  assert.equal(env.els.roleBadge.textContent, '本地文件');
  assert.equal(env.els.saveState.textContent, '已就绪');
  assert.equal(env.els.loginCard.hidden, true, '本地模式不该要口令');
  env.els.inpName.value = '本地加一个';
  env.els.btnAdd.fire('click');
  await sleep(50);
  assert.match(env.els.stat.textContent, /共 1 部作品/);
  assert.ok(env.lsData['worklib.v1'].includes('本地加一个'), '本地模式没写 localStorage');
  assert.equal(server.calls.length, 0);
});

await t('「换口令」按钮：只在服务器模式出现，点了会清掉口令并刷新', async () => {
  const server = makeServer();
  const env = makeEnv({ server, storedToken: OWNER_TOKEN });
  boot(env);
  await sleep(30);
  assert.equal(env.els.btnForgetToken.hidden, false, '服务器模式该显示「换口令」');
  env.els.btnForgetToken.fire('click');
  assert.equal(env.ssData['worklib.token'], undefined, '口令没清掉');
  assert.equal(env.lsData['worklib.token'], undefined, 'localStorage 里也不该有口令');
  assert.equal(env.cap.reloads, 1, '没有刷新页面');

  const localEnv = makeEnv({ server, protocol: 'file:' });
  boot(localEnv);
  await sleep(20);
  assert.equal(localEnv.els.btnForgetToken.hidden, true, '本地模式不该显示「换口令」');
});

console.log('');
console.log(`服务器模式测试结果: ${pass} 通过, ${fail} 失败`);
if (fail) { failures.forEach(f => console.log('  - ' + f)); process.exit(1); }
