// 从 作品库.html 中抽出 <script id="core-logic"> 的纯逻辑段，在 Node 里执行后做断言。
// 用法: node tests/run-tests.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const here = dirname(fileURLToPath(import.meta.url));
const htmlPath = join(here, '..', '作品库.html');
const html = readFileSync(htmlPath, 'utf8');

function extractCore(src) {
  const start = src.indexOf('/* CORE-LOGIC-START');
  const end = src.indexOf('/* CORE-LOGIC-END */');
  assert.ok(start >= 0 && end > start, '在 HTML 里找不到 CORE-LOGIC 标记段');
  return src.slice(start, end);
}

const sandbox = { console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(extractCore(html), sandbox, { filename: 'core-logic.js' });
const C = sandbox.WorkLibCore;
assert.ok(C, 'core-logic 段没有导出 WorkLibCore');

let pass = 0, fail = 0;
const failures = [];
function t(name, fn) {
  try { fn(); pass++; console.log('  PASS  ' + name); }
  catch (e) { fail++; failures.push(name + ' -> ' + e.message); console.log('  FAIL  ' + name + ' -> ' + e.message); }
}

console.log('[1] UUID 生成');
const UUID_V7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

t('uuidv7 符合 v7 规范（版本位 7、变体位 10）', () => {
  for (let i = 0; i < 200; i++) assert.match(C.uuidv7(), UUID_V7);
});

t('作品 UUID 形如 xey + v7 + cmd', () => {
  for (let i = 0; i < 50; i++) {
    const u = C.makeWorkUuid();
    assert.ok(u.startsWith('xey'), '前缀不是 xey: ' + u);
    assert.ok(u.endsWith('cmd'), '后缀不是 cmd: ' + u);
    const inner = u.slice(3, -3);
    assert.match(inner, UUID_V7, '中间不是合法 UUID v7: ' + u);
  }
});

t('5000 个 UUID 全局唯一', () => {
  const set = new Set();
  for (let i = 0; i < 5000; i++) set.add(C.makeWorkUuid());
  assert.equal(set.size, 5000, '出现碰撞');
});

t('时间戳单调：后生成的 UUID 时间位不小于先生成的', () => {
  const a = C.makeWorkUuid(), b = C.makeWorkUuid();
  const ta = a.slice(3, 15), tb = b.slice(3, 15); // 前 12 位 hex = 48 位毫秒
  assert.ok(tb >= ta, `时间位倒挂: ${ta} > ${tb}`);
});

console.log('[1] 名称归一化与精确查重');
t('归一化去掉书名号、首尾空白、压缩连续空白', () => {
  assert.equal(C.normalizeName('《葬送的芙莉莲》'), '葬送的芙莉莲');
  assert.equal(C.normalizeName('  作品  '), '作品');
  assert.equal(C.normalizeName('  作品   名  '), '作品 名');
  assert.equal(C.normalizeName('「作品」'), '作品');
  assert.equal(C.normalizeName(null), '');
});

t('带不带书名号都判为重复，返回序号下标', () => {
  const works = [{ name: '葬送的芙莉莲' }, { name: '来自深渊' }];
  assert.equal(C.findDuplicateIndex(works, '葬送的芙莉莲'), 0);
  assert.equal(C.findDuplicateIndex(works, '《葬送的芙莉莲》'), 0);
  assert.equal(C.findDuplicateIndex(works, '  葬送的芙莉莲 '), 0);
  assert.equal(C.findDuplicateIndex(works, '来自深渊'), 1);
  assert.equal(C.findDuplicateIndex(works, '来自深淵'), -1); // 繁体差异留给第 2 步
  assert.equal(C.findDuplicateIndex(works, '没听过的作品'), -1);
  assert.equal(C.findDuplicateIndex(works, '《》'), -1, '空名不应匹配到任何作品');
});

console.log('[2] 归一化：全半角 / 大小写 / 空白 / 标点');
t('书名号、空白差异视为同一部', () => {
  assert.equal(C.compareKey('《葬送的芙莉莲》'), C.compareKey('葬送的 芙莉莲'));
  assert.equal(C.compareKey('  来自深渊 '), C.compareKey('来自深渊'));
});
t('全角字母数字、全角斜杠、表意空格都等价', () => {
  assert.equal(C.compareKey('Ｆａｔｅ／ｓｔａｙ　ｎｉｇｈｔ'), C.compareKey('fate stay night'));
  assert.equal(C.compareKey('ｖｏｌ．１'), C.compareKey('vol.1'));
});
t('大小写不敏感', () => {
  assert.equal(C.compareKey('STEINS;GATE'), C.compareKey('steins gate'));
  assert.equal(C.compareKey('Cowboy Bebop'), C.compareKey('cowboybebop'));
});
t('标点差异不产生新作品（Re:Zero / ReZero）', () => {
  assert.equal(C.compareKey('Re:Zero'), C.compareKey('ReZero'));
  assert.equal(C.compareKey('钢之炼金术师（FA）'), C.compareKey('钢之炼金术师 FA'));
});
t('空输入比出来还是空', () => {
  assert.equal(C.compareKey(''), '');
  assert.equal(C.compareKey('《》'), '');
  assert.equal(C.compareKey(null), '');
});
t('显示名不被改写（不自动改名称、不转全半角、不转大小写）', () => {
  assert.equal(C.normalizeName('Ｆａｔｅ／ｓｔａｙ night'), 'Ｆａｔｅ／ｓｔａｙ night');
  assert.equal(C.normalizeName('STEINS;GATE'), 'STEINS;GATE');
  assert.equal(C.normalizeName('Re:Zero'), 'Re:Zero');
});

console.log('[2] 查重：别名承接翻译差异');
const lib = () => ([
  { name: '葬送的芙莉莲', aliases: ['葬送のフリーレン', 'Frieren'] },
  { name: '来自深渊', aliases: ['メイドインアビス'] }
]);
t('正式名命中', () => {
  assert.equal(C.findDuplicate(lib(), '《葬送的芙莉莲》').index, 0);
  assert.equal(C.findDuplicate(lib(), '葬送的芙莉莲').via, 'name');
});
t('别名命中即判为同一部（翻译差异 / 原名）', () => {
  const h1 = C.findDuplicate(lib(), '葬送のフリーレン');
  assert.equal(h1.index, 0);
  assert.equal(h1.via, 'alias');
  assert.equal(h1.text, '葬送のフリーレン');
  assert.equal(C.findDuplicate(lib(), 'ＦＲＩＥＲＥＮ').index, 0);
  assert.equal(C.findDuplicate(lib(), 'メイドインアビス').index, 1);
});
t('没登记过的译名不会被擅自合并', () => {
  assert.equal(C.findDuplicate(lib(), '葬送'), null);
  assert.equal(C.findDuplicate(lib(), '深渊'), null);
});

console.log('[2] 别名清理');
// 跨 vm 边界的数组原型不同，用 JSON 比对
const sameJSON = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b));
t('分隔符支持 / 、 , ; | 等', () => {
  sameJSON(C.splitAliasInput('a / b、c，d;e|f'), ['a ', ' b', 'c', 'd', 'e', 'f']);
});
t('清理掉空项、与正式名相同的项、彼此重复的项，并保持顺序', () => {
  sameJSON(
    C.cleanAliases([' Frieren ', '', '葬送的芙莉莲', 'frieren', '葬送のフリーレン'], '葬送的芙莉莲'),
    ['Frieren', '葬送のフリーレン']
  );
  sameJSON(C.cleanAliases(['《别名》'], '正式名'), ['别名']);
});

