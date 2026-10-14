# 把我发给你的 AI.md

> **这个文件的用途**：把整个文件内容**原样发给任何一个新的 AI 对话**（Claude / ChatGPT / DeepSeek 等），
> 它就能立刻、完整地理解这个项目是什么、怎么用、代码在哪、改的时候必须守哪些规矩。
> 建议连同仓库一起发给 AI（让它自己读 `README.md`、`作品库.html`、`server/作品库服务.py`、`tests/`）。
>
> 读这个文件的 AI：请先读完本文，再按「给 AI 的工作约定」那一节行事。

---

## 0. 一句话

**单文件网页应用 + 零依赖 Python 服务器**，用来记录和整理个人收集的作品（动画/漫画/小说/游戏），
给每部作品分配一个**永久绑定的 UUID**，支持标签分类、拼音排序、多种导出、以及**给朋友只读分享**。

- 应用本体：`作品库.html`（一个文件，含 HTML + CSS + JS，双击即用）
- 服务器：`server/作品库服务.py`（**只用 Python 标准库**，无第三方依赖）
- 平台：主要面向 Windows（有 `.bat` 一键脚本），服务端本身跨平台

---

## 1. 这份代码最核心的设计（先懂这些，再改代码）

### 1.1 UUID 规则（整个项目的基石）

格式：`xey` + **UUID v7** + `cmd`，例如
`xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd`

- 手动生成（不是库）：48 位毫秒时间戳 + 版本/变体位 + `crypto.getRandomValues` 随机位
- **永久绑定**：改名、改别名、重排、换设备都**不允许**改动 UUID
- 可以手动改写（只写中间一截也行，前后缀自动补齐），但要校验格式、查重、并提示"不是 v7"
- **删除即封印**：删除的作品进 `discarded` 名单，它的 UUID **不回流**（导入时命中已舍弃 UUID 会跳过并提示）
- **常规输出里不显示 UUID**：列表默认看不到，点某行的「显示UUID」才临时展开（内存里，不落盘）；
  「作品-UUID 对照表 CSV」只在你明确点它时才导出

### 1.2 比对口径（查重 / 搜索共用）

`NFKC 归一化 → 去掉标点/符号/空白 → 转小写`。
于是《作品名》= `作品名!` = `作品名`；**显示时永远保留你输入的原字**，绝不自动改写用户输入。

### 1.3 排序规则

1. 中文按**拼音**正序（`Intl.Collator('zh-Hans-u-co-pinyin')`）
2. 其他语言按字母序（`Intl.Collator('en', {numeric:true, sensitivity:'base'})`）
3. 先中文段、后其他语言段；规则可复现（同分保持稳定顺序）

插入位置可选：「按规则插到位」或「一律追加到末尾」（后者需要时手动点「按规则重排」）。

### 1.4 标签（多标签分类）

- 每部作品 `tags: []`，可多个；最长 12 字；内部不含分隔符（写了 `/` 自动拆成两个）
- 内置常用分类（恋爱/日常/奇幻/科幻/战斗/搞笑/悬疑/治愈/校园/异世界/…），也能自己输入
- 表格上方「标签筛选」列出标签和条数，点一下筛选、多个是「任一命中」
- 标签也参与**搜索**和**导出 CSV**

### 1.5 两种运行模式（同一份 HTML，自动判断）

| 打开方式 | 模式 | 数据存哪 | 口令 |
|---|---|---|---|
| 双击本地文件（`file://`） | 本地模式 | 浏览器 `localStorage` | 不需要 |
| 从 `http(s)://` 打开 | 服务器模式 | 服务端 `server/data/作品库.json` | 需要 owner / guest |

**服务器模式的隐私设计（重要，别改坏）**：

- 未带口令（401）或连不上服务器 → **一条作品都不显示**（不拿本机缓存充数），只弹口令框
- 服务器模式**不在浏览器留任何数据副本**（不写 localStorage）
- 口令只存 `sessionStorage`（关标签页即失效），并在 `pagehide`（非 bfcache）时清除
- 启动时清掉老版本可能残留在 `localStorage` 里的 `worklib.v1` / `worklib.token`
- API 响应一律 `Cache-Control: no-store`；静态页面才允许 gzip / 304

