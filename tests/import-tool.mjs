// 导入器测试：直接调用 main()，把 console.log 截下来断言报告内容。
// 用法: node tests/import-tool.mjs
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import assert from 'node:assert/strict';
import { main, parseDelimited, readRecords } from '../tools/导入基准清单.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const tmp = join(here, 'fixtures', 'tmp');
mkdirSync(tmp, { recursive: true });

let pass = 0, fail = 0;
const failures = [];
function t(name, fn) {
  try { fn(); pass++; console.log('  PASS  ' + name); }
  catch (e) { fail++; failures.push(name + ' -> ' + e.message); console.log('  FAIL  ' + name + ' -> ' + e.message); }
}
// 跑一次导入器并收集它打印的报告
function run(args) {
  const logs = [];
  const orig = console.log;
  console.log = (...a) => logs.push(a.join(' '));
  try { main(['node', '导入基准清单.mjs', ...args]); }
  finally { console.log = orig; }
  return logs.join('\n');
}
const wipe = p => { try { rmSync(p, { force: true }); } catch (e) { /* 忽略 */ } };

console.log('[导入器] 解析');
t('CSV 解析认引号与转义', () => {
  const rows = parseDelimited('a,"b,c","他""说"\r\n1,2,3', ',');
  assert.equal(JSON.stringify(rows), JSON.stringify([['a', 'b,c', '他"说'], ['1', '2', '3']]));
});
t('读 CSV：表头列名顺序随意都能认', () => {
  const f = join(tmp, 'order.csv');
  writeFileSync(f, 'UUID,别名,作品名\n,葬送のフリーレン,葬送的芙莉莲\n', 'utf8');
  const { records, columns } = readRecords(f);
  assert.equal(records[0].name, '葬送的芙莉莲');
  assert.equal(records[0].aliases.join('/'), '葬送のフリーレン');
  assert.equal(columns.nameCol, 2);
  assert.equal(columns.uuidCol, 0);
});
t('读 TXT：忽略空行与 # 注释行', () => {
  const { records } = readRecords(join(here, 'fixtures', '名单.txt'));
  assert.equal(records.length, 4);
  assert.equal(records[0].name, '葬送的芙莉莲');
  assert.equal(records[1].name, '《来自深渊》');
});
t('读 JSON：既认备份格式也认裸数组', () => {
  const f1 = join(tmp, 'backup.json');
  writeFileSync(f1, JSON.stringify({ works: [{ uuid: 'xeyxcmd', name: '甲' }] }), 'utf8');
  assert.equal(readRecords(f1).records.length, 1);
  const f2 = join(tmp, 'bare.json');
  writeFileSync(f2, JSON.stringify([{ name: '乙' }]), 'utf8');
  assert.equal(readRecords(f2).records[0].name, '乙');
});

console.log('[导入器] 只报告不写文件');
t('默认不写任何文件，报告里说明清楚', () => {
  const out = join(tmp, 'should-not-exist.json');
  wipe(out);
  const report = run([join(root, '作品清单-模板.csv')]);
  assert.match(report, /这是核对报告，没有写任何文件/);
  let existed = true;
  try { readFileSync(out); } catch (e) { existed = false; }
  assert.equal(existed, false, '不该写出文件');
  assert.match(report, /读入记录：4 条/);
  assert.match(report, /将新增：4 条/);
  assert.match(report, /重复（已在库里，按规则不新增）：0 条/);
});
t('空库导入模板：4 条都新增，序号按最终顺序且连续', () => {
  const report = run([join(root, '作品清单-模板.csv')]);
  assert.match(report, /\+ 序号 1  来自深渊/);
  assert.match(report, /\+ 序号 2  葬送的芙莉莲/);
  assert.match(report, /\+ 序号 3  Cowboy Bebop/);
  assert.match(report, /\+ 序号 4  Re:Zero/);
  assert.match(report, /排序预览（前 30）：1\.来自深渊  2\.葬送的芙莉莲  3\.Cowboy Bebop  4\.Re:Zero/);
});