console.log('[3] 排序规则：中文按拼音 → 其他语言按字母');
const names = w => w.map(x => x.name).join(' | ');

t('中文按拼音首字母正序', () => {
  const zh = ['张同学', '葬送的芙莉莲', '阿凡达', '白箱', '来自深渊'].map(n => ({ name: n }));
  assert.equal(names(C.sortWorksByRule(zh)), '阿凡达 | 白箱 | 来自深渊 | 葬送的芙莉莲 | 张同学');
});
t('归类判定：含假名 → 其他语言；纯汉字 → 中文；空名排最后', () => {
  assert.equal(C.groupOf('葬送的芙莉莲'), 0);
  assert.equal(C.groupOf('86-不存在的战区'), 0, '含汉字即归中文组');
  assert.equal(C.groupOf('葬送のフリーレン'), 1, '有假名归其他语言');
  assert.equal(C.groupOf('メイドインアビス'), 1);
  assert.equal(C.groupOf('Re:Zero'), 1);
  assert.equal(C.groupOf('《》'), 2);
});
t('其他语言整体排在中文之后，内部按原名字母序', () => {
  const mixed = ['张同学', 'Frieren', '葬送的芙莉莲', '阿凡达', 'メイドインアビス',
                 '来自深渊', 'Cowboy Bebop', '白箱'].map(n => ({ name: n }));
  assert.equal(names(C.sortWorksByRule(mixed)),
    '阿凡达 | 白箱 | 来自深渊 | 葬送的芙莉莲 | 张同学 | Cowboy Bebop | Frieren | メイドインアビス');
});
t('作品名里的数字按数值比（Vol.2 在 Vol.10 之前）', () => {
  const v = ['Vol.10', 'Vol.2'].map(n => ({ name: n }));
  assert.equal(names(C.sortWorksByRule(v)), 'Vol.2 | Vol.10');
});
t('重排不改动原数组', () => {
  const src = [{ name: '张' }, { name: '阿' }];
  const out = C.sortWorksByRule(src);
  assert.equal(names(src), '张 | 阿');
  assert.equal(names(out), '阿 | 张');
});
t('同一个库排两次结果一致（顺序确定、可复现）', () => {
  const many = ['张', '阿', 'Re:Zero', '葬送のフリーレン', 'B', 'a', 'メイド'].map(n => ({ name: n }));
  assert.equal(names(C.sortWorksByRule(many)), names(C.sortWorksByRule(C.sortWorksByRule(many))));
});
t('按规则插入位置正确，且不动其他作品', () => {
  const cur = ['阿凡达', '白箱', '来自深渊'].map(n => ({ name: n }));
  assert.equal(C.insertionIndexByRule(cur, '不良少女'), 2);
  assert.equal(C.insertionIndexByRule(cur, 'AAA'), 3, '其他语言组排在中文组之后，故插到末尾');
  assert.equal(C.insertionIndexByRule(cur, '最后之作'), 3);
  assert.equal(names(cur), '阿凡达 | 白箱 | 来自深渊', '插入位置计算不应改动现有顺序');
});

