#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务端真机冒烟测试：按用户会用的那条命令真的把服务起起来，再从外面用 HTTP 打一遍。

    python tests\\server-live-test.py

和 server-test.py 的区别：那个是把 handler 起在进程内测逻辑，这个测的是
「命令行能不能正常启动、能不能被浏览器访问、关掉之后端口有没有释放」。

子进程的 stdout/stderr 重定向到文件（不用管道），以便在受限环境里也能跑。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_DIR = os.path.join(ROOT, "server")
SERVER_PY = os.path.join(SERVER_DIR, "作品库服务.py")
PAGE_PY = os.path.join(ROOT, "作品库.html")
WORK = os.path.join(HERE, "fixtures", "tmp", "server-live")

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


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get(port, target, token=None, timeout=5):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"X-Worklib-Token": token} if token else {}
        conn.request("GET", target, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def put(port, body, token=None, timeout=5):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Worklib-Token"] = token
        conn.request("PUT", "/api/db", body=json.dumps(body).encode("utf-8"), headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
CONFIG = os.path.join(WORK, "config.json")
DATA = os.path.join(WORK, "作品库.json")
OUT = os.path.join(WORK, "stdout.txt")
ERR = os.path.join(WORK, "stderr.txt")
PORT = free_port()

with open(OUT, "w", encoding="utf-8") as out, open(ERR, "w", encoding="utf-8") as err:
    proc = subprocess.Popen(
        [sys.executable, SERVER_PY, "--config", CONFIG, "--port", str(PORT),
         "--data", DATA, "--static", PAGE_PY],
        cwd=SERVER_DIR, stdout=out, stderr=err,
    )

print("[真机冒烟] 已启动进程 pid=%d，端口 %d" % (proc.pid, PORT))


def wait_up(timeout=25):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("服务进程提前退出了（exit=%s），见 %s" % (proc.returncode, ERR))
        try:
            st, body = get(PORT, "/", timeout=2)
            if st == 200:
                return body
        except OSError as exc:
            last = exc
        time.sleep(0.4)
    raise AssertionError("等不到服务起来：%s" % last)


try:
    page = wait_up()


    def _serves_page():
        assert "作品库" in page and "<script" in page, "拿到的不是页面"


    t("命令行启动后，本机能 GET 到页面", _serves_page)


    def _banner():
        text = open(OUT, encoding="utf-8").read()
        assert "作品库服务已启动" in text, "启动横幅没打出来：%r" % text[:200]
        assert "127.0.0.1:%d" % PORT in text, "没打印本机地址"
        assert "局域网" in text, "没打印局域网地址（bind=0.0.0.0 时应该有）"
        assert "owner = " in text and "guest = " in text, "没把生成的口令打出来"


    t("启动横幅：本机地址、局域网地址、两个口令都有", _banner)


    def _config_generated():
        assert os.path.isfile(CONFIG), "没有生成 config.json"
        with open(CONFIG, encoding="utf-8") as fh:
            cfg = json.load(fh)
        assert len(cfg["owner_token"]) >= 32 and len(cfg["guest_token"]) >= 32, "口令太短"
        assert cfg["owner_token"] != cfg["guest_token"]


    t("首次启动自动生成 config.json 与两串强口令", _config_generated)

    with open(CONFIG, encoding="utf-8") as fh:
        CFG = json.load(fh)
    OWNER = CFG["owner_token"]
    GUEST = CFG["guest_token"]


    def _auth():
        st, _ = get(PORT, "/api/db")
        assert st == 401, "没口令竟然能读：%s" % st
        st2, _ = get(PORT, "/api/db", token=GUEST)
        assert st2 == 200, "guest 口令读不了：%s" % st2
        st3, _ = get(PORT, "/api/whoami", token=OWNER)
        assert st3 == 200


    t("从网络进来：无口令 401、guest 能读、owner 能进", _auth)


    DEF_DB = {"works": [{"uuid": "xey0192f0d4-1a2b-7c3d-8e4f-5a6b7c8d9e0fcmd",
                         "name": "葬送的芙莉莲", "aliases": ["Frieren"], "note": "", "addedAt": "x"}],
              "discarded": [], "settings": {"insertByRule": True}}


    def _write_and_persist():
        st, body = put(PORT, {"db": DEF_DB, "revision": 0}, token=OWNER)
        assert st == 200, "owner 写入失败：%s / %s" % (st, body)
        assert json.loads(body)["revision"] == 1
        assert os.path.isfile(DATA), "数据没落盘"
        with open(DATA, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        assert on_disk["works"][0]["name"] == "葬送的芙莉莲", "落盘内容不对"
        st2, _ = put(PORT, {"db": DEF_DB, "revision": 0}, token=GUEST)
        assert st2 == 403, "guest 竟然写成功了：%s" % st2


    t("通过 HTTP 写入能落盘，guest 写入被 403 挡住", _write_and_persist)


    def _snapshot_dir():
        # 第一次写入前文件还不存在，无从快照；再写一次，这次必须留下「写之前」的快照
        st, body = put(PORT, {"db": DEF_DB, "revision": 1}, token=OWNER)
        assert st == 200, "第二次写入失败：%s / %s" % (st, body)
        folder = os.path.join(WORK, "快照")
        files = [f for f in os.listdir(folder)] if os.path.isdir(folder) else []
        assert any("before-write" in f for f in files), "没有写前快照：%s" % files
        newest = sorted(files, key=lambda f: os.path.getmtime(os.path.join(folder, f)))[-1]
        with open(os.path.join(folder, newest), encoding="utf-8") as fh:
            snap = json.load(fh)
        assert snap.get("revision") == 1, "快照里应该是写之前的 rev1，实际 %s" % snap.get("revision")


    t("写前快照存的是上一版好数据（rev1）", _snapshot_dir)

finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _port_released():
    time.sleep(0.6)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
        conn.request("GET", "/")
        conn.getresponse()
        raise AssertionError("关掉服务后端口还通着，没释放")
    except (ConnectionRefusedError, OSError) as exc:
        assert not isinstance(exc, AssertionError), exc


t("关掉服务后端口立即释放（随用随开的关键）", _port_released)


def _stderr_clean():
    text = open(ERR, encoding="utf-8").read().strip()
    assert "Traceback" not in text, "服务端有异常输出：\n%s" % text


t("服务端运行期间没有异常堆栈", _stderr_clean)

print("")
print("真机冒烟结果: %d 通过, %d 失败" % (PASS, FAIL))
if FAIL:
    for line in FAILURES:
        print("  - " + line)
    sys.exit(1)