console.log('[导入器] 重复 / 疑似重复 / UUID 冲突');
t('同名与别名都算重复，且明确报出命中方式', () => {
  const lib = join(tmp, 'lib1.json');
  writeFileSync(lib, JSON.stringify({
    settings: { insertByRule: true }, discarded: [],
    works: [{ uuid: 'xey01920000-0000-7000-8000-000000000001cmd', name: '葬送的芙莉莲', aliases: ['Frieren'], addedAt: 'x' }]
  }), 'utf8');
  const list = join(tmp, 'dupes.txt');
  writeFileSync(list, '《葬送的芙莉莲》\nFrieren\n来自深渊\n', 'utf8');
  const report = run([list, '--lib', lib]);
  assert.match(report, /重复（已在库里，按规则不新增）：2 条/);
  assert.match(report, /命中序号 1（葬送的芙莉莲）/);
  assert.match(report, /命中它的别名「Frieren」/);
  assert.match(report, /将新增：1 条/);
});
t('疑似重复默认不加进来，并列出候选与相似度', () => {
  const list = join(tmp, 'close.txt');
  writeFileSync(list, '葬送的芙莉莲\n葬送芙莉莲\n', 'utf8');
  const report = run([list]);
  assert.match(report, /疑似重复（像但要你拍板）：1 条/);
  assert.match(report, /输入「葬送芙莉莲」像：［未收录］/);
  assert.match(report, /序号 1　葬送的芙莉莲　相似度 \d+%/);
  assert.match(report, /将新增：1 条/);
  assert.match(report, /确认是新作品就加 --accept-similar 重跑/);
});
t('--accept-similar 时才把疑似重复当新作品收下', () => {
  const list = join(tmp, 'close.txt');
  const report = run([list, '--accept-similar']);
  assert.match(report, /疑似重复（像但要你拍板）：1 条，其中已按 --accept-similar 收录 1 条/);
  assert.match(report, /输入「葬送芙莉莲」像：［已收录］/);
  assert.match(report, /将新增：2 条/);
});
t('基准文件里的 UUID 原样保留', () => {
  const list = join(tmp, 'withuuid.csv');
  const u = 'xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd';
  writeFileSync(list, '作品名,别名,UUID\n来自深渊,, ' + u + '\n', 'utf8');
  const out = join(tmp, 'withuuid-out.json');
  run([list, '--write', out]);
  const db = JSON.parse(readFileSync(out, 'utf8'));
  assert.equal(db.works[0].uuid, u, '基准文件里的 UUID 被改掉了');
});
t('UUID 撞库时不覆盖，单独列出来', () => {
  const lib = join(tmp, 'lib2.json');
  const u = 'xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd';
  writeFileSync(lib, JSON.stringify({
    settings: { insertByRule: true }, discarded: [],
    works: [{ uuid: u, name: '甲作品', aliases: [], addedAt: 'x' }]
  }), 'utf8');
  const list = join(tmp, 'clash.csv');
  writeFileSync(list, '作品名,UUID\n乙作品,' + u + '\n', 'utf8');
  const report = run([list, '--lib', lib]);
  assert.match(report, /UUID 冲突（没有覆盖任何东西）：1 条/);
  assert.match(report, /带的 UUID .* 已属于 甲作品/);
  assert.match(report, /将新增：0 条/);
});
t('已舍弃的 UUID 不许借基准文件回流', () => {
  const u = 'xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd';
  const lib = join(tmp, 'lib3.json');
  writeFileSync(lib, JSON.stringify({
    settings: { insertByRule: true },
    discarded: [{ uuid: u, name: '删过的作品', discardedAt: 'x' }],
    works: []
  }), 'utf8');
  const list = join(tmp, 'revive.csv');
  writeFileSync(list, '作品名,UUID\n删过的作品,' + u + '\n', 'utf8');
  const report = run([list, '--lib', lib]);
  assert.match(report, /已舍弃，不回流/);
  assert.match(report, /将新增：0 条/);
});