console.log('[4] 相似提示');
t('编辑距离：中英文都对', () => {
  assert.equal(C.levenshtein('', ''), 0);
  assert.equal(C.levenshtein('kitten', 'sitting'), 3);
  assert.equal(C.levenshtein('葬送的芙莉莲', '葬送的芙莉莲'), 0);
  assert.equal(C.levenshtein('葬送的芙莉莲', '葬送芙莉莲'), 1);
  assert.equal(C.levenshtein('Frieren', 'Frierenn'), 1);
});
const simLib = () => ([
  { name: '葬送的芙莉莲', aliases: ['葬送のフリーレン', 'Frieren'] },
  { name: '来自深渊', aliases: ['メイドインアビス'] },
  { name: 'Re:Zero', aliases: ['从零开始的异世界生活'] }
]);
t('打错一个字会给出候选', () => {
  const cands = C.findSimilar(simLib(), '葬送芙莉莲');
  assert.equal(cands.length, 1);
  assert.equal(cands[0].index, 0);
  assert.ok(cands[0].score > 0.8, '相似度应偏高: ' + cands[0].score);
});
t('英文拼写错误命中别名', () => {
  const cands = C.findSimilar(simLib(), 'Frierenn');
  assert.equal(cands.length, 1);
  assert.equal(cands[0].index, 0);
  assert.equal(cands[0].via, 'alias');
  assert.equal(cands[0].text, 'Frieren');
});
t('只写了主标题（少了副标题/前缀）也能提示', () => {
  const cands = C.findSimilar(simLib(), '芙莉莲');
  assert.equal(cands.length, 1);
  assert.equal(cands[0].index, 0);
  assert.ok(cands[0].score >= 0.85, '包含关系应给加成: ' + cands[0].score);
});
t('不像的名字不提示（不制造噪音）', () => {
  assert.equal(C.findSimilar(simLib(), '进击的巨人').length, 0);
  assert.equal(C.findSimilar(simLib(), 'Cowboy Bebop').length, 0);
});
t('完全同名不算「相似」，交给查重路径处理', () => {
  assert.equal(C.findSimilar(simLib(), '来自深渊').length, 0);
  assert.equal(C.findSimilar(simLib(), 'メイドインアビス').length, 0);
});
t('太短的名字不做猜测', () => {
  assert.ok('钢炼'.length < C.SIMILAR_MIN_LEN);
  assert.equal(C.findSimilar(simLib(), '钢炼').length, 0);
});
t('候选按相似度从高到低，最多 5 条', () => {
  const many = [];
  for (let i = 0; i < 10; i++) many.push({ name: '相似作品' + i });
  const cands = C.findSimilar(many, '相似作品');
  assert.ok(cands.length <= 5, '超过 5 条: ' + cands.length);
  for (let i = 1; i < cands.length; i++) assert.ok(cands[i - 1].score >= cands[i].score, '没有按相似度排序');
});
t('findSimilar 不改动传进来的数组', () => {
  const lib2 = simLib();
  const snapshot = JSON.stringify(lib2);
  C.findSimilar(lib2, '葬送芙莉莲');
  assert.equal(JSON.stringify(lib2), snapshot);
});

console.log('[5] 导出：作品列表 与 作品-UUID 对应表');
const expLib = () => ([
  { uuid: 'xey0192-aaaa-7bbb-8ccc-ddddddddddddcmd', name: '葬送的芙莉莲', aliases: ['Frieren', '葬送のフリーレン'] },
  { uuid: 'xey0193-eeee-7fff-8aaa-bbbbbbbbbbbbcmd', name: 'Re:Zero, 第一季', aliases: [] }
]);
t('作品列表：序号连续，且完全不含 UUID', () => {
  const rows = C.listRows(expLib());
  assert.equal(rows.map(r => r.seq).join(','), '1,2');
  const dump = JSON.stringify(rows);
  assert.ok(!/uuid/i.test(dump), '列表行里出现了 uuid 字段');
  assert.ok(!dump.includes('xey'), '列表行里带出了 UUID 内容');
  assert.equal(rows[0].aliases, 'Frieren / 葬送のフリーレン');
});
t('对应表：序号/作品名/UUID 一一对上', () => {
  const src = expLib();
  const rows = C.uuidRows(src);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].uuid, src[0].uuid);
  assert.equal(rows[1].name, 'Re:Zero, 第一季');
  assert.ok(rows.every(r => r.uuid.startsWith('xey') && r.uuid.endsWith('cmd')));
});
t('CSV：表头、CRLF、逗号与引号转义', () => {
  const csv = C.toCSV(['序号', '作品名'], [[1, 'Re:Zero, 第一季'], [2, '他说"你好"']]);
  const lines = csv.split('\r\n');
  assert.equal(lines[0], '序号,作品名');
  assert.equal(lines[1], '1,"Re:Zero, 第一季"');
  assert.equal(lines[2], '2,"他说""你好"""');
});
t('导出的作品列表 CSV 里没有 UUID', () => {
  const rows = C.listRows(expLib());
  const csv = C.toCSV(['序号', '作品名', '别名'], rows.map(r => [r.seq, r.name, r.aliases]));
  assert.ok(!csv.includes('xey'), 'CSV 里漏出了 UUID');
});