### 1.6 服务端约定

- 两级口令：`owner`（可写）/ `guest`（只读）。比较用 `hmac.compare_digest`（防时序）
- 401 = 没口令/口令错；403 = 有口令但无权写；409 = 版本号过期（乐观并发，`revision`）
- 静态资源**白名单**：`/`、`/index.html`、`/作品库.html`、favicon 204；其它一律 404
- 写入：**临时文件 + `os.replace` 原子替换 + fsync**；写前自动存**快照**到 `server/data/快照/`（内容相同则去重跳过）
- **处理器里任何未捕获异常都要变成 500 JSON**，绝不能直接把连接掐断
  （否则浏览器只显示"网络错误"，完全没法排查 —— 这是踩过的坑）
- 支持 **gzip**（>1KB 且客户端支持时压缩，页面约压到 30%）与 **ETag/304**（手机上二次打开几乎瞬间）
- 一个 HTML 页面请求 = 一个线程（`ThreadingHTTPServer`）

---

## 2. 目录与代码地图

```
作品库.html              ← 应用本体。中间有一段 /* CORE-LOGIC-START … CORE-LOGIC-END */
                            的纯逻辑块，测试会把它抽出来在 Node 的 vm 里跑
server/作品库服务.py      ← 服务器：路由、鉴权、原子写、快照、gzip/304、端口失败提示
server/config.json       ← 配置（口令留空 = 首次启动自动生成并打印）
server/data/             ← 数据文件与快照（.gitignore 已忽略你的真实数据）
tools/launcher.py        ← 启动器：选端口(被占用自动换)、拉隧道、打印地址与口令
tools/生成启动脚本.py     ← 由它生成所有 .bat（强制纯 ASCII + CRLF）
tools/diagnose.py        ← 局域网连不上的自检（监听地址/IP/防火墙/网络类型/结论）
tools/ipv6url.py         ← 打印手机可用的公网 IPv6 网址
tools/cf_dns.py          ← 用 Cloudflare API 更新一条 AAAA 记录（域名→家里 IPv6）
tools/创建隧道.py         ← 用 Cloudflare API 自动建隧道（实验性，未充分验证）
tests/                   ← 全部离线测试（见下）
启动-作品库.bat            ← 只开局域网
启动-作品库（一键）.bat     ← 局域网 + 手机域名访问（自动提权：IPv6 转发+防火墙+更新DNS）
启动-作品库（含外网）.bat   ← 附加 Cloudflare 隧道
启动-作品库（含cpolar）.bat ← 附加 cpolar http 隧道
诊断-局域网.bat / 开放防火墙-7878.bat / 手机流量访问-IPv6.bat
```

**关键函数名**（改代码时好找）：

- `作品库.html` 逻辑块内：`makeWorkUuid` `compareKey` `normalizeName` `insertionIndexByRule`
  `findDuplicate` `findSimilar` `prepareAdd` `resolveSimilar` `insertWork` `renameWork`
  `setUuid` `deleteWork` `mergeImport` `searchWorks` `matchWork` `cleanTags` `allTags`
  `worksWithTags` `listRows` `toCSV`
- 服务器内：`resolve_role` `_send` `_handle_static` `_handle_api` `read_db_locked`
  `write_db_locked` `snapshot_locked` `bind_error_hint` `make_server`

---

## 3. 怎么用（用户视角）

### 3.1 只在这台电脑用

双击 `作品库.html`。不要 Python、不要联网、不要口令。

### 3.2 当服务器用（手机 / 多设备 / 分享）

双击 `启动-作品库.bat` → 窗口打印：

```
本机    : http://127.0.0.1:7878
局域网  : http://192.168.x.x:7878
```

**窗口别关**（关掉 = 停止服务）。手机连同一 WiFi 打开局域网地址 + **owner 口令**。

### 3.3 分享给朋友（只读）

