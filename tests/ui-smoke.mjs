// 界面层冒烟测试：不装浏览器，用最小 DOM 替身把 作品库.html 的两段脚本真跑起来，
// 再模拟点击走一遍「添加 → 重复 → 相似弹窗 → 导出 → 改名 → 删除」。
// 用法: node tests/ui-smoke.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', '作品库.html'), 'utf8');

const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.ok(scripts.length >= 2, '没抽到两段脚本');

/* ---------------- 最小 DOM 替身 ---------------- */
const cap = { downloads: [], lastBlob: null, confirmQueue: [], promptQueue: [], copied: [] };

function makeEl(id) {
  return {
    id, _listeners: {}, style: {}, className: '', textContent: '', innerHTML: '',
    value: '', checked: false, hidden: false, open: false,
    href: '', download: '', type: '',
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    removeEventListener() {},
    fire(type, ev) { (this._listeners[type] || []).forEach(fn => fn(ev || {})); },
    hasListener(type) { return !!this._listeners[type]; },
    focus() {}, blur() {},
    click() { this.fire('click', {}); if (this.download || this.href) cap.downloads.push({
      filename: this.download,
      content: cap.lastBlob ? cap.lastBlob.parts.join('') : '',
      mime: cap.lastBlob ? cap.lastBlob.type : ''
    }); },
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelector(sel) { return id === 'dlgSimilar' && /cand/.test(sel) ? { value: '0' } : null; },
    closest() { return null; },
    getAttribute() { return null; },
    setAttribute() {},
    select() {},
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
const sandbox = {
  console,
  document,
  localStorage: {
    getItem: k => (k in lsData ? lsData[k] : null),
    setItem: (k, v) => { lsData[k] = String(v); },
    removeItem: k => { delete lsData[k]; }
  },
  Blob: class { constructor(parts, opts) { this.parts = parts; this.type = (opts || {}).type; } },
  URL: { createObjectURL: b => { cap.lastBlob = b; return 'blob:fake'; }, revokeObjectURL: () => {} },
  FileReader: class {
    readAsText() {
      this.result = sandbox.__fileContent;
      if (this.onload) this.onload();
    }
  },
  navigator: { clipboard: { writeText: t => { cap.copied.push(t); return Promise.resolve(); } } },
  setTimeout, clearTimeout
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.window.confirm = () => (cap.confirmQueue.length ? cap.confirmQueue.shift() : false);
sandbox.window.prompt = () => (cap.promptQueue.length ? cap.promptQueue.shift() : null);
sandbox.window.alert = () => {};
vm.createContext(sandbox);

let pass = 0, fail = 0;
const failures = [];
function t(name, fn) {
  // 每条用例前清空预置回答，跑完要求全部被消费 —— 否则说明流程没走到预期分支
  cap.confirmQueue.length = 0;
  cap.promptQueue.length = 0;
  try {
    fn();
    const left = cap.confirmQueue.length + cap.promptQueue.length;
    if (left) throw new Error('有 ' + left + ' 个预置回答没被消费，说明没走到预期分支');
    pass++; console.log('  PASS  ' + name);
  } catch (e) { fail++; failures.push(name + ' -> ' + e.message); console.log('  FAIL  ' + name + ' -> ' + e.message); }
}
const clickAdd = (name, alias, tags) => {
  els.inpName.value = name || '';
  els.inpAlias.value = alias || '';
  els.inpTags.value = tags || '';
  els.btnAdd.fire('click');
};
const lastDownload = () => cap.downloads[cap.downloads.length - 1];

/* ---------------- 启动：真跑界面脚本 ---------------- */
console.log('[UI] 脚本启动');
t('两段脚本按顺序执行，不抛异常', () => {
  scripts.forEach((code, i) => vm.runInContext(code, sandbox, { filename: 'inline-' + i + '.js' }));
  assert.ok(sandbox.WorkLibCore, '核心段没有挂上 WorkLibCore');
});
t('启动后渲染出状态：0 部作品、已就绪', () => {
  assert.equal(els.stat.textContent, '共 0 部作品');
  assert.equal(els.saveState.textContent, '已就绪');
  assert.match(els.listHead.textContent, /拼音首字母正序/);
  assert.equal(els.emptyHint.style.display, 'block');
});
t('所有按钮/输入都接上了事件', () => {
  const need = { btnAdd: 'click', btnResort: 'click', btnExportJson: 'click', btnExportList: 'click',
                 btnExportUuid: 'click', btnPreviewList: 'click', btnClosePreview: 'click',
                 btnImportJson: 'click', dlgSame: 'click', dlgAlias: 'click', dlgNew: 'click',
                 dlgCancel: 'click', tbody: 'click', fileImport: 'change',
                 chkInsertByRule: 'change', inpName: 'keydown', inpAlias: 'keydown' };
  for (const [id, type] of Object.entries(need)) {
    assert.ok(els[id], '元素 ' + id + ' 没被脚本取到');
    assert.ok(els[id].hasListener(type), id + ' 没接 ' + type);
  }
});

console.log('[UI] 添加 / 重复 / 相似弹窗');
t('输入带书名号的名字 → 已添加，列表和存档都写进去了', () => {
  clickAdd('《葬送的芙莉莲》', 'Frieren / 葬送のフリーレン');
  assert.match(els.toast.textContent, /^已添加（序号 1）/, '实际提示: ' + els.toast.textContent);
  assert.match(els.tbody.innerHTML, /葬送的芙莉莲/);
  assert.doesNotMatch(els.tbody.innerHTML, /《/, '存进库的名字不该带书名号');
  assert.match(els.tbody.innerHTML, /Frieren \/ 葬送のフリーレン/);
  assert.equal(els.stat.textContent, '共 1 部作品');
  assert.match(lsData['worklib.v1'], /xey[0-9a-f-]+cmd/, '没写到本地存储');
  assert.equal(els.inpName.value, '', '添加成功后输入框应清空');
});
t('再输一遍同名 → 提示「重复（序号 X）」，不新增', () => {
  clickAdd('葬送的芙莉莲');
  assert.match(els.toast.textContent, /重复（序号 1）/, '实际提示: ' + els.toast.textContent);
  assert.equal(els.stat.textContent, '共 1 部作品');
});
t('输别名 → 判重复', () => {
  clickAdd('Frieren');
  assert.match(els.toast.textContent, /重复（序号 1）/);
  assert.match(els.toast.textContent, /别名/);
});
t('拼错一个字 → 弹候选弹窗，列出候选和相似度', () => {
  clickAdd('葬送芙莉莲');
  assert.equal(els.dlgSimilar.open, true, '弹窗没打开');
  assert.equal(els.dlgInput.textContent, '葬送芙莉莲');
  assert.match(els.dlgCands.innerHTML, /葬送的芙莉莲/);
  assert.match(els.dlgCands.innerHTML, /相似度 \d+%/);
  assert.equal(els.dlgCands.innerHTML.includes('checked'), true, '第一个候选应默认选中');
  assert.equal(els.stat.textContent, '共 1 部作品', '弹窗期间不该落库');
});
t('点「是同一作品」→ 弹窗关掉、判重复、不新增', () => {
  els.dlgSame.fire('click');
  assert.equal(els.dlgSimilar.open, false);
  assert.match(els.toast.textContent, /重复（序号 1）/);
  assert.equal(els.stat.textContent, '共 1 部作品');
});
t('再拼错一次，点「是新作品」→ 才真的新增', () => {
  clickAdd('葬送芙莉莲');
  assert.equal(els.dlgSimilar.open, true);
  els.dlgNew.fire('click');
  assert.match(els.toast.textContent, /^已添加（序号 2）/);
  assert.equal(els.stat.textContent, '共 2 部作品');
});
t('点「登记为别名」→ 别名写进那条作品，以后同写法直接判重复', () => {
  // 「藏送的芙莉莲」与已有的「葬送的芙莉莲」只差一个字 → 必定弹候选
  clickAdd('藏送的芙莉莲');
  assert.equal(els.dlgSimilar.open, true, '应该弹候选弹窗');
  assert.match(els.dlgCands.innerHTML, /葬送的芙莉莲/);
  const before = els.stat.textContent;
  els.dlgAlias.fire('click');
  assert.equal(els.dlgSimilar.open, false);
  assert.match(els.toast.textContent, /登记为序号 \d+（葬送的芙莉莲）的别名/, '实际提示: ' + els.toast.textContent);
  assert.equal(els.stat.textContent, before, '只是登记别名，不该新增作品');

  clickAdd('藏送的芙莉莲');           // 同写法再来一次
  assert.equal(els.dlgSimilar.open, false, '登记过别名就不该再弹候选');
  assert.match(els.toast.textContent, /重复（序号 \d+）/);
  assert.match(els.toast.textContent, /别名/);
});

console.log('[UI] 导出');
t('导出作品列表 CSV：有作品名，没有 UUID', () => {
  const n = cap.downloads.length;
  els.btnExportList.fire('click');
  assert.equal(cap.downloads.length, n + 1, '没有触发下载');
  const d = lastDownload();
  assert.match(d.filename, /^作品列表-\d{8}-\d{4}\.csv$/);
  assert.match(d.content, /序号,作品名,别名/);
  assert.match(d.content, /葬送的芙莉莲/);
  assert.doesNotMatch(d.content, /xey/, '作品列表里漏出 UUID 了');
  assert.equal(d.content.charCodeAt(0), 0xFEFF, 'CSV 少了 BOM，Excel 打开会乱码');
});
t('导出作品-UUID 对应表：UUID 在里面，且与库里一致', () => {
  els.btnExportUuid.fire('click');
  const d = lastDownload();
  assert.match(d.filename, /^作品-UUID对应表-\d{8}-\d{4}\.csv$/);
  assert.match(d.content, /序号,作品名,UUID/);
  const db = JSON.parse(lsData['worklib.v1']);
  db.works.forEach(w => {
    assert.ok(d.content.includes(w.uuid), '对应表缺少 ' + w.name + ' 的 UUID');
  });
});
t('「显示完整作品列表」切换预览区', () => {
  els.previewWrap.hidden = true;
  els.btnPreviewList.fire('click');
  assert.equal(els.previewWrap.hidden, false);
  assert.match(els.previewBody.innerHTML, /葬送的芙莉莲/);
  assert.match(els.previewTitle.textContent, /完整作品列表/);
  els.btnClosePreview.fire('click');
  assert.equal(els.previewWrap.hidden, true);
});
t('导出备份 JSON：含 works 与 uuid', () => {
  els.btnExportJson.fire('click');
  const d = lastDownload();
  assert.match(d.filename, /^作品库-备份-\d{8}-\d{4}\.json$/);
  const parsed = JSON.parse(d.content);
  assert.ok(Array.isArray(parsed.works));
  assert.ok(parsed.works.every(w => w.uuid && w.name));
});

console.log('[UI] 重排 / 改名 / 删除');
t('「按规则重排」要先确认；同意后顺序变化、UUID 与作品绑定不变', () => {
  // 先关掉「按规则插到位」，追加两条明显乱序的作品，制造出真正需要重排的局面
  els.chkInsertByRule.checked = false;
  els.chkInsertByRule.fire('change', { target: els.chkInsertByRule });
  clickAdd('阿凡达');                       // 拼音 a，规则序应在最前
  if (els.dlgSimilar.open) els.dlgNew.fire('click');
  const before = JSON.parse(lsData['worklib.v1']);
  const bindBefore = before.works.map(w => w.uuid + '=>' + w.name).sort().join('|');
  assert.notEqual(before.works[0].name, '阿凡达', '制造乱序失败：它已经在第一位了');

  cap.confirmQueue.push(false);
  els.btnResort.fire('click');
  assert.equal(JSON.parse(lsData['worklib.v1']).works[0].name, before.works[0].name, '拒绝后不该重排');
  assert.match(els.toast.textContent, /已取消，顺序没有改动/);

  cap.confirmQueue.push(true);
  els.btnResort.fire('click');
  assert.match(els.toast.textContent, /已按规则重排/);
  const after = JSON.parse(lsData['worklib.v1']);
  assert.equal(after.works[0].name, '阿凡达', '拼音 a 应排第一，实际是 ' + after.works[0].name);
  assert.equal(after.works.map(w => w.uuid + '=>' + w.name).sort().join('|'), bindBefore,
    '重排把 UUID 和作品拆散了');

  els.chkInsertByRule.checked = true;        // 恢复默认，别影响后面的用例
  els.chkInsertByRule.fire('change', { target: els.chkInsertByRule });
});
t('改名：位置和 UUID 都不动', () => {
  const before = JSON.parse(lsData['worklib.v1']).works;
  const target = before.findIndex(w => w.name === '阿凡达');
  const uuid = before[target].uuid;
  cap.promptQueue.push('阿凡达2');
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'rename' : String(target) }) } });
  const after = JSON.parse(lsData['worklib.v1']).works;
  assert.equal(after[target].name, '阿凡达2');
  assert.equal(after[target].uuid, uuid, '改名把 UUID 弄丢了');
  assert.match(els.toast.textContent, /已改名/);
});
t('改 UUID：手动填的那条会摊开给你核对、非 v7 会提示、格式错的不动', () => {
  const before = JSON.parse(lsData['worklib.v1']).works;
  const idx = before.findIndex(w => w.name === '葬送的芙莉莲');
  const inner = '0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0f';
  cap.promptQueue.push(inner);
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'edituuid' : String(idx) }) } });
  assert.equal(JSON.parse(lsData['worklib.v1']).works[idx].uuid, 'xey' + inner + 'cmd');
  assert.match(els.toast.textContent, /已改 UUID/);
  assert.match(els.tbody.innerHTML, new RegExp('xey' + inner + 'cmd'), '改完应该把这条摊开');

  cap.promptQueue.push('乱写的');
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'edituuid' : String(idx) }) } });
  assert.equal(JSON.parse(lsData['worklib.v1']).works[idx].uuid, 'xey' + inner + 'cmd', '格式不对时不该改动');
  assert.match(els.toast.textContent, /格式不对/);
});
t('导入备份 JSON：能读、能合并、带 BOM 也不怕、重复的不会进两遍', () => {
  const uuid = 'xey0192f0d4-9999-7aaa-8bbb-cccccccccccccmd';
  const payload = {
    settings: { insertByRule: true }, discarded: [],
    works: [{ uuid, name: '导入进来的作品', aliases: ['别名字'], note: '', addedAt: '2026-01-01T00:00:00.000Z' }]
  };
  const before = JSON.parse(lsData['worklib.v1']).works.length;

  // 故意加 BOM：模拟记事本「UTF-8 带 BOM」另存
  sandbox.__fileContent = '\uFEFF' + JSON.stringify(payload);
  cap.confirmQueue.push(true);
  els.fileImport.files = [{ name: '备份.json' }];
  els.fileImport.fire('change', { target: els.fileImport });

  assert.match(els.toast.textContent, /导入完成：新增 1 条/, '实际提示: ' + els.toast.textContent);
  const db = JSON.parse(lsData['worklib.v1']);
  assert.equal(db.works.length, before + 1);
  assert.ok(db.works.some(w => w.uuid === uuid && w.name === '导入进来的作品'));
  assert.equal(db.works.find(w => w.uuid === uuid).aliases.join('/'), '别名字');
  assert.match(els.tbody.innerHTML, /导入进来的作品/, '导入后列表没刷新');

  // 再导一次同一个文件：按 UUID 去重，不该翻倍
  cap.confirmQueue.push(true);
  els.fileImport.files = [{ name: '备份.json' }];
  els.fileImport.fire('change', { target: els.fileImport });
  assert.match(els.toast.textContent, /新增 0 条/);
  assert.equal(JSON.parse(lsData['worklib.v1']).works.length, before + 1);
});
t('导入坏文件：给提示而不是崩', () => {
  const before = lsData['worklib.v1'];
  sandbox.__fileContent = '这不是 JSON';
  els.fileImport.files = [{ name: '坏.json' }];
  els.fileImport.fire('change', { target: els.fileImport });
  assert.match(els.toast.textContent, /不是合法的 JSON/);
  assert.equal(lsData['worklib.v1'], before, '坏文件不该改动数据');
});
t('删除：要确认，UUID 进舍弃名单，列表里消失', () => {
  const before = JSON.parse(lsData['worklib.v1']);
  const idx = before.works.findIndex(w => w.name === '阿凡达2');
  const uuid = before.works[idx].uuid;
  cap.confirmQueue.push(false);
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'del' : String(idx) }) } });
  assert.equal(JSON.parse(lsData['worklib.v1']).works.length, before.works.length, '拒绝后不该删');

  cap.confirmQueue.push(true);
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'del' : String(idx) }) } });
  const after = JSON.parse(lsData['worklib.v1']);
  assert.equal(after.works.length, before.works.length - 1);
  assert.ok(after.discarded.some(d => d.uuid === uuid), 'UUID 没进舍弃名单');
  assert.doesNotMatch(els.tbody.innerHTML, /阿凡达2/);
  assert.match(els.toast.textContent, /已删除/);
});
t('刷新页面（重跑一遍脚本）数据还在', () => {
  // 真刷新 = 全新 DOM：清掉旧监听器再跑，避免双重绑定造成假象
  Object.keys(els).forEach(k => { els[k]._listeners = {}; });
  scripts.forEach((code, i) => vm.runInContext(code, sandbox, { filename: 'reload-' + i + '.js' }));
  assert.match(els.tbody.innerHTML, /葬送的芙莉莲/, '重新加载后列表是空的');
  assert.match(els.stat.textContent, /共 \d+ 部作品/);
});
t('勾选开关会写进设置并落盘', () => {
  els.chkInsertByRule.checked = false;
  els.chkInsertByRule.fire('change', { target: els.chkInsertByRule });
  assert.equal(JSON.parse(lsData['worklib.v1']).settings.insertByRule, false);
  assert.match(els.toast.textContent, /追加到末尾/);
});