console.log('[6] 端到端流程（界面调用的就是这几个函数）');
const newDb = () => ({ settings: { insertByRule: true }, works: [], discarded: [] });
const nameList = db => db.works.map(w => w.name).join(' | ');
const bindMap = db => db.works.map(w => w.uuid + '=>' + w.name).sort().join('\n');

t('从空库录入一批作品：归一化、别名、按规则插位', () => {
  const db = newDb();
  let r = C.prepareAdd(db, '《葬送的芙莉莲》', '葬送のフリーレン / Frieren');
  assert.equal(r.status, 'added');
  assert.equal(db.works[0].name, '葬送的芙莉莲', '书名号应被去掉');
  assert.equal(db.works[0].aliases.join('/'), '葬送のフリーレン/Frieren');
  assert.match(db.works[0].uuid, /^xey[0-9a-f-]{36}cmd$/);

  assert.equal(C.prepareAdd(db, '来自深渊', 'メイドインアビス').status, 'added');
  assert.equal(db.works[0].name, '来自深渊', '「来」应插到「葬」之前');
  assert.equal(C.prepareAdd(db, 'Cowboy Bebop').status, 'added');
  assert.equal(C.prepareAdd(db, 'Re:Zero').status, 'added');
  assert.equal(nameList(db), '来自深渊 | 葬送的芙莉莲 | Cowboy Bebop | Re:Zero',
    '中文在前按拼音，其他语言在后按字母');
  assert.equal(new Set(db.works.map(w => w.uuid)).size, db.works.length, 'UUID 有重复');
});

t('重复判定：同名、带书名号、别名、全半角都拦得住', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲', '葬送のフリーレン / Frieren');
  const cases = [
    ['《葬送的芙莉莲》', 'name'],
    ['葬送的芙莉莲', 'name'],
    ['葬送のフリーレン', 'alias'],
    ['ＦＲＩＥＲＥＮ', 'alias'],
  ];
  for (const [input, via] of cases) {
    const r = C.prepareAdd(db, input);
    assert.equal(r.status, 'duplicate', input + ' 应该判重复');
    assert.equal(r.index, 0);
    assert.equal(r.via, via, input + ' 命中方式不对');
  }
  assert.equal(db.works.length, 1, '重复时不该落库');
});

t('拼错一个字：弹候选，选「同一作品」不落库', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲', '葬送のフリーレン');
  const r = C.prepareAdd(db, '葬送芙莉莲');
  assert.equal(r.status, 'need-similar');
  assert.equal(r.candidates[0].index, 0);
  const res = C.resolveSimilar(db, r, 'same', 0);
  assert.equal(res.status, 'duplicate');
  assert.equal(res.index, 0);
  assert.equal(db.works.length, 1);
});

t('拼错但其实是译名：选「登记为别名」后，同写法以后直接判重复', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲');
  let r = C.prepareAdd(db, '葬送芙莉莲');
  assert.equal(r.status, 'need-similar');
  const res = C.resolveSimilar(db, r, 'alias', 0);
  assert.equal(res.status, 'duplicate');
  assert.equal(res.aliasAdded, true);
  assert.equal(db.works[0].aliases.join('/'), '葬送芙莉莲');
  assert.equal(db.works.length, 1);
  const again = C.prepareAdd(db, '葬送芙莉莲');
  assert.equal(again.status, 'duplicate');
  assert.equal(again.via, 'alias');
});

t('拼错但确实是新作品：选「是新作品」才落库', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲');
  const r = C.prepareAdd(db, '葬送芙莉莲');
  const res = C.resolveSimilar(db, r, 'new', 0);
  assert.equal(res.status, 'added');
  assert.equal(db.works.length, 2);
});

t('候选弹窗点「取消」：一个字都不写', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲');
  const r = C.prepareAdd(db, '葬送芙莉莲');
  const res = C.resolveSimilar(db, r, 'cancel', 0);
  assert.equal(res.status, 'canceled');
  assert.equal(db.works.length, 1);
  assert.equal(db.works[0].aliases.length, 0);
});

