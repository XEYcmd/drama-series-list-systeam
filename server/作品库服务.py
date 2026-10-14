#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作品库服务端 —— 静态托管 + 作品库读写接口 + 双口令鉴权 + 原子落盘。

只用 Python 标准库（3.9+ 即可，实测 3.12）。默认只干四件事：

  1. 把 作品库.html 发给浏览器（页面本身不含任何数据）；
  2. GET  /api/whoami  告诉页面「你是 owner 还是 guest」；
  3. GET  /api/db      读整库（要口令）；
  4. PUT  /api/db      写整库（**只有 owner**，带版本号防并发覆盖）。

设计要点：
  * 口令只存在本机 config.json，绝不进网页；比较用 hmac.compare_digest（常量时间）。
  * 静态资源走白名单，只发 作品库.html，**不会**把 data/作品库.json、基准 CSV 之类暴露出去。
  * 落盘是「临时文件 + os.replace」原子替换，并每次启动/写入留快照，防写一半断电。
  * 库文件就是应用自己的 db JSON（多一个 revision 字段），所以 作品库-基准导入.json
    直接拷过来就能当数据文件用，旧工具也照样读得懂。

用法：
  python 作品库服务.py                     # 用同目录 config.json 起服务
  python 作品库服务.py --port 9000 --owner abc --guest def
  python 作品库服务.py --data D:\\x\\lib.json --quiet
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

# 输出被重定向/被父进程捕获时用 UTF-8（接到真控制台时保持系统默认，中文才不会花）。
# 但若外部显式设了 PYTHONIOENCODING，就尊重它 —— 启动脚本会设成 gbk，
# 这样「.bat 自己的中文提示」和「Python 的输出」在同一个控制台/日志里编码一致。
if not sys.stdout.isatty() and not os.environ.get("PYTHONIOENCODING"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SERVER_DIR, "config.json")
DEFAULT_DATA_PATH = os.path.join(SERVER_DIR, "data", "作品库.json")
DEFAULT_STATIC_PATH = os.path.normpath(os.path.join(SERVER_DIR, "..", "作品库.html"))

# 允许对外发的静态资源（白名单，杜绝目录穿越与数据文件外泄）
STATIC_ROUTES = {
    "/": "page",
    "/index.html": "page",
    "/作品库.html": "page",
}
FAVICON = b""  # 有请求就回 204

