#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作品库服务端测试：把服务起在随机端口上，用真 HTTP 请求逐条验证。

    python tests\\server-test.py

覆盖：静态白名单与目录穿越、401/403/409/400/413、owner/guest 角色、
原子落盘与快照、重启后数据仍在、public_read、局域网免口令。
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import sys
import threading
from urllib.parse import quote

sys.dont_write_bytecode = True          # 别在 server\ 里留 __pycache__

# 输出被重定向/被父进程捕获时用 UTF-8（接到真控制台时保持系统默认，中文才不会花）
if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_PY = os.path.join(ROOT, "server", "作品库服务.py")
PAGE_PY = os.path.join(ROOT, "作品库.html")
# 测试数据一律放工作目录内的临时区（不写系统 TEMP，避免被文件沙箱拦）
TMP_ROOT = os.path.join(HERE, "fixtures", "tmp", "server")

spec = importlib.util.spec_from_file_location("worklib_server", SERVER_PY)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PASS = 0
FAIL = 0
FAILURES = []


def t(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print("  PASS  " + name)
    except Exception as exc:                                      # noqa: BLE001
        FAIL += 1
        FAILURES.append("%s -> %s" % (name, exc))
        print("  FAIL  %s -> %s" % (name, exc))


def expect(actual, want, what):
    assert actual == want, "%s 期望 %s，实际 %s" % (what, want, actual)


class Run:
    """起一个服务实例（同一进程内 STATE 是全局的，所以任何时刻只留一个）。"""

    current = None
    seq = 0

    def __init__(self, **overrides):
        if Run.current:
            Run.current.stop()
        data_dir = overrides.pop("_data_dir", None)
        if not data_dir:
            Run.seq += 1
            data_dir = os.path.join(TMP_ROOT, "run%d" % Run.seq)
            shutil.rmtree(data_dir, ignore_errors=True)
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.data_file = os.path.join(data_dir, "作品库.json")
        cfg = mod.default_config()
        cfg.update({
            "bind": "127.0.0.1",
            "port": 0,
            "owner_token": "owner-secret-1234567890",
            "guest_token": "guest-secret-0987654321",
            "data_file": self.data_file,
            "static_file": PAGE_PY,
            "quiet": True,
        })
        cfg.update(overrides)
        self.cfg = cfg
        self.httpd = mod.make_server(cfg)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        Run.current = self

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        finally:
            self.thread.join(timeout=5)
            if Run.current is self:
                Run.current = None

    # ---- 请求工具 ----
    def req(self, method, target, token=None, body=None, headers=None, raw_body=False, raw=False):
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["X-Worklib-Token"] = token
        payload = None
        if body is not None:
            payload = body if raw_body else json.dumps(body, ensure_ascii=False).encode("utf-8")
            if not raw_body:
                hdrs["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, target, body=payload, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            if raw:
                return resp.status, data, dict(resp.getheaders())
            return resp.status, data.decode("utf-8", "replace"), dict(resp.getheaders())
        finally:
            conn.close()


def as_json(text):
    return json.loads(text)


def sample_works():
    return [
        {"uuid": "xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd", "name": "葬送的芙莉莲",
         "aliases": ["Frieren"], "note": "", "addedAt": "2026-01-01T00:00:00.000Z"},
        {"uuid": "xey0192f0d5-2b3c-7d4e-9f50-6b7c8d9e0f10cmd", "name": "来自深渊",
         "aliases": [], "note": "", "addedAt": "2026-01-01T00:00:00.000Z"},
    ]


def sample_db():
    return {"kind": "worklib-db", "version": 1, "settings": {"insertByRule": True},
            "works": sample_works(), "discarded": []}


# =========================================================================== #
print("[服务端] 静态页面与白名单")
run = Run()


def _get_root():
    st, body, hs = run.req("GET", "/")
    assert st == 200, "状态 %s" % st
    assert "作品库" in body, "返回的不是页面"
    assert "text/html" in hs.get("Content-Type", ""), hs.get("Content-Type")


t("GET / 返回页面（200 + text/html）", _get_root)


def _get_named():
    # 浏览器发中文路径时一定是百分号编码的，这里就按真实请求测
    for target in ("/%E4%BD%9C%E5%93%81%E5%BA%93.html", "/index.html", "/"):
        st, body, _ = run.req("GET", target)
        assert st == 200 and "作品库" in body, "%s -> %s" % (target, st)


t("作品库.html 的两种写法都能拿到页面", _get_named)


def _favicon():
    st, _, _ = run.req("GET", "/favicon.ico")
    expect(st, 204, "favicon")


t("favicon 请求返回 204（不报错）", _favicon)


def _traversal():
    attacks = [
        "/../server/config.json",
        "/../../server/config.json",
        "/..%2fserver%2fconfig.json",
        "/%2e%2e/server/config.json",
        "/server/config.json",
        "/data/%E4%BD%9C%E5%93%81%E5%BA%93.json",
        "/api/../server/config.json",
        "/../%E4%BD%9C%E5%93%81%E5%BA%93-%E5%9F%BA%E5%87%86%E5%AF%BC%E5%85%A5.json",
    ]
    for target in attacks:
        st, body, _ = run.req("GET", target)
        assert st in (400, 404), "%s 竟然返回 %s" % (target, st)
        assert "owner-secret" not in body, "%s 泄露了口令！" % target
        assert "xey0192" not in body, "%s 泄露了数据！" % target


t("目录穿越与数据文件全部拿不到（404，且不泄露口令/数据）", _traversal)


def _backslash_path():
    st, _, _ = run.req("GET", "/..%5Cserver%5Cconfig.json")
    assert st in (400, 404), "状态 %s" % st


t("反斜杠/编码过的穿越路径也被挡住", _backslash_path)


print("[服务端] 鉴权：401 / 403 / 角色")
t("不带口令读库 → 401", lambda: expect(run.req("GET", "/api/db")[0], 401, "无口令 GET"))
t("口令写错 → 401", lambda: expect(run.req("GET", "/api/db", token="wrong-token-ascii")[0], 401, "错误口令 GET"))


def _whoami_owner():
    st, body, _ = run.req("GET", "/api/whoami", token=run.cfg["owner_token"])
    assert st == 200, "状态 %s" % st
    obj = as_json(body)
    assert obj["role"] == "owner" and obj["canWrite"] is True, obj


t("owner 口令 → whoami 报 owner 且可写", _whoami_owner)


def _whoami_guest():
    st, body, _ = run.req("GET", "/api/whoami", token=run.cfg["guest_token"])
    assert st == 200, "状态 %s" % st
    obj = as_json(body)
    assert obj["role"] == "guest" and obj["canWrite"] is False, obj


t("guest 口令 → whoami 报 guest 且不可写", _whoami_guest)


def _guest_can_read():
    st, body, _ = run.req("GET", "/api/db", token=run.cfg["guest_token"])
    assert st == 200, "状态 %s" % st
    obj = as_json(body)
    assert obj["ok"] is True and obj["revision"] == 0 and obj["db"]["works"] == [], obj


t("guest 能读库（空库 revision 0）", _guest_can_read)


def _guest_cannot_write():
    st, body, _ = run.req("PUT", "/api/db", token=run.cfg["guest_token"], body={"db": sample_db(), "revision": 0})
    assert st == 403, "状态 %s（应该 403）" % st
    assert "只读" in as_json(body)["error"], body


t("guest 写入 → 403（服务端强制，不是前端藏按钮）", _guest_cannot_write)


def _anon_cannot_write():
    st, _, _ = run.req("PUT", "/api/db", body={"db": sample_db(), "revision": 0})
    expect(st, 401, "无口令 PUT")
    st2, _, _ = run.req("PUT", "/api/db", token="wrong-token-ascii", body={"db": sample_db(), "revision": 0})
    expect(st2, 401, "错口令 PUT")


t("无口令 / 错口令写入 → 401", _anon_cannot_write)


print("[服务端] 读写与版本号")


def _owner_write():
    st, body, hs = run.req("PUT", "/api/db", token=run.cfg["owner_token"],
                           body={"db": sample_db(), "revision": 0})
    assert st == 200, "状态 %s / %s" % (st, body)
    obj = as_json(body)
    assert obj["revision"] == 1, obj
    assert hs.get("X-Revision") == "1", hs


t("owner 写入 → 200，版本号 0 → 1", _owner_write)


def _read_back():
    st, body, _ = run.req("GET", "/api/db", token=run.cfg["owner_token"])
    obj = as_json(body)
    assert st == 200 and obj["revision"] == 1, obj
    names = [w["name"] for w in obj["db"]["works"]]
    assert names == ["葬送的芙莉莲", "来自深渊"], names
    assert obj["db"]["works"][0]["aliases"] == ["Frieren"], "别名丢了"
    assert obj["db"]["works"][0]["uuid"].endswith("cmd"), "UUID 变了"


t("读回来数据一字不差（顺序、别名、UUID）", _read_back)


def _file_on_disk():
    assert os.path.isfile(run.data_file), "数据文件没落盘：%s" % run.data_file
    with open(run.data_file, encoding="utf-8") as fh:
        obj = json.load(fh)
    assert obj["revision"] == 1, obj.get("revision")
    assert len(obj["works"]) == 2
    assert "updatedAt" in obj, "没有 updatedAt"


t("数据确实写在磁盘上（含 revision / updatedAt）", _file_on_disk)


def _conflict():
    st, body, hs = run.req("PUT", "/api/db", token=run.cfg["owner_token"],
                           body={"db": sample_db(), "revision": 0})     # 拿着旧版本再写
    expect(st, 409, "过期版本 PUT")
    assert hs.get("X-Revision") == "1", hs
    obj = as_json(body)
    assert obj["ok"] is False and "别处修改" in obj["error"], obj
    with open(run.data_file, encoding="utf-8") as fh:
        assert json.load(fh)["works"][1]["name"] == "来自深渊", "冲突时文件被改坏了"


t("版本号过期写入 → 409，且磁盘数据没被动", _conflict)


def _revision_ok():
    st, _, _ = run.req("PUT", "/api/db", token=run.cfg["owner_token"],
                       body={"db": sample_db(), "revision": 1}, headers={"If-Revision": "1"})
    expect(st, 200, "版本号正确的 PUT")


t("版本号对得上就能写（revision 1 → 2）", _revision_ok)


def _bad_json():
    st, _, _ = run.req("PUT", "/api/db", token=run.cfg["owner_token"], body=b"{not json", raw_body=True)
    expect(st, 400, "坏 JSON")


t("请求体不是 JSON → 400", _bad_json)


def _no_works():
    st, body, _ = run.req("PUT", "/api/db", token=run.cfg["owner_token"], body={"db": {"foo": 1}, "revision": 2})
    assert st == 400 and "works" in as_json(body)["error"], "状态 %s / %s" % (st, body)


t("请求体里没有 works 数组 → 400", _no_works)


def _atomic_no_temp_left():
    leftovers = [f for f in os.listdir(run.data_dir) if f.startswith(".tmp-")]
    assert not leftovers, "落盘后残留临时文件：%s" % leftovers


t("原子写入：目录里不残留临时文件", _atomic_no_temp_left)


def _snapshots():
    """写入前应该先把「上一版好数据」存进快照目录，手滑才有得翻。"""
    folder = os.path.join(os.path.dirname(run.data_file), "快照")
    assert os.path.isdir(folder), "没有快照目录"
    files = [f for f in os.listdir(folder) if f.endswith(".json")]
    assert files, "没有任何快照"
    assert any("before-write" in f for f in files), "写前快照缺失：%s" % files

    def rev_of(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("revision", 0)

    live = rev_of(run.data_file)
    oldest = sorted(files, key=lambda f: os.path.getmtime(os.path.join(folder, f)))[0]
    snap_rev = rev_of(os.path.join(folder, oldest))
    # 关键不变量：快照存的是「写之前」的版本，所以一定比线上旧
    assert snap_rev < live, "快照 revision=%s 不比线上 revision=%s 旧，说明存错了时机" % (snap_rev, live)


t("写入前先留旧版快照（存的是上一版好数据）", _snapshots)


print("[服务端] 重启与配置开关")


def _restart_keeps_data():
    data_dir = run.data_dir
    run.stop()
    again = Run(_data_dir=data_dir)
    st, body, _ = again.req("GET", "/api/db", token=again.cfg["owner_token"])
    obj = as_json(body)
    assert st == 200, "状态 %s" % st
    assert obj["revision"] == 2, "重启后版本号变了：%s" % obj["revision"]
    assert [w["name"] for w in obj["db"]["works"]] == ["葬送的芙莉莲", "来自深渊"], "重启后数据丢了"
    # 启动时数据已存在 → 应该留一份启动快照
    folder = os.path.join(data_dir, "快照")
    files = [f for f in os.listdir(folder) if f.endswith(".json")] if os.path.isdir(folder) else []
    assert any("start" in f for f in files), "启动快照缺失：%s" % files


t("重启服务后数据与版本号都还在（并留启动快照）", _restart_keeps_data)


def _public_read():
    pub = Run(public_read=True)
    st, body, _ = pub.req("GET", "/api/db")                 # 不带口令
    assert st == 200, "状态 %s" % st
    assert as_json(body)["ok"] is True
    st2, body2, _ = pub.req("GET", "/api/whoami")
    assert st2 == 200 and as_json(body2)["role"] == "guest", "public_read 应该是 guest（%s / %s）" % (st2, body2)
    st3, _, _ = pub.req("PUT", "/api/db", body={"db": sample_db(), "revision": 0})
    expect(st3, 403, "public_read 下的 PUT")


t("public_read：不带口令能读（guest），仍然不能写", _public_read)


def _lan_no_token():
    lan = Run(lan_no_token=True, lan_cidrs=["127.0.0.1/32"])
    st, body, _ = lan.req("GET", "/api/whoami")             # 不带口令，来源是 127.0.0.1
    assert st == 200, "状态 %s" % st
    assert as_json(body)["role"] == "owner", body
    st2, body2, _ = lan.req("PUT", "/api/db", body={"db": sample_db(), "revision": 0})
    assert st2 == 200, "局域网免口令时应该能写：%s / %s" % (st2, body2)


t("lan_no_token：局域网网段内免口令，且视为 owner", _lan_no_token)


def _lan_no_token_not_in_range():
    lan = Run(lan_no_token=True, lan_cidrs=["10.99.0.0/16"])   # 127.0.0.1 不在范围内
    st, _, _ = lan.req("GET", "/api/db")
    expect(st, 401, "网段外的免口令 GET")
    st2, _, _ = lan.req("GET", "/api/db", token=lan.cfg["owner_token"])
    expect(st2, 200, "网段外带口令 GET")


t("lan_no_token 只对配置的网段生效（网段外仍需口令）", _lan_no_token_not_in_range)


def _size_limit():
    small = Run(max_body_bytes=64)
    big = json.dumps({"db": {"works": [{"name": "x" * 500}]}, "revision": 0}).encode("utf-8")
    assert len(big) > 64
    st, _, _ = small.req("PUT", "/api/db", token=small.cfg["owner_token"], body=big, raw_body=True)
    expect(st, 413, "超大请求体")


t("请求体超过 max_body_bytes → 413", _size_limit)


def _empty_token_not_accepted():
    blank = Run(owner_token="", guest_token="")
    st, _, _ = blank.req("GET", "/api/db", token="")
    expect(st, 401, "空口令 GET")


t("配置里口令为空时不会误放行", _empty_token_not_accepted)


def _tokens_persisted():
    cfg_dir = os.path.join(TMP_ROOT, "cfg")
    shutil.rmtree(cfg_dir, ignore_errors=True)
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, "config.json")
    cfg = mod.default_config()
    cfg["data_file"] = os.path.join(cfg_dir, "作品库.json")
    cfg["static_file"] = PAGE_PY
    assert mod.ensure_tokens(cfg, cfg_path) is True
    with open(cfg_path, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert len(saved["owner_token"]) >= 32 and len(saved["guest_token"]) >= 32, "口令太短"
    assert saved["owner_token"] != saved["guest_token"], "两个口令不能一样"
    again = mod.default_config()
    again.update(saved)
    assert mod.ensure_tokens(again, cfg_path) is False, "第二次不该再重新生成"


t("口令为空时自动生成强口令并写回 config.json（只做一次）", _tokens_persisted)


def _uses_compare_digest():
    with open(SERVER_PY, encoding="utf-8") as fh:
        src = fh.read()
    assert "hmac.compare_digest" in src, "口令比较没有用常量时间比较"
    assert "os.replace" in src, "落盘不是原子替换"
    assert "X-Worklib-Token" in src


t("源码层面：常量时间比较口令 + 原子替换落盘", _uses_compare_digest)


def _token_exact_match():
    s = Run()                     # 前面的实例都被后续 Run 停掉了，这里自己起一个
    st1, _, _ = s.req("GET", "/api/db", token=s.cfg["owner_token"])
    st2, _, _ = s.req("GET", "/api/db", token=s.cfg["guest_token"])
    assert st1 == 200 and st2 == 200, (st1, st2)
    st3, _, _ = s.req("GET", "/api/db", token=s.cfg["owner_token"][:-1] + "X")
    expect(st3, 401, "近似口令 GET")
    # owner 与 guest 不能互相通用
    st4, body, _ = s.req("GET", "/api/whoami", token=s.cfg["guest_token"])
    assert as_json(body)["role"] == "guest", body


t("口令必须完全匹配（少一位都不行，owner/guest 不串用）", _token_exact_match)


def _handler_exception_becomes_500():
    """处理器里抛异常时必须回一个正经的 500 JSON —— 直接把连接掐断的话，
    浏览器只会显示"网络错误"，完全看不出原因（用户就踩过这个坑）。"""
    srv = Run()
    real = mod.read_db_locked

    def boom():
        raise RuntimeError("故意炸一下")

    mod.read_db_locked = boom
    try:
        st, body, _ = srv.req("GET", "/api/db", token=srv.cfg["owner_token"])
        assert st == 500, "异常没有变成 500，而是 %s" % st
        assert "内部错误" in body, body
    finally:
        mod.read_db_locked = real


t("处理器里抛异常 → 回 500 JSON（而不是掐断连接）", _handler_exception_becomes_500)


def _gzip_and_etag():
    """手机流量链路又慢又丢包：页面必须能 gzip、再访问只回 304。"""
    import gzip as _gz
    srv = Run()
    st, plain, hs = srv.req("GET", "/", raw=True)
    assert st == 200, st
    assert b"<html" in plain[:600] or b"<!DOCTYPE" in plain[:60], "拿到的不是页面"
    etag = hs.get("ETag")
    assert etag, "响应里没有 ETag"
    assert len(plain) > 10000, "页面太小，这个测试没意义"

    st2, raw, hs2 = srv.req("GET", "/", headers={"Accept-Encoding": "gzip, deflate"}, raw=True)
    assert st2 == 200
    assert hs2.get("Content-Encoding") == "gzip", "没有 gzip：%s" % hs2.get("Content-Encoding")
    assert hs2.get("Vary") == "Accept-Encoding"
    assert len(raw) < len(plain), "压缩后反而更大"
    assert _gz.decompress(raw) == plain, "解压回来和原页面不一致"
    print("      （页面 %d B → gzip %d B，缩到 %.0f%%）" % (len(plain), len(raw), 100.0 * len(raw) / len(plain)))

    st3, body3, hs3 = srv.req("GET", "/", headers={"If-None-Match": etag}, raw=True)
    assert st3 == 304, "页面没变却没回 304，而是 %s" % st3
    assert not body3, "304 不该带正文"
    assert hs3.get("ETag") == etag

    st4, _b4, hs4 = srv.req("GET", "/api/db", token=srv.cfg["owner_token"])
    assert st4 == 200 and hs4.get("Cache-Control") == "no-store", "API 必须不可缓存：%s" % hs4


t("页面 gzip（约 1/5 大小）+ ETag 304；API 仍 no-store", _gzip_and_etag)


if Run.current:
    Run.current.stop()

print("")
print("服务端测试结果: %d 通过, %d 失败" % (PASS, FAIL))
if FAIL:
    for line in FAILURES:
        print("  - " + line)
    sys.exit(1)