t('别名撞别人：拒绝则整条不添加；同意则丢弃冲突别名', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲', '葬送のフリーレン');
  const r1 = C.prepareAdd(db, '来自深渊', '葬送のフリーレン', { onAliasConflict: () => false });
  assert.equal(r1.status, 'canceled');
  assert.equal(db.works.length, 1, '拒绝后不该新增');
  const r2 = C.prepareAdd(db, '来自深渊', '葬送のフリーレン', { onAliasConflict: () => true });
  assert.equal(r2.status, 'added');
  assert.equal(db.works.find(w => w.name === '来自深渊').aliases.length, 0, '冲突别名应被丢弃');
});

t('改别名：撞别人时要用户裁决', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲');
  C.prepareAdd(db, '来自深渊', '阿比斯别名');
  assert.equal(nameList(db), '来自深渊 | 葬送的芙莉莲');
  const r1 = C.setAliases(db, 1, '阿比斯别名', { onAliasConflict: () => false });
  assert.equal(r1.status, 'canceled');
  assert.equal(db.works[1].aliases.length, 0);
  const r2 = C.setAliases(db, 1, '阿比斯别名 / 芙莉莲别名', { onAliasConflict: () => true });
  assert.equal(r2.status, 'updated');
  assert.equal(db.works[1].aliases.join('/'), '芙莉莲别名');
});

t('改名：位置不动、UUID 不变、撞名被拒', () => {
  const db = newDb();
  C.prepareAdd(db, '来自深渊');
  C.prepareAdd(db, '葬送的芙莉莲');
  assert.equal(nameList(db), '来自深渊 | 葬送的芙莉莲');
  const uuid = db.works[1].uuid;
  const bad = C.renameWork(db, 1, '来自深渊');
  assert.equal(bad.status, 'duplicate');
  assert.equal(db.works[1].name, '葬送的芙莉莲', '撞名时不该改动');
  const ok = C.renameWork(db, 1, '钢之炼金术师');
  assert.equal(ok.status, 'renamed');
  assert.equal(db.works[1].name, '钢之炼金术师');
  assert.equal(db.works[1].uuid, uuid, 'UUID 必须跟着作品');
  assert.equal(nameList(db), '来自深渊 | 钢之炼金术师', '改名不自动移动位置');
});

t('按规则重排：顺序变、UUID 与作品的绑定不变', () => {
  const db = newDb();
  C.prepareAdd(db, '张同学');
  C.prepareAdd(db, 'Re:Zero');
  C.prepareAdd(db, '阿凡达');
  C.prepareAdd(db, 'メイドインアビス');
  C.prepareAdd(db, '白箱');
  const before = bindMap(db);
  db.works = C.sortWorksByRule(db.works);
  assert.equal(nameList(db), '阿凡达 | 白箱 | 张同学 | Re:Zero | メイドインアビス');
  assert.equal(bindMap(db), before, '重排把 UUID 和作品拆散了');
  const seqs = db.works.map((w, i) => i + 1);
  assert.equal(seqs.join(','), '1,2,3,4,5', '序号应连续');
});

t('手动改 UUID：补前后缀、查唯一、拒绝回流、非 v7 只警告', () => {
  const db = newDb();
  C.prepareAdd(db, '来自深渊');
  C.prepareAdd(db, '葬送的芙莉莲');
  const inner = '0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0f';

  assert.equal(C.setUuid(db, 0, inner).uuid, 'xey' + inner + 'cmd', '应自动补前后缀');
  assert.equal(db.works[0].uuid, 'xey' + inner + 'cmd');
  assert.equal(C.setUuid(db, 0, 'xey' + inner.toUpperCase() + 'CMD').status, 'unchanged', '大小写应等价');
  assert.equal(C.setUuid(db, 0, '乱写的').status, 'bad-format');
  assert.equal(C.setUuid(db, 0, '').status, 'empty');
  assert.equal(C.setUuid(db, 0, db.works[1].uuid).status, 'duplicate', '撞别人的 UUID 要拦');
  assert.equal(db.works[0].uuid, 'xey' + inner + 'cmd', '被拒时不该改动');

  const gone = C.deleteWork(db, 1);
  assert.equal(C.setUuid(db, 0, gone.uuid).status, 'discarded', '舍弃过的 UUID 不许回流');

  const v4 = '0192f0d4-1a2b-4c3d-8e4f-5a6b7c8d9e0f';
  const r = C.setUuid(db, 0, v4);
  assert.equal(r.status, 'updated');
  assert.equal(r.warning, 'not-v7', '非 v7 应给警告');
  assert.equal(db.works[0].uuid, 'xey' + v4 + 'cmd');
});