console.log('[UI] 搜索 / 单条 UUID');
const searchFor = q => {
  els.inpSearch.value = q;
  els.inpSearch.fire('input', { target: els.inpSearch });
};
const shownRows = () => els.tbody.innerHTML.split('<tr').length - 1;

t('默认不显示任何 UUID；点「显示UUID」才摊开那一条，可复制、可隐藏', () => {
  searchFor('');
  assert.doesNotMatch(els.tbody.innerHTML, /xey/, '常规列表里不该出现 UUID');

  const db = JSON.parse(lsData['worklib.v1']);
  const idx = db.works.findIndex(w => w.name === '葬送的芙莉莲');
  const uuid = db.works[idx].uuid;

  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'uuid' : String(idx) }) } });
  assert.match(els.tbody.innerHTML, new RegExp(uuid), 'UUID 没摊开');
  assert.match(els.tbody.innerHTML, /隐藏UUID/, '按钮该变成「隐藏UUID」');
  assert.match(els.toast.textContent, /已显示《葬送的芙莉莲》的 UUID/);

  // 只摊开了这一条：其余作品仍然没有 UUID 露出来
  assert.equal((els.tbody.innerHTML.match(/<code>/g) || []).length, 1, '只该摊开一条');

  cap.copied.length = 0;
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'copy' : String(idx) }) } });
  assert.equal(cap.copied[0], uuid, '复制到的不是这条的 UUID');

  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'uuid' : String(idx) }) } });
  assert.doesNotMatch(els.tbody.innerHTML, /xey/, '隐藏后不该还剩 UUID');
  assert.match(els.toast.textContent, /已隐藏/);
});