LOCK = threading.RLock()
STATE = {}


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def default_config() -> dict:
    return {
        "bind": "0.0.0.0",
        "port": 7878,
        "owner_token": "",
        "guest_token": "",
        "public_read": False,
        "lan_no_token": False,
        "lan_cidrs": ["127.0.0.1/32", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "data_file": "data/作品库.json",
        "static_file": "../作品库.html",
        "snapshot_keep": 10,
        "max_body_bytes": 8 * 1024 * 1024,
    }


def load_config(path: str | None) -> dict:
    cfg = default_config()
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        if not isinstance(user, dict):
            raise SystemExit("config.json 必须是一个 JSON 对象")
        cfg.update({k: v for k, v in user.items() if k in cfg})
    return cfg


def save_config(cfg: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def ensure_tokens(cfg: dict, path: str | None) -> bool:
    """口令为空就生成两串强随机口令；返回是否改过配置。"""
    changed = False
    if not cfg.get("owner_token"):
        cfg["owner_token"] = secrets.token_urlsafe(32)
        changed = True
    if not cfg.get("guest_token"):
        cfg["guest_token"] = secrets.token_urlsafe(32)
        changed = True
    if changed and path:
        save_config(cfg, path)
    return changed


def resolve_from_server_dir(p: str) -> str:
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(SERVER_DIR, p))


# --------------------------------------------------------------------------- #
# 数据读写
# --------------------------------------------------------------------------- #
def empty_db() -> dict:
    return {
        "kind": "worklib-db",
        "version": 1,
        "settings": {"insertByRule": True},
        "works": [],
        "discarded": [],
        "revision": 0,
    }


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_db_locked() -> dict:
    path = STATE["data_file"]
    if not os.path.isfile(path):
        return empty_db()
    with open(path, "r", encoding="utf-8") as fh:
        obj = json.load(fh)
    if not isinstance(obj, dict) or not isinstance(obj.get("works"), list):
        raise ValueError("数据文件里没有 works 数组：%s" % path)
    obj.setdefault("discarded", [])
    obj.setdefault("settings", {"insertByRule": True})
    return obj


def atomic_write(path: str, text: str) -> None:
    """临时文件 + fsync + os.replace，保证读到的要么是旧内容要么是新内容。"""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def snapshot_locked(reason: str = "auto") -> str | None:
    """把当前数据留一份快照，只保留最新 snapshot_keep 份。

    注意：写入前调用（存的是「上一版好数据」），这样手滑删错了还能翻回来。
    内容与最新一份完全相同就跳过，避免同一次编辑产生一串一模一样的快照。
    """
    src = STATE["data_file"]
    if not os.path.isfile(src):
        return None
    folder = os.path.join(os.path.dirname(os.path.abspath(src)), "快照")
    os.makedirs(folder, exist_ok=True)
    existing = sorted(
        (f for f in os.listdir(folder) if f.endswith(".json")),
        key=lambda f: os.path.getmtime(os.path.join(folder, f)),
    )
    try:
        with open(src, "rb") as fh:
            current = fh.read()
    except OSError:
        return None
    if existing:
        try:
            with open(os.path.join(folder, existing[-1]), "rb") as fh:
                if fh.read() == current:
                    return None
        except OSError:
            pass
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    dst = os.path.join(folder, "作品库-%s-%s.json" % (stamp, reason))
    try:
        with open(dst, "wb") as fh:
            fh.write(current)
    except OSError:
        return None
    keep = int(STATE["snapshot_keep"])
    if keep > 0:
        for old in existing[:max(0, len(existing) - keep + 1)]:
            try:
                os.unlink(os.path.join(folder, old))
            except OSError:
                pass
    return dst


def write_db(db: dict, expected_revision) -> tuple[str, int]:
    """返回 (结果, 版本号)；结果是 ok / conflict。"""
    with LOCK:
        current = read_db_locked()
        cur_rev = int(current.get("revision") or 0)
        if expected_revision is not None and int(expected_revision) != cur_rev:
            return "conflict", cur_rev
        snapshot_locked("before-write")                 # 先留旧版，再覆盖
        out = dict(db)
        out["revision"] = cur_rev + 1
        out["updatedAt"] = now_iso()
        atomic_write(STATE["data_file"], json.dumps(out, ensure_ascii=False, indent=2))
        return "ok", out["revision"]


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
def client_ip(handler) -> str:
    return handler.client_address[0] if handler.client_address else ""


def ip_in_lan(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in STATE["lan_cidrs"]:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def resolve_role(handler):
    """返回 'owner' / 'guest' / None（None = 401）。"""
    token = handler.headers.get("X-Worklib-Token") or ""
    if not token:
        auth = handler.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    token = token.strip()

    if STATE["lan_no_token"] and ip_in_lan(client_ip(handler)):
        return "owner"
    if token:
        if hmac.compare_digest(token, STATE["owner_token"]):
            return "owner"
        if hmac.compare_digest(token, STATE["guest_token"]):
            return "guest"
        return None                      # 给了口令但对不上：明确 401，别默默降级
    if STATE["public_read"]:
        return "guest"
    return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "WorkLibServer/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础工具 ----------
    def log_message(self, fmt, *args):        # 屏蔽默认的 stderr 噪音
        pass

    def _log(self, status, note=""):
        if STATE.get("quiet"):
            return
        who = STATE.get("_role") or "-"
        line = "[%s] %-4s %-28s %s %s" % (
            datetime.now().strftime("%H:%M:%S"), status, self.path,
            client_ip(self), ("(" + who + ")") if who != "-" else "",
        )
        if note:
            line += "  " + note
        # 打印日志绝不允许把请求处理搞挂（控制台编码不匹配是常见坑）
        try:
            print(line, flush=True)
        except Exception:                                         # noqa: BLE001
            try:
                print(line.encode("ascii", "replace").decode("ascii"), flush=True)
            except Exception:                                     # noqa: BLE001
                pass

    def _send(self, code, payload=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        extra = dict(extra or {})

        # 手机流量下链路又慢又丢包，能压就压：页面 75KB → 十几 KB，体感差别很大。
        # 只有客户端说支持 gzip、且内容值得压（>1KB）时才压；API 的小 JSON 不折腾。
        if (len(payload) > 1024 and code == 200
                and "gzip" in (self.headers.get("Accept-Encoding") or "").lower()):
            packed = gzip.compress(payload, 6, mtime=0)
            if len(packed) < len(payload):
                payload = packed
                extra["Content-Encoding"] = "gzip"
                extra["Vary"] = "Accept-Encoding"

        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and payload:
            self.wfile.write(payload)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", extra)

    def _fail(self, code, message, extra=None):
        self._json(code, {"ok": False, "error": message}, extra)

    def _read_body(self):
        """返回 (body, 错误码)；读不完或超限时返回错误码并准备关连接。"""
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return b"", None
        try:
            length = int(raw_len)
        except ValueError:
            return b"", 400
        if length < 0:
            return b"", 400
        if length > int(STATE["max_body_bytes"]):
            self.close_connection = True          # 不读、直接断，避免串包
            return b"", 413
        return self.rfile.read(length), None

    # ---------- 路由 ----------
    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_PUT(self):
        self._handle("PUT")

    def do_POST(self):
        self._handle("POST")

    # 任何未预料的异常都不许把连接直接掐断（那在浏览器里只会显示成"网络错误"，
    # 完全看不出原因）。统一兜成 500 + 错误信息，页面就能把问题显示给你看。
    def _handle(self, method):
        try:
            self._dispatch(method)
        except Exception as exc:                                  # noqa: BLE001
            try:
                self._fail(500, "服务端内部错误：%s: %s" % (type(exc).__name__, exc))
            except Exception:                                     # noqa: BLE001
                pass
            try:
                self.close_connection = True
            except Exception:                                     # noqa: BLE001
                pass
            self._log(500, "未捕获异常：%s: %s" % (type(exc).__name__, exc))

    def _dispatch(self, method):
        STATE["_role"] = None
        try:
            path = unquote(urlsplit(self.path).path)
        except Exception:
            self._fail(400, "路径不合法")
            self._log(400)
            return
        if "\\" in path or "\x00" in path:
            self._fail(400, "路径不合法")
            self._log(400)
            return

        if path.startswith("/api/"):
            self._handle_api(method, path)
        elif method == "GET":
            self._handle_static(path)
        else:
            self._fail(405, "这个地址只支持 GET")
            self._log(405)

    # ---------- 静态 ----------
    def _handle_static(self, path):
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            self._log(204)
            return
        if path not in STATIC_ROUTES:
            # 只认白名单：/data/作品库.json、/../server/config.json 之类一律 404
            self._fail(404, "没有这个资源", {"Cache-Control": "no-store"})
            self._log(404)
            return
        target = STATE["static_file"]
        if not os.path.isfile(target):
            self._fail(500, "服务端找不到页面文件：%s" % target)
            self._log(500, target)
            return
        with open(target, "rb") as fh:
            body = fh.read()
        # ETag：页面没变就让浏览器只拿一个 304（几百字节），手机上第二次打开几乎是瞬间
        tag = '"%s"' % hashlib.sha256(body).hexdigest()[:16]
        if (self.headers.get("If-None-Match") or "").strip() == tag:
            self._send(304, b"", "text/html; charset=utf-8",
                       {"ETag": tag, "Cache-Control": "no-cache"})
            self._log(304, "页面未变")
            return
        self._send(200, body, "text/html; charset=utf-8",
                   {"Cache-Control": "no-cache", "ETag": tag})
        self._log(200, "%d B" % len(body))

    # ---------- 接口 ----------
    def _handle_api(self, method, path):
        role = resolve_role(self)
        STATE["_role"] = role

        if path == "/api/whoami":
            if role is None:
                self._fail(401, "需要口令", {"Cache-Control": "no-store"})
                self._log(401, "口令缺失或不匹配")
                return
            self._json(200, {
                "ok": True,
                "role": role,
                "canWrite": role == "owner",
                "publicRead": bool(STATE["public_read"]),
                "serverTime": now_iso(),
            }, {"Cache-Control": "no-store"})
            self._log(200)
            return

        if path == "/api/db":
            if role is None:
                self._fail(401, "需要口令", {"Cache-Control": "no-store"})
                self._log(401, "口令缺失或不匹配")
                return
            if method == "GET":
                try:
                    with LOCK:
                        db = read_db_locked()
                except (OSError, ValueError) as exc:
                    self._fail(500, "服务端读库失败：%s" % exc)
                    self._log(500, str(exc))
                    return
                rev = int(db.get("revision") or 0)
                self._json(200, {"ok": True, "revision": rev, "db": db},
                           {"Cache-Control": "no-store", "X-Revision": str(rev)})
                self._log(200, "rev=%d works=%d" % (rev, len(db.get("works") or [])))
                return

            if method in ("PUT", "POST"):
                if role != "owner":
                    self._fail(403, "只读口令不能修改（PUT 需要 owner 口令）")
                    self._log(403, "guest 尝试写入")
                    return
                body, err = self._read_body()
                if err:
                    self._fail(err, "请求体太大或长度不合法" if err == 413 else "请求体长度不合法")
                    self._log(err)
                    return
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._fail(400, "请求体不是合法 JSON：%s" % exc)
                    self._log(400)
                    return
                if isinstance(payload, dict) and "db" in payload:
                    db = payload.get("db")
                    expected = payload.get("revision")
                else:
                    db = payload
                    expected = self.headers.get("If-Revision")
                if not isinstance(db, dict) or not isinstance(db.get("works"), list):
                    self._fail(400, "请求体里没有 works 数组")
                    self._log(400)
                    return
                expected_i = None
                if expected not in (None, ""):
                    try:
                        expected_i = int(expected)
                    except (TypeError, ValueError):
                        self._fail(400, "revision 必须是整数")
                        self._log(400)
                        return
                try:
                    result, rev = write_db(db, expected_i)
                except OSError as exc:
                    self._fail(500, "服务端写库失败：%s" % exc)
                    self._log(500, str(exc))
                    return
                if result == "conflict":
                    self._fail(409, "库已被别处修改，请重新载入后再保存",
                               {"X-Revision": str(rev), "X-Current-Revision": str(rev)})
                    self._log(409, "期望 rev=%s，实际 rev=%d" % (expected, rev))
                    return
                self._json(200, {"ok": True, "revision": rev}, {"X-Revision": str(rev)})
                self._log(200, "rev=%d works=%d" % (rev, len(db["works"])))
                return

            self._fail(405, "这个接口只支持 GET / PUT")
            self._log(405)
            return

        self._fail(404, "没有这个接口")
        self._log(404)


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def configure(cfg: dict) -> dict:
    data_file = resolve_from_server_dir(cfg["data_file"])
    static_file = resolve_from_server_dir(cfg["static_file"])
    STATE.clear()
    STATE.update(cfg)
    STATE["data_file"] = data_file
    STATE["static_file"] = static_file
    STATE["quiet"] = bool(cfg.get("quiet"))
    STATE["_role"] = None
    return STATE


def bind_error_hint(exc, port: int):
    """绑不上端口时给人话，而不是甩一堆 traceback（WinError 10013 用户踩过）。"""
    winerr = getattr(exc, "winerror", None)
    if winerr == 10013 or getattr(exc, "errno", None) == 13:
        return [
            "Windows 不允许绑定这个端口（WinError 10013）。常见原因：",
            "  1) 这段端口被系统「保留」了 —— 装了 Hyper-V / WSL / Docker 的机器很常见；",
            "  2) 端口被别的程序占着；",
            "  3) 安全软件 / 防火墙拦截了绑定。",
            "怎么办：",
            "  · 最省事：把 server\\config.json 里的 \"port\" 改成别的（推荐 8899），再启动；",
            "  · 看谁占着：netstat -ano | findstr :%d" % port,
            "  · 看保留范围（管理员窗口）：netsh int ipv4 show excludedportrange protocol=tcp",
            "  （双击「启动-作品库.bat」时启动器会自动换一个能用的端口，一般不会走到这里。）",
        ]
    if winerr == 10048 or getattr(exc, "errno", None) in (48, 98, 10048):
        return [
            "这个端口已经被别的程序占用了。",
            "  · 关掉那个程序，或把 server\\config.json 里的 \"port\" 改成别的（推荐 8899）。",
            "  · 看谁占着：netstat -ano | findstr :%d" % port,
        ]
    return [
        "绑定 %d 端口失败：%s" % (port, exc),
        "  · 换一个端口试试：改 server\\config.json 里的 \"port\"（推荐 8899）。",
    ]


def make_server(cfg: dict) -> ThreadingHTTPServer:
    configure(cfg)
    os.makedirs(os.path.dirname(STATE["data_file"]) or ".", exist_ok=True)
    snapshot_locked("start")
    httpd = ThreadingHTTPServer((cfg["bind"], int(cfg["port"])), Handler)
    httpd.daemon_threads = True
    return httpd


def lan_ip() -> str:
    """猜一个局域网 IP（不会真的发包）。"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        if s:
            s.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="作品库服务端")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径（默认 server/config.json）")
    ap.add_argument("--port", type=int, help="覆盖端口")
    ap.add_argument("--bind", help="覆盖监听地址（0.0.0.0 = 局域网可访问）")
    ap.add_argument("--data", help="覆盖数据文件路径")
    ap.add_argument("--static", help="覆盖页面文件路径")
    ap.add_argument("--owner", help="覆盖 owner 口令")
    ap.add_argument("--guest", help="覆盖 guest 口令")
    ap.add_argument("--public-read", action="store_true", help="允许不带口令只读")
    ap.add_argument("--lan-no-token", action="store_true", help="局域网网段内免口令（按 lan_cidrs）")
    ap.add_argument("--quiet", action="store_true", help="不打印每条请求")
    args = ap.parse_args(argv)

    cfg = load_config(args.config if os.path.isfile(args.config) else None)
    for key, val in (("port", args.port), ("bind", args.bind), ("data_file", args.data),
                     ("static_file", args.static), ("owner_token", args.owner),
                     ("guest_token", args.guest)):
        if val:
            cfg[key] = val
    if args.public_read:
        cfg["public_read"] = True
    if args.lan_no_token:
        cfg["lan_no_token"] = True
    cfg["quiet"] = args.quiet

    # 口令为空就生成并写回配置文件；但命令行显式给了口令时不落盘（免得覆盖你的配置）
    persist = None if (args.owner or args.guest) else args.config
    freshly_made = persist is not None and not os.path.isfile(persist)
    generated = ensure_tokens(cfg, persist)
    if freshly_made:
        save_config(cfg, args.config)
        print("已生成配置文件：%s" % args.config)

    if not os.path.isfile(resolve_from_server_dir(cfg["static_file"])):
        print("警告：找不到页面文件 %s" % resolve_from_server_dir(cfg["static_file"]))

    try:
        httpd = make_server(cfg)
    except OSError as exc:
        print("")
        print("=" * 62)
        print("[启动失败] %d 端口用不了：%s" % (int(cfg.get("port") or 7878), exc))
        for line in bind_error_hint(exc, int(cfg.get("port") or 7878)):
            print("  " + line)
        print("=" * 62)
        return 2
    port = httpd.server_address[1]
    ip = lan_ip()
    print("=" * 62)
    print("作品库服务已启动")
    print("  本机    : http://127.0.0.1:%d" % port)
    if cfg["bind"] not in ("127.0.0.1", "localhost"):
        print("  局域网  : http://%s:%d" % (ip, port))
    print("  数据文件: %s" % STATE["data_file"])
    print("  页面文件: %s" % STATE["static_file"])
    print("  只读口令%s" % ("（public_read 已开：不带口令也能只读）" if cfg["public_read"] else "：启动参数或 config.json 里的 guest_token"))
    if generated:
        print("  已生成/更新口令并写回 config.json：")
        print("    owner = %s" % cfg["owner_token"])
        print("    guest = %s" % cfg["guest_token"])
    print("按 Ctrl+C 停止（关掉这个窗口也可以）")
    print("=" * 62)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止…")
    finally:
        httpd.server_close()
        print("已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