t('删除后 UUID 进舍弃名单，导入旧备份也不回流', () => {
  const db = newDb();
  C.prepareAdd(db, '来自深渊');
  C.prepareAdd(db, '葬送的芙莉莲');
  const backup = JSON.parse(JSON.stringify(db));   // 删之前的备份
  const gone = C.deleteWork(db, 0);
  assert.equal(db.works.length, 1);
  assert.equal(db.discarded.length, 1);
  assert.equal(db.discarded[0].uuid, gone.uuid);

  // 同一套数据再导回自己：剩的那条算重复，删掉的那条不回流
  const same = C.mergeImport(db, backup);
  assert.equal(same.added, 0);
  assert.equal(same.skippedDup, 1);
  assert.equal(same.skippedDiscarded, 1, '已舍弃的 UUID 不该回流');
  assert.equal(db.works.length, 1);

  // 另一个库：舍弃名单已经跟着新备份带过来了，这时再导入旧备份
  const fresh = newDb();
  fresh.discarded = JSON.parse(JSON.stringify(db.discarded));
  const res = C.mergeImport(fresh, backup);
  assert.equal(res.added, 1, '只有没删的那条能进来');
  assert.equal(res.skippedDiscarded, 1, '已舍弃的 UUID 不该回流');
  assert.ok(!fresh.works.some(w => w.uuid === gone.uuid), '被舍弃的 UUID 回流了');
  assert.equal(fresh.works[0].name, '葬送的芙莉莲');
});

t('导出备份 → 导入空库：数据无损（uuid/名字/别名/舍弃名单）', () => {
  const a = newDb();
  C.prepareAdd(a, '葬送的芙莉莲', '葬送のフリーレン / Frieren');
  C.prepareAdd(a, '来自深渊');
  C.deleteWork(a, 1);
  C.prepareAdd(a, 'Cowboy Bebop');
  const file = JSON.parse(JSON.stringify(a));       // 相当于导出的 JSON

  const b = newDb();
  const res = C.mergeImport(b, file);
  assert.equal(res.added, a.works.length);
  assert.equal(res.skippedDup, 0);
  assert.equal(JSON.stringify(b.works), JSON.stringify(a.works), '作品数据往返后有差异');
  assert.equal(JSON.stringify(b.discarded), JSON.stringify(a.discarded), '舍弃名单有差异');
});

t('导出的两份表：列表不含 UUID，对应表与库一一对应', () => {
  const db = newDb();
  C.prepareAdd(db, '甲作品', '甲别名');
  C.prepareAdd(db, 'Re:Zero');
  const listCsv = C.toCSV(['序号', '作品名', '别名'],
    C.listRows(db.works).map(r => [r.seq, r.name, r.aliases]));
  assert.ok(!listCsv.includes('xey'), '作品列表里漏出 UUID');
  const uuidCsv = C.toCSV(['序号', '作品名', 'UUID'],
    C.uuidRows(db.works).map(r => [r.seq, r.name, r.uuid]));
  const lines = uuidCsv.split('\r\n').slice(1);
  assert.equal(lines.length, db.works.length);
  lines.forEach((line, i) => {
    assert.ok(line.includes(db.works[i].uuid), '第 ' + (i + 1) + ' 行 UUID 对不上');
    assert.ok(line.includes(db.works[i].name), '第 ' + (i + 1) + ' 行作品名对不上');
  });
});

t('全套流程跑完，UUID 仍然全局唯一', () => {
  const db = newDb();
  const inputs = ['甲', '乙', '丙', '葬送的芙莉莲', '来自深渊', 'Re:Zero', 'Cowboy Bebop',
                  '阿凡达', '白箱', 'メイドインアビス'];
  inputs.forEach(n => C.prepareAdd(db, n, n + '别名'));
  inputs.forEach(n => C.prepareAdd(db, n + '别名'));   // 全部应判重复
  assert.equal(db.works.length, inputs.length, '别名查重漏了');
  assert.equal(new Set(db.works.map(w => w.uuid)).size, inputs.length);
  assert.ok(db.works.every(w => /^xey[0-9a-f-]{36}cmd$/.test(w.uuid)), '有 UUID 格式不合规');
});