console.log('[导入器] --write 产出可直接导入的备份');
t('写出的 JSON 结构正确、UUID 合规、能再被自己读回', () => {
  const out = join(tmp, 'final.json');
  wipe(out);
  run([join(root, '作品清单-模板.csv'), '--write', out]);
  const db = JSON.parse(readFileSync(out, 'utf8'));
  assert.equal(db.works.length, 4);
  assert.ok(db.works.every(w => /^xey[0-9a-f-]{36}cmd$/.test(w.uuid)), '有 UUID 不合规');
  assert.equal(new Set(db.works.map(w => w.uuid)).size, 4, 'UUID 有重复');
  assert.deepEqual(db.works.map(w => w.name), ['来自深渊', '葬送的芙莉莲', 'Cowboy Bebop', 'Re:Zero']);
  assert.ok(Array.isArray(db.discarded));
  assert.equal(readRecords(out).records.length, 4, '写出的备份自己读不回来');
});
t('--keep-order 保持文件顺序，不按拼音排', () => {
  const out = join(tmp, 'keep.json');
  run([join(root, '作品清单-模板.csv'), '--write', out, '--keep-order']);
  const db = JSON.parse(readFileSync(out, 'utf8'));
  assert.deepEqual(db.works.map(w => w.name), ['葬送的芙莉莲', '来自深渊', 'Cowboy Bebop', 'Re:Zero']);
});
t('以已有备份为基准继续追加（--lib）时不会重复导入', () => {
  const base = join(tmp, 'final.json');
  const list = join(tmp, 'more.txt');
  writeFileSync(list, '葬送的芙莉莲\n进击的巨人\n', 'utf8');
  const report = run([list, '--lib', base]);
  assert.match(report, /原有 4 条/);
  assert.match(report, /将新增：1 条/);
  assert.match(report, /重复（已在库里，按规则不新增）：1 条/);
});

console.log('[导入器] --drop：按用户指示剔除并记录舍弃');
t('--drop 剔掉那条，UUID 进舍弃名单并写明原因', () => {
  const list = join(tmp, 'typo.csv');
  writeFileSync(list, [
    '序号,作品名,UUID',
    '1,challote,xey01926f31-8aaa-7dd6-8fb9-a3b4c5d6e7f8cmd',
    '2,charlotte,xey01926f31-8aab-7ee7-9ac0-b4c5d6e7f809cmd',
    '3,clannad,xey01926f31-8aac-7ff8-8bd1-c5d6e7f8091acmd'
  ].join('\n'), 'utf8');
  const out = join(tmp, 'typo-out.json');
  const report = run([list, '--accept-similar', '--drop', 'challote', '--write', out]);
  assert.match(report, /按你的指示剔除：1 条/);
  assert.match(report, /- challote　UUID xey01926f31-8aaa-7dd6-8fb9-a3b4c5d6e7f8cmd/);
  assert.match(report, /将新增：2 条/);

  const db = JSON.parse(readFileSync(out, 'utf8'));
  assert.deepEqual(db.works.map(w => w.name), ['charlotte', 'clannad']);
  assert.equal(db.discarded.length, 1);
  assert.equal(db.discarded[0].name, 'challote');
  assert.equal(db.discarded[0].uuid, 'xey01926f31-8aaa-7dd6-8fb9-a3b4c5d6e7f8cmd');
  assert.match(db.discarded[0].reason, /合并去重/);
  assert.ok(!db.works.some(w => w.uuid === db.discarded[0].uuid), '被剔除的 UUID 还在作品表里');
});
t('--drop 的名字对不上时明确警告，而不是默默什么都不做', () => {
  const report = run([join(tmp, 'typo.csv'), '--drop', '压根没有这个名字']);
  assert.match(report, /在清单里没找到，没有剔除任何东西/);
  // 没剔除，于是 challote 先落了库，后面的 charlotte 反而被当成疑似重复扣下 → 只新增 2 条
  assert.match(report, /将新增：2 条/);
  assert.match(report, /输入「charlotte」像：［未收录］/);
  assert.match(report, /序号 1　challote　相似度 \d+%/);
});
t('--drop 的名字照同样口径匹配（大小写/空格/书名号）', () => {
  const report = run([join(tmp, 'typo.csv'), '--drop', '《CHALLOTE》']);
  assert.match(report, /按你的指示剔除：1 条/);
});

console.log('');
console.log(`导入器测试结果: ${pass} 通过, ${fail} 失败`);
if (fail) { failures.forEach(f => console.log('  - ' + f)); process.exit(1); }