t('搜索：按作品名过滤，序号仍是整库真实位置', () => {
  searchFor('葬送');
  assert.match(els.searchInfo.textContent, /找到 \d+ \/ \d+ 条/);
  assert.match(els.tbody.innerHTML, /葬送的芙莉莲/);
  assert.match(els.tbody.innerHTML, /葬送芙莉莲/);
  assert.equal(shownRows(), 2);

  // 过滤出来的行的序号必须等于它在整库里的真实位置
  const db = JSON.parse(lsData['worklib.v1']);
  const seqs = [...els.tbody.innerHTML.matchAll(/<td class="seq">(\d+)<\/td>/g)].map(m => Number(m[1]));
  const expected = db.works.map((w, i) => ({ i: i + 1, n: w.name }))
    .filter(x => x.n.includes('葬送')).map(x => x.i);
  assert.deepEqual(JSON.stringify(seqs), JSON.stringify(expected), '序号被过滤结果重新编号了');
});

t('搜索：别名也能搜到', () => {
  searchFor('Frieren');
  assert.equal(shownRows(), 1);
  assert.match(els.tbody.innerHTML, /葬送的芙莉莲/, '别名命中应该显示它所属的作品');
});

t('搜索：UUID 全文 / 中间一截 / 去连字符 / 大小写都能搜到', () => {
  const db = JSON.parse(lsData['worklib.v1']);
  const w = db.works.find(x => x.name === '葬送的芙莉莲');
  const cases = [w.uuid, w.uuid.slice(3, 15), w.uuid.replace(/-/g, ''), w.uuid.toUpperCase(), w.uuid.slice(0, -3)];
  for (const q of cases) {
    searchFor(q);
    assert.equal(shownRows(), 1, '搜「' + q + '」应该只有一条');
    assert.match(els.tbody.innerHTML, /葬送的芙莉莲/, '搜「' + q + '」命中的作品不对');
  }
});