console.log('[7] 搜索：作品名 / 别名 / UUID');
const searchLib = () => ([
  { uuid: 'xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd', name: '葬送的芙莉莲', aliases: ['葬送のフリーレン', 'Frieren'] },
  { uuid: 'xey0192f0d5-2b3c-7d4e-9f50-6b7c8d9e0f10cmd', name: '来自深渊', aliases: ['メイドインアビス'] },
  { uuid: 'xey0192f0d6-3c4d-7e5f-8a61-7c8d9e0f1021cmd', name: 'Re:Zero', aliases: [] }
]);
t('空查询返回全部（顺序不变）', () => {
  sameJSON(C.searchWorks(searchLib(), ''), [0, 1, 2]);
  sameJSON(C.searchWorks(searchLib(), '   '), [0, 1, 2]);
  sameJSON(C.searchWorks(searchLib(), '《》'), [0, 1, 2]);
});
t('按名字搜索：部分匹配，且忽略书名号/空格/标点/大小写', () => {
  const lib = searchLib();
  sameJSON(C.searchWorks(lib, '芙莉莲'), [0]);
  sameJSON(C.searchWorks(lib, '《葬送的芙莉莲》'), [0]);
  sameJSON(C.searchWorks(lib, '葬送的 芙莉莲'), [0]);
  sameJSON(C.searchWorks(lib, 'rezero'), [2]);
  sameJSON(C.searchWorks(lib, 'RE:ZERO'), [2]);
  sameJSON(C.searchWorks(lib, '深渊'), [1]);
});
t('按别名搜索命中它所属的作品', () => {
  const lib = searchLib();
  sameJSON(C.searchWorks(lib, 'Frieren'), [0]);
  sameJSON(C.searchWorks(lib, 'ふり'), []);
  sameJSON(C.searchWorks(lib, 'メイドイン'), [1]);
});
t('按 UUID 搜索：全文、中段、去连字符、大小写、只写前后缀都能命中', () => {
  const lib = searchLib();
  const u = lib[0].uuid;
  sameJSON(C.searchWorks(lib, u), [0]);
  sameJSON(C.searchWorks(lib, u.slice(3, 15)), [0]);
  sameJSON(C.searchWorks(lib, u.replace(/-/g, '')), [0]);
  sameJSON(C.searchWorks(lib, u.toUpperCase()), [0]);
  sameJSON(C.searchWorks(lib, u.slice(0, -3)), [0]);        // 不带 cmd
  sameJSON(C.searchWorks(lib, u.slice(3)), [0]);            // 不带 xey
  sameJSON(C.searchWorks(lib, 'xey'), [0, 1, 2]);           // 前缀 → 全部
});
t('下标是整库真实位置，不是过滤后的名次', () => {
  sameJSON(C.searchWorks(searchLib(), '来自深渊'), [1]);
  sameJSON(C.searchWorks(searchLib(), '1021cmd'), [2]);
});
t('搜不到就是空数组，不是 null', () => {
  sameJSON(C.searchWorks(searchLib(), '压根不存在的作品zzz'), []);
});
t('搜索不改动传入的数组', () => {
  const lib = searchLib();
  const snapshot = JSON.stringify(lib);
  C.searchWorks(lib, '芙莉莲');
  C.searchWorks(lib, 'xey');
  assert.equal(JSON.stringify(lib), snapshot);
});
t('matchWork 单条判定与 searchWorks 一致', () => {
  const lib = searchLib();
  assert.equal(C.matchWork(lib[0], 'Frieren'), true);
  assert.equal(C.matchWork(lib[0], '来自深渊'), false);
  assert.equal(C.matchWork(lib[2], ''), true);
});

console.log('[8] 标签（分类）');
t('标签清理：去空、去重（按比对口径）、截断过长、按分隔符拆开', () => {
  sameJSON(C.cleanTags(['奇幻', ' 奇幻 ', '', '搞笑/日常', 'x'.repeat(20)]),
    ['奇幻', '搞笑', '日常', 'xxxxxxxxxxxx']);
  sameJSON(C.cleanTags('奇幻/日常'), ['奇幻', '日常']);
  sameJSON(C.cleanTags([]), []);
});
t('标签输入按分隔符拆开', () => {
  sameJSON(C.splitTagInput('奇幻/日常、搞笑,治愈;校园|异世界'),
    ['奇幻', '日常', '搞笑', '治愈', '校园', '异世界']);
});
t('标签统计：次数降序，同次数按拼音/字母（顺序可复现）', () => {
  const works = [
    { name: 'a', tags: ['日常', '治愈'] },
    { name: 'b', tags: ['日常'] },
    { name: 'c', tags: ['战斗', '日常'] }
  ];
  sameJSON(C.allTags(works).map(x => x.tag + ':' + x.count), ['日常:3', '战斗:1', '治愈:1']);
  const again = C.allTags(works).map(x => x.tag + ':' + x.count);
  sameJSON(C.allTags(works).map(x => x.tag + ':' + x.count), again);
});
t('标签筛选：多个标签是「任一命中」，返回整库真实下标', () => {
  const works = [
    { name: 'a', tags: ['日常'] },
    { name: 'b', tags: ['战斗'] },
    { name: 'c', tags: [] },
    { name: 'd', tags: ['日常', '治愈'] }
  ];
  sameJSON(C.worksWithTags(works, ['日常']), [0, 3]);
  sameJSON(C.worksWithTags(works, ['日常', '战斗']), [0, 1, 3]);
  sameJSON(C.worksWithTags(works, []), null);
  sameJSON(C.worksWithTags(works, ['不存在']), []);
});
t('搜索也认标签', () => {
  const works = [{ name: '甲', aliases: [], tags: ['奇幻', '搞笑'], uuid: 'xeyxcmd' }];
  assert.equal(C.matchWork(works[0], '奇幻'), true);
  assert.equal(C.matchWork(works[0], '奇幻'), true);
  assert.equal(C.matchWork(works[0], '搞笑'), true);
  assert.equal(C.matchWork(works[0], '治愈'), false);
  sameJSON(C.searchWorks(works, '奇幻'), [0]);
});
t('新增作品时能带标签，标签跟着一起落库', () => {
  const db = newDb();
  const r = C.prepareAdd(db, '带标签的作品', '', { tags: '奇幻/搞笑' });
  assert.equal(r.status, 'added');
  sameJSON(db.works[0].tags, ['奇幻', '搞笑']);
  sameJSON(r.tags, ['奇幻', '搞笑']);
});
t('相似弹窗这条路上，标签不会丢', () => {
  const db = newDb();
  C.prepareAdd(db, '葬送的芙莉莲');
  const pending = C.prepareAdd(db, '葬送芙莉莲', '', { tags: ['奇幻'] });
  assert.equal(pending.status, 'need-similar');
  const res = C.resolveSimilar(db, pending, 'new', 0);
  assert.equal(res.status, 'added');
  const added = db.works.find(w => w.name === '葬送芙莉莲');
  sameJSON(added.tags, ['奇幻']);
});
t('setTags 改标签；不存在的作品返回 canceled', () => {
  const db = newDb();
  C.prepareAdd(db, '甲作品');
  const r = C.setTags(db, 0, '日常、治愈');
  assert.equal(r.status, 'updated');
  sameJSON(db.works[0].tags, ['日常', '治愈']);
  assert.equal(C.setTags(db, 99, 'x').status, 'canceled');
});
t('导出行的标签列 + 备份往返都带上标签', () => {
  const db = newDb();
  C.prepareAdd(db, '甲作品', '', { tags: '日常' });
  assert.equal(C.listRows(db.works)[0].tags, '日常');
  const file = JSON.parse(JSON.stringify(db));
  const fresh = newDb();
  C.mergeImport(fresh, file);
  sameJSON(fresh.works[0].tags, ['日常']);
});
t('老数据没有 tags 字段也不会出错', () => {
  const old = { works: [{ uuid: 'xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd', name: '老作品', aliases: [] }] };
  const fresh = newDb();
  C.mergeImport(fresh, old);
  sameJSON(fresh.works[0].tags, []);
  assert.equal(C.matchWork(fresh.works[0], '老'), true);
  sameJSON(C.worksWithTags(fresh.works, ['日常']), []);
  assert.equal(C.listRows(fresh.works)[0].tags, '');
});