同一个地址 + **guest 口令**。他能看、能搜、能导出；**所有写操作服务端直接 403**，
界面自动切成只读（连 UUID 都看不到）。

### 3.4 外网访问（三种，都在仓库里有对应脚本/文档）

| 方式 | 适合 | 要点 |
|---|---|---|
| **A. 自己域名 + 公网 IPv6 直连**（免费、最快） | 家里宽带拿到 2xxx 公网 IPv6 | `tools/cf_dns.py` 把 `lib.<你的域名>` 的 AAAA 更新成家里当前 IPv6（**必须灰云/DNS only**，否则流量绕境外反而慢）；`手机流量访问-IPv6.bat` 打通 IPv6→IPv4 转发与防火墙 |
| **B. cpolar / 同类国内穿透** | 无公网 IPv6、想省事 | 走国内节点，用 `-region cn_top`；**自定义域名需付费套餐**（免费版实测报"当前 Plan 不允许使用该功能"） |
| **C. Cloudflare 隧道** | 想要固定域名、不管 IP | 连接器 token 存 `tunnel-token.txt`；国内访问常偏慢（流量绕境外） |

### 3.5 日常运维

- 换口令：改 `server/config.json`（清空则重启后重新生成并打印）
- 换端口：改 `server/config.json` 的 `"port"`（**目前固定 7878**）
- 局域网连不上：`诊断-局域网.bat` → 按结论修；防火墙用 `开放防火墙-7878.bat`
- 数据备份：网页里「导出备份 JSON」；服务端每次写前也自动留快照

---

## 4. 数据格式

```json
{
  "settings": { "insertByRule": true },
  "works": [
    {
      "uuid": "xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd",
      "name": "作品名",
      "aliases": ["别名1", "别名2"],
      "tags": ["日常", "治愈"],
      "note": "",
      "addedAt": "2026-01-01T00:00:00.000Z"
    }
  ],
  "discarded": [ { "uuid": "…", "name": "…", "discardedAt": "…" } ]
}
```

- 服务端会额外维护顶层 `revision`（版本号，用于 409 并发控制）；推给服务端的 `db` 里**不带** `revision`
- **导入合并语义**：按 UUID 合并；新 UUID 追加；**同 UUID 会用文件里的名字/别名/标签更新已有记录**
  （UUID 本身永不改）；命中 `discarded` 的 UUID 跳过并提示

---

## 5. 测试（全部离线，不需要网络）

```bash
node tests/run-tests.mjs           # 73  核心逻辑：UUID/查重/相似/排序/标签/导入导出/CSV
node tests/ui-smoke.mjs            # 33  界面（本地模式）：添加/重复/相似弹窗/导出/标签/搜索
node tests/ui-server-mode.mjs      # 18  界面（服务器模式）：口令/只读/409/隐私/重连
node tests/import-tool.mjs         # 18  批量导入工具

python tests/server-test.py        # 32  服务端：鉴权/越权/404白名单/413/快照/gzip/304/异常兜底
python tests/server-live-test.py   #  8  真起进程：端口释放、重启保数据
python tests/launcher-test.py      # 11(+1 跳过)  启动脚本：编码/端口回退/令牌解析
python tests/cf-dns-test.py        #  7  域名解析自动更新（用假 Cloudflare API）
```

设计取舍在测试里有对应断言，改代码时**别把这些断言删掉**：

- 未捕获异常必须变 500 JSON，不能掐断连接
- 服务器模式不在浏览器留数据副本；口令只在 sessionStorage 并在关页面时清
- API 必须 `no-store`；页面必须能 gzip + 304
- 所有 `.bat` 必须**纯 ASCII + CRLF**

> 注意：发布版仓库**不包含** `tests/e2e-web.py`（那是原作者个人作品清单/迁移基准强绑定的端到端核对脚本）。

---

## 6. 开发约定与已知的坑（务必遵守）