t('搜索：搜不到时给明确提示，清空后恢复全部', () => {
  searchFor('压根不存在的作品zzz');
  assert.equal(shownRows(), 0);
  assert.match(els.emptyHint.textContent, /没有匹配/);
  assert.equal(els.emptyHint.style.display, 'block');

  els.btnClearSearch.fire('click');
  assert.equal(els.inpSearch.value, '');
  assert.equal(els.searchInfo.textContent, '');
  const db = JSON.parse(lsData['worklib.v1']);
  assert.equal(shownRows(), db.works.length, '清空后应该显示全部');
  assert.equal(els.emptyHint.style.display, 'none');
});

t('搜索状态下操作打在正确的那一条上（不是过滤后的行号）', () => {
  // 只显示出「葬送芙莉莲」，但它在整库里的位置不是 1
  const db = JSON.parse(lsData['worklib.v1']);
  const realIdx = db.works.findIndex(w => w.name === '葬送芙莉莲');
  assert.notEqual(realIdx, 0, '这条本来就不在第一位，测试才有意义');

  searchFor('葬送芙莉莲');
  const rowIdx = els.tbody.innerHTML.match(/data-act="rename" data-i="(\d+)"/)[1];
  assert.equal(Number(rowIdx), realIdx, '过滤后的操作绑定到了错误的下标');

  cap.promptQueue.push('葬送芙莉莲 第二季');
  els.tbody.fire('click', { target: { closest: () => ({ getAttribute: a => a === 'data-act' ? 'rename' : rowIdx }) } });
  const after = JSON.parse(lsData['worklib.v1']);
  assert.equal(after.works[realIdx].name, '葬送芙莉莲 第二季', '改名打到了别的作品上');
  assert.equal(after.works.length, db.works.length, '不该多出或少掉作品');
  assert.match(els.tbody.innerHTML, /葬送芙莉莲 第二季/, '改名后列表没跟着刷新');
  assert.equal(shownRows(), 1, '现在只剩这条还匹配查询');
  els.btnClearSearch.fire('click');
});

