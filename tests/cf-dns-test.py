# -*- coding: utf-8 -*-
"""离线测试：tools\\cf_dns.py —— 用一个假的 Cloudflare API 验证它的行为。

真接口不联网也能测：起一个本地假 API，断言它
  * 记录不存在时新建 AAAA（灰云、TTL 60）
  * 记录已存在但地址变了时更新
  * 地址已经对的时候什么都不做
  * 没有令牌文件时给明确提示、返回 1
  * --dry-run 不发写请求
"""
import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import importlib.util

spec = importlib.util.spec_from_file_location("cfdns", os.path.join(ROOT, "tools", "cf_dns.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

WORK = os.path.join(ROOT, "tests", "fixtures", "tmp", "cfdns")
PASS = FAIL = 0
FAILURES = []


def t(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print("  PASS  " + name)
    except AssertionError as exc:
        FAIL += 1
        FAILURES.append("%s -> %s" % (name, exc))
        print("  FAIL  %s -> %s" % (name, exc))


class FakeCF:
    """极简 Cloudflare API 假实现。"""

    def __init__(self):
        self.records = []            # [{"id":..,"type":"AAAA","name":..,"content":..,"proxied":..,"ttl":..}]
        self.calls = []
        self.token_ok = True
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                if not outer.token_ok or not (self.headers.get("Authorization") or "").startswith("Bearer "):
                    self._send(403, {"success": False, "errors": [{"message": "令牌无效"}], "result": None})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                outer.calls.append(("GET", self.path, None))
                if self.path.startswith("/zones?"):
                    return self._send(200, {"success": True, "result": [{"id": "zone123", "name": "example.com"}]})
                if "/dns_records?" in self.path:
                    want = self.path.split("name=")[1].split("&")[0]
                    from urllib.parse import unquote
                    want = unquote(want)
                    hit = [r for r in outer.records if r["type"] == "AAAA" and r["name"] == want]
                    return self._send(200, {"success": True, "result": hit})
                self._send(404, {"success": False, "errors": [{"message": "没有这个接口"}]})

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

            def do_POST(self):
                if not self._auth():
                    return
                b = self._body()
                outer.calls.append(("POST", self.path, b))
                rec = {"id": "rec%d" % (len(outer.records) + 1), "type": b["type"],
                       "name": b["name"], "content": b["content"], "proxied": b.get("proxied", False),
                       "ttl": b.get("ttl")}
                outer.records.append(rec)
                self._send(200, {"success": True, "result": rec})

            def do_PUT(self):
                if not self._auth():
                    return
                b = self._body()
                outer.calls.append(("PUT", self.path, b))
                rid = self.path.rstrip("/").split("/")[-1]
                for r in outer.records:
                    if r["id"] == rid:
                        r.update({"content": b["content"], "ttl": b.get("ttl"),
                                  "proxied": b.get("proxied", False)})
                        return self._send(200, {"success": True, "result": r})
                self._send(404, {"success": False, "errors": [{"message": "记录不存在"}]})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_args(api, extra):
    """直接调 main()，用 argv 模拟命令行。"""
    tok = os.path.join(WORK, "token.txt")
    with open(tok, "w", encoding="utf-8") as fh:
        fh.write("# 注释\n cf-test-token-1234567890 \n")
    argv = ["cf_dns.py", "--token-file", tok, "--api-base", api.base,
            "--ip", "2408:xxxx:6c0:15f1:a4aa:fe76:fbc6:9"] + extra
    old = sys.argv
    sys.argv = argv
    try:
        return mod.main()
    finally:
        sys.argv = old


if os.path.isdir(WORK):
    shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)

print("[域名解析自动更新] 离线测试（假 Cloudflare API）")

api = FakeCF()
try:
    def _create():
        assert not api.records
        rc = run_args(api, [])
        assert rc == 0, "返回码 %s" % rc
        assert len(api.records) == 1, api.records
        r = api.records[0]
        assert r["type"] == "AAAA" and r["name"] == "lib.example.com"
        assert r["content"].startswith("2408:"), r
        assert r["proxied"] is False, "必须是灰云（不经过 Cloudflare 代理）"
        assert r["ttl"] == 60
        assert any(c[0] == "POST" for c in api.calls), "应该用 POST 新建"

    t("记录不存在 → 新建 AAAA（灰云 / TTL 60）", _create)

    def _noop():
        before = len(api.calls)
        rc = run_args(api, [])
        assert rc == 0
        writes = [c for c in api.calls[before:] if c[0] in ("POST", "PUT")]
        assert not writes, "地址没变却写了 DNS：%s" % writes

    t("地址已经对了 → 不重复写", _noop)

    def _update():
        api.records[0]["content"] = "2408:xxxx:dead:beef::1"
        before = len(api.calls)
        rc = run_args(api, [])
        assert rc == 0
        assert api.records[0]["content"].startswith("2408:xxxx:6c0"), api.records[0]
        assert any(c[0] == "PUT" for c in api.calls[before:]), "应该用 PUT 更新"

    t("地址变了 → 用 PUT 更新成新地址", _update)

    def _dry_run():
        api.records[0]["content"] = "2408:xxxx:1:2:3:4:5:6"
        before = len(api.calls)
        rc = run_args(api, ["--dry-run"])
        assert rc == 0
        writes = [c for c in api.calls[before:] if c[0] in ("POST", "PUT")]
        assert not writes, "dry-run 却真的写了：%s" % writes

    t("--dry-run 只说不做", _dry_run)

    def _bad_token():
        api.token_ok = False
        try:
            rc = run_args(api, [])
            assert rc == 2, "令牌无效应该返回 2，实际 %s" % rc
        finally:
            api.token_ok = True

    t("令牌无效 → 明确报错、返回 2", _bad_token)

    def _no_token_file():
        argv = ["cf_dns.py", "--token-file", os.path.join(WORK, "nope.txt"),
                "--api-base", api.base, "--ip", "2408:xxxx:6c0:15f1:a4aa:fe76:fbc6:9"]
        old = sys.argv
        sys.argv = argv
        try:
            rc = mod.main()
        finally:
            sys.argv = old
        assert rc == 1, "没有令牌文件应该返回 1，实际 %s" % rc

    t("没有令牌文件 → 返回 1 并告诉怎么建", _no_token_file)

    def _proxied_off_enforced():
        # 即使记录被手动改成橙云，下次更新也要压回灰云
        api.records[0]["proxied"] = True
        rc = run_args(api, [])
        assert rc == 0
        assert api.records[0]["proxied"] is False, "应该强制灰云"

    t("记录被改成橙云 → 更新时压回灰云", _proxied_off_enforced)
finally:
    api.stop()

print("")
print("域名解析测试结果: %d 通过, %d 失败" % (PASS, FAIL))
if FAIL:
    for line in FAILURES:
        print("  - " + line)
    sys.exit(1)