1. **`.bat` 必须纯 ASCII + CRLF**
   Windows 的 cmd 按当前代码页解析批处理，中文/编码极易踩坑。
   所以约定是：**`.bat` 只负责调用 Python，所有中文提示由 Python 打印**，
   `tools/生成启动脚本.py` 生成时会**强制校验纯 ASCII 并把换行转成 CRLF**，不合格直接中止。
   非 ASCII 的 `.bat` 内容会以"非法多字节序列"或乱码报错。
2. **不要用 PowerShell 的文本 cmdlet 读写带中文的 UTF-8 文件**
   （`Get-Content` / `Set-Content` 在 Windows PowerShell 5.1 下按 ANSI 处理，会把文件写坏）。
   改文件用编辑器或 Python。
3. **控制台显示乱码 ≠ 文件坏了**：PS 5.1 读 UTF-8 会显示乱码，用 Python 校验才可信。
4. **不要 `chcp 65001`**：在这台机器上会让 cmd 解析 `.bat` 的字节偏移错乱。
5. **端口**：默认 `7878`。绑定失败 `WinError 10013`（系统保留段，Hyper-V/WSL/Docker 常见）
   或 `10048`（被占用）时，启动器会**自动换端口**并在窗口里说明；服务端也会打印中文原因而不是 traceback。
6. **静态资源白名单**不要放宽（`/data/…`、`../server/config.json` 必须继续 404）。
7. **改动必须保持测试全绿**；新增行为要补测试。
8. 前端逻辑尽量保持在 `/* CORE-LOGIC-START … CORE-LOGIC-END */` 块内 ——
   测试靠抽取这一段在 Node 里跑，放外面就测不到了。

---

## 7. 给 AI 的工作约定（**操作禁忌**）

这个项目有一条硬规则：**未经用户明确指示，绝不允许**：

1. ❌ **自动全表重建**（重新生成整个库）
2. ❌ **自动去重**（合并"看起来一样"的作品 —— 只能**提示**，让用户裁决）
3. ❌ **自动修改名称**（显示时永远保留用户输入的原字）
4. ❌ **自动移动排序位置**（除非用户点了「按规则重排」或新增时勾选了"插到位"）
5. ❌ **自动重新分配 UUID**（UUID 永久绑定，改名/重排都不动它）

其它必须守的：

- 任何写操作前先想清楚**会不会动到 UUID 或用户输入的原字**
- 涉及真实数据的修改，**先备份/先快照**，并如实报告改了什么
- **不要把任何令牌、真实域名、账号 ID、公网 IP、个人作品清单写进代码或文档**；
  仓库里一律用占位符（`example.com`、`YOUR_CLOUDFLARE_API_TOKEN`、`192.168.x.x`）
- 不知道的事**直说不知道**，不要把猜测当成结论；测试失败要如实报告，不要用"跳过"掩盖

---

## 8. 当前状态 / 未完成项

- 应用、服务器、启动脚本、标签、导出、隐私设计、205+ 项测试：**已完成并自测通过**
- Windows 下的一键脚本、诊断脚本、防火墙脚本、IPv6 域名方案：**已完成**
- **Cloudflare 隧道**：脚本与文档齐备（`tools/创建隧道.py` 属实验性，功能未充分验证，用前先 `--dry-run`）
- **cpolar 自定义域名**：免费版不支持（实测报"当前 Plan 不允许"），要用自己域名需付费或改用方案 A
- 发布仓库已做脱敏（域名/IP/账号/令牌/个人数据全部替换或排除），并带 `.gitignore` 防止再提交

---

## 9. 给 AI 的最简启动指令

> 你正在接手「作品库 · 作品-UUID 管理器」这个项目。请先读 `README.md` 和本文件，
> 再读 `作品库.html`（尤其 `CORE-LOGIC-START…END` 之间的纯逻辑）、`server/作品库服务.py`、
> 以及 `tests/` 下的测试。理解之后：
> 1）不要自动重建/去重/改名/移动/改 UUID（详见第 7 节）；
> 2）改动必须保持测试全绿，并补上对应新测试；
> 3）`.bat` 保持纯 ASCII + CRLF，中文提示交给 Python；
> 4）任何令牌、域名、IP、个人数据都不能写进代码或文档，用占位符。