t('搜索时新增的作品如果不在结果里，会明确提醒', () => {
  searchFor('葬送');
  const before = JSON.parse(lsData['worklib.v1']).works.length;
  clickAdd('不沾边的作品名');           // 不匹配「葬送」
  if (els.dlgSimilar.open) els.dlgNew.fire('click');
  assert.match(els.toast.textContent, /^已添加（序号 \d+）/);
  assert.match(els.toast.textContent, /不在当前筛选结果里/, '实际提示: ' + els.toast.textContent);
  assert.equal(JSON.parse(lsData['worklib.v1']).works.length, before + 1, '作品本身要加进去');
  assert.doesNotMatch(els.tbody.innerHTML, /不沾边的作品名/, '它不该出现在过滤结果里');
  els.btnClearSearch.fire('click');
  assert.match(els.tbody.innerHTML, /不沾边的作品名/, '清空搜索后应该看得到');
});

console.log('[UI] 标签（分类）');

t('添加时就能带标签，列表里显示成小标签', () => {
  els.btnClearSearch.fire('click');
  clickAdd('带标签的作品', '某别名', '奇幻/搞笑');
  if (els.dlgSimilar.open) els.dlgNew.fire('click');
  assert.match(els.toast.textContent, /标签 奇幻\/搞笑/, '实际提示: ' + els.toast.textContent);
  assert.match(els.tbody.innerHTML, /data-tag="奇幻"/, '列表里没渲染标签');
  assert.match(els.tbody.innerHTML, /data-tag="搞笑"/);
  const db = JSON.parse(lsData['worklib.v1']);
  const w = db.works.find(x => x.name === '带标签的作品');
  assert.equal(w.tags.join('/'), '奇幻/搞笑');
  assert.equal(els.inpTags.value, '', '添加后标签输入框应清空');
});