t('导入：同 UUID 会用文件里的名字/别名/标签更新已有记录，UUID 本身不动', () => {
  const db = newDb();
  C.prepareAdd(db, '甲作品');
  const uuid = db.works[0].uuid;
  const file = { works: [{ uuid, name: '甲作品', aliases: ['别名甲'], tags: ['奇幻', '日常'] }] };
  const res = C.mergeImport(db, file);
  assert.equal(res.added, 0, '不该新增');
  assert.equal(res.updated, 1, '应该更新 1 条');
  sameJSON(db.works[0].aliases, ['别名甲']);
  sameJSON(db.works[0].tags, ['奇幻', '日常']);
  assert.equal(db.works[0].uuid, uuid, 'UUID 绝不能被改');
  const again = C.mergeImport(db, file);
  assert.equal(again.updated, 0, '内容一样就不该算更新');
  assert.equal(again.skippedDup, 1);
});

console.log('[1] 页面脚本语法检查');
t('HTML 内每个 script 段都能通过解析（无语法错误）', () => {
  const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  assert.ok(blocks.length >= 2, '只抽到 ' + blocks.length + ' 个 script 段');
  blocks.forEach((code, i) => { new vm.Script(code, { filename: 'inline-' + i + '.js' }); });
});

t('界面依赖的元素 id 都存在', () => {
  const ids = ['inpName', 'inpAlias', 'btnAdd', 'btnResort', 'chkInsertByRule',
               'btnExportJson', 'btnImportJson', 'fileImport',
               'banner', 'toast', 'stat', 'saveState', 'listHead', 'tbody', 'emptyHint',
               'inpSearch', 'btnClearSearch', 'searchInfo',
               'inpTags', 'tagBar',
               'dlgTags', 'dlgTagsName', 'dlgTagCands', 'inpNewTag', 'btnAddTag',
               'dlgTagPicked', 'dlgTagSave', 'dlgTagCancel',
               'roleBadge', 'btnForgetToken', 'loginCard', 'inpToken', 'btnLogin', 'loginHint',
               'roNote', 'issueBox', 'issueText', 'btnIssueAction',
               'btnPreviewList', 'btnExportList', 'btnExportUuid',
               'previewWrap', 'previewTitle', 'previewBody', 'btnClosePreview',
               'dlgSimilar', 'dlgInput', 'dlgCands',
               'dlgSame', 'dlgAlias', 'dlgNew', 'dlgCancel'];
  const ui = html.slice(html.indexOf('<script>'));
  ids.forEach(id => {
    assert.ok(html.includes('id="' + id + '"'), 'HTML 缺少元素 ' + id);
    assert.ok(ui.includes("'" + id + "'"), '界面脚本没有引用 ' + id);
  });
});

console.log('');
console.log(`结果: ${pass} 通过, ${fail} 失败`);
if (fail) { failures.forEach(f => console.log('  - ' + f)); process.exit(1); }