t('标签栏显示每个标签的条数；点一个就筛选', () => {
  assert.match(els.tagBar.innerHTML, /标签筛选/, '标签栏没出来');
  assert.match(els.tagBar.innerHTML, /奇幻<span class="n">1<\/span>/, '条数不对：' + els.tagBar.innerHTML);
  // 点列表里那行的「奇幻」标签 → 只剩这一条
  els.tbody.fire('click', { target: { closest: sel => /chip/.test(sel) ? { getAttribute: () => '奇幻' } : null } });
  assert.equal((els.tbody.innerHTML.match(/<tr/g) || []).length, 1, '筛选没生效');
  assert.match(els.tbody.innerHTML, /带标签的作品/);
  assert.match(els.tagBar.innerHTML, /btnClearTagFilter/, '没有「清空筛选」');
  // 清空
  els.tagBar.fire('click', { target: { closest: sel => /btnClearTagFilter/.test(sel) ? { getAttribute: () => null } : null } });
  const n = JSON.parse(lsData['worklib.v1']).works.length;
  assert.equal((els.tbody.innerHTML.match(/<tr/g) || []).length, n, '清空后应恢复全部');
});

t('标签对话框：预置 + 用过的 + 自定义，能选能存能取消', () => {
  const db = JSON.parse(lsData['worklib.v1']);
  const idx = db.works.findIndex(w => w.name === '带标签的作品');
  // 打开
  els.tbody.fire('click', { target: { closest: sel => /button/.test(sel) ? { getAttribute: a => a === 'data-act' ? 'tags' : String(idx) } : null } });
  assert.equal(els.dlgTags.open, true, '对话框没打开');
  assert.match(els.dlgTagCands.innerHTML, /恋爱/, '没给预置标签');
  assert.match(els.dlgTagCands.innerHTML, /奇幻/, '没给已用过的标签');
  assert.match(els.dlgTagPicked.innerHTML, /奇幻/);
  // 去掉「奇幻」、加上「恋爱」
  els.dlgTagCands.fire('click', { target: { closest: sel => /chip/.test(sel) ? { getAttribute: () => '奇幻' } : null } });
  els.dlgTagCands.fire('click', { target: { closest: sel => /chip/.test(sel) ? { getAttribute: () => '恋爱' } : null } });
  // 自定义一个
  els.inpNewTag.value = '自创标签';
  els.btnAddTag.fire('click');
  assert.match(els.dlgTagPicked.innerHTML, /自创标签/);
  els.dlgTagSave.fire('click');
  assert.equal(els.dlgTags.open, false, '保存后应关闭');
  const after = JSON.parse(lsData['worklib.v1']).works[idx];
  assert.equal(after.tags.join('/'), '搞笑/恋爱/自创标签', '实际: ' + after.tags.join('/'));
  assert.match(els.toast.textContent, /已更新标签/);

  // 取消不生效
  els.tbody.fire('click', { target: { closest: sel => /button/.test(sel) ? { getAttribute: a => a === 'data-act' ? 'tags' : String(idx) } : null } });
  els.dlgTagCands.fire('click', { target: { closest: sel => /chip/.test(sel) ? { getAttribute: () => '恐怖' } : null } });
  els.dlgTagCancel.fire('click');
  assert.equal(JSON.parse(lsData['worklib.v1']).works[idx].tags.join('/'), '搞笑/恋爱/自创标签', '取消却改了数据');
});

t('标签进导出：作品列表 CSV 有「标签」列，且标签也能被搜索', () => {
  els.btnExportList.fire('click');
  const csv = lastDownload().content;
  assert.match(csv, /序号,作品名,别名,标签/, '表头缺标签列');
  assert.match(csv, /搞笑 \/ 恋爱 \/ 自创标签/, '标签没写进 CSV');
  assert.doesNotMatch(csv, /xey/, '作品列表里漏出 UUID');
  searchFor('恋爱');                       // 搜索也认标签
  assert.equal((els.tbody.innerHTML.match(/<tr/g) || []).length, 1, '搜标签没命中');
  assert.match(els.tbody.innerHTML, /带标签的作品/);
  els.btnClearSearch.fire('click');
});

console.log('');
console.log(`UI 冒烟结果: ${pass} 通过, ${fail} 失败`);
if (fail) { failures.forEach(f => console.log('  - ' + f)); process.exit(1); }
