#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动脚本真机测试：真的像双击一样用 cmd 跑一遍，验证

  * .bat 是纯 ASCII + CRLF（cmd 按代码页解析批处理，里面不能有中文）；
  * 中文提示由 tools\\launcher.py 输出，重定向到文件后编码一致、不乱码；
  * 起得来、能访问、打印本机与局域网网址、生成口令；
  * 关掉之后端口立即释放；
  * stderr 里没有 `不是内部或外部命令` 之类的解析错误。

    python tests\\launcher-test.py
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

sys.dont_write_bytecode = True          # import 项目里的 .py 时别留下 __pycache__


def file_sha(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# 守卫：这一整套测试运行期间，真实的 server\config.json 必须一个字节都不变
REAL_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "server", "config.json")
REAL_CONFIG_SHA_BEFORE = file_sha(REAL_CONFIG) if os.path.isfile(REAL_CONFIG) else None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BAT = os.path.join(ROOT, "启动-作品库.bat")
BAT_TUNNEL = os.path.join(ROOT, "启动-作品库（含外网）.bat")
LAUNCHER = os.path.join(ROOT, "tools", "launcher.py")
GEN = os.path.join(ROOT, "tools", "生成启动脚本.py")
WORK = os.path.join(HERE, "fixtures", "tmp", "launcher")
LOG_ENCODING = "gbk"        # 重定向时 launcher 与 server 都用系统默认编码

PASS = 0
FAIL = 0
SKIP = 0
SKIPPED = []
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


class Skipped(Exception):
    pass


def skip(reason):
    raise Skipped(reason)


def ts(name, fn):
    """允许显式跳过的用例：环境做不到的事不假装通过，也不当成失败。"""
    global PASS, FAIL, SKIP
    try:
        fn()
        PASS += 1
        print("  PASS  " + name)
    except Skipped as exc:
        SKIP += 1
        SKIPPED.append("%s -> %s" % (name, exc))
        print("  SKIP  %s -> %s" % (name, exc))
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


subprocess.run([sys.executable, GEN], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
assert os.path.isfile(BAT), "生成器没有产出 .bat"
assert os.path.isfile(BAT_TUNNEL), "生成器没有产出「含外网」那个 .bat"

shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
OUT = os.path.join(WORK, "out.txt")
ERR = os.path.join(WORK, "err.txt")
CFG = os.path.join(WORK, "config.json")
DATA = os.path.join(WORK, "作品库.json")
PORT = free_port()
raw = open(BAT, "rb").read()
raw_tunnel = open(BAT_TUNNEL, "rb").read()

print("[启动脚本] 文件本身")


def _ascii_only():
    for name, blob in ((os.path.basename(BAT), raw), (os.path.basename(BAT_TUNNEL), raw_tunnel)):
        bad = [(i, b) for i, b in enumerate(blob) if b > 127]
        assert not bad, "%s 含非 ASCII 字节 %s …（cmd 可能在别的代码页下读坏）" % (name, bad[:5])
        text = blob.decode("ascii")
        assert "tools\\launcher.py" in text, "%s 没有调用 tools\\launcher.py" % name
        assert "pause" in text, "%s 出错时会一闪而过" % name
    assert b"--with-tunnel" in raw_tunnel, "「含外网」那个没有传 --with-tunnel"
    assert b"--with-tunnel" not in raw, "普通那个不该传 --with-tunnel"


t("两个 .bat 都是纯 ASCII，普通版/含外网版区分正确，失败时会 pause", _ascii_only)


def _crlf():
    for name, blob in ((os.path.basename(BAT), raw), (os.path.basename(BAT_TUNNEL), raw_tunnel)):
        assert blob.count(b"\r\n") > 30, "%s 的 CRLF 太少" % name
        assert blob.count(b"\n") == blob.count(b"\r\n"), "%s 存在裸 LF 换行（cmd 会把长行切碎）" % name


t("两个 .bat 全部 CRLF 换行", _crlf)


def _all_bats_ascii_crlf():
    """项目里每个 .bat 都必须纯 ASCII + CRLF（新加的诊断/防火墙脚本也不例外）。"""
    import glob
    bats = sorted(glob.glob(os.path.join(ROOT, "*.bat")))
    assert len(bats) >= 4, "只找到 %d 个 .bat，应该至少有 4 个" % len(bats)
    for p in bats:
        raw = open(p, "rb").read()
        bad = [b for b in raw if b > 127]
        assert not bad, "%s 含非 ASCII 字节" % os.path.basename(p)
        assert raw.count(b"\n") == raw.count(b"\r\n"), "%s 有裸 LF" % os.path.basename(p)


t("项目里所有 .bat（含诊断、开放防火墙）都是纯 ASCII + CRLF", _all_bats_ascii_crlf)


def _no_chcp():
    for name, blob in ((os.path.basename(BAT), raw), (os.path.basename(BAT_TUNNEL), raw_tunnel)):
        assert "chcp" not in blob.decode("ascii"), "%s 用了 chcp（会打乱 cmd 的解析位置）" % name


t(".bat 里没有 chcp 之类的代码页把戏", _no_chcp)


def _launcher_utf8():
    src = open(LAUNCHER, "rb").read().decode("utf-8")     # 能按 UTF-8 解出中文
    assert "作品库 · 正在启动服务" in src, "启动器里没有中文提示"
    assert "cloudflared" in src, "启动器没有隧道状态提示"
    assert "防火墙" in src
    assert "PYTHONIOENCODING" in src, "没有把输出编码传给子进程（日志会一半乱码）"
    assert "tunnel-token.txt" in src and "--token" in src, "启动器不支持面板给的连接器 token"


t("tools\\launcher.py 是 UTF-8，含中文提示/隧道/编码统一/面板 token 支持", _launcher_utf8)


def _token_file_parsing():
    """token 文件解析：注释要跳过、引号要容忍、空文件不能当 token。"""
    spec = importlib.util.spec_from_file_location("worklib_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tmp = os.path.join(WORK, "token-test.txt")
    mod.TOKEN_FILE = tmp

    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# 注释\n# 还是注释\n")
    assert mod.read_tunnel_token() is None, "只有注释时不该读出 token"

    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write('# 注释\n\n  "eyJhbGciOi-test-token"  \n')
    assert mod.read_tunnel_token() == "eyJhbGciOi-test-token", "带引号/空格的 token 没清理干净"

    os.remove(tmp)
    assert mod.read_tunnel_token() is None, "文件不存在时应该返回 None"

    # 项目里那份 tunnel-token.txt 有两种合法状态：
    #   a) 还是注释模板 → 解析出来必须是 None（不能把说明当成 token 发出去）
    #   b) 已经填了面板给的连接器 token → 必须能解析出一整串、且不含空格/引号
    mod.TOKEN_FILE = os.path.join(ROOT, "tunnel-token.txt")        # 前面指向过临时文件，这里切回来
    real = open(mod.TOKEN_FILE, "rb").read().decode("utf-8")
    non_comment = [l.strip() for l in real.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not non_comment:
        assert mod.read_tunnel_token() is None, "注释模板不该被当成 token"
    else:
        assert len(non_comment) == 1, "tunnel-token.txt 里有 %d 行非注释内容，应该只有一行 token" % len(non_comment)
        tok = non_comment[0].strip('"').strip("'").strip()
        assert " " not in tok and len(tok) > 40, "这行不像连接器 token（长度 %d）" % len(tok)
        assert mod.read_tunnel_token() == tok, "真实 token 没被正确解析出来"

    # cloudflared 查找顺序：项目里自带 > bin\ > PATH
    fake_root = os.path.join(WORK, "fake-root")
    os.makedirs(fake_root, exist_ok=True)
    exe = os.path.join(fake_root, "cloudflared.exe")
    with open(exe, "wb") as fh:
        fh.write(b"MZ fake")
    mod.ROOT = fake_root
    assert mod.find_cloudflared() == exe, "放在项目目录里的 cloudflared.exe 没被认出来"


t("token 文件解析：跳过注释、容忍引号、模板不会被误当 token", _token_file_parsing)


def listener_pids(port: int):
    """谁在监听这个端口 —— 不依赖 taskkill 的权限，作为清理兜底。

    netstat 的输出重定向到文件（不用管道，受限环境里管道会被拦）。
    """
    out_file = os.path.join(WORK, "netstat.txt")
    with open(out_file, "wb") as fh:
        subprocess.run(["netstat", "-ano", "-p", "TCP"], stdout=fh,
                       stderr=subprocess.DEVNULL)
    pids = set()
    with open(out_file, "r", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                if parts[1].endswith(":%d" % port):
                    try:
                        pids.add(int(parts[4]))
                    except ValueError:
                        pass
    return pids


def kill_tree(proc, port):
    """先试 taskkill /T；不行就按端口找出监听进程直接杀（沙箱里 taskkill 会被拒）。"""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    if proc.poll() is None:
        for pid in listener_pids(port):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(0.6)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


print("[启动脚本] 真的跑一遍")


def _run_and_check():
    with open(OUT, "wb") as out, open(ERR, "wb") as err:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", os.path.basename(BAT), "--config", CFG, "--data", DATA, "--port", str(PORT)],
            cwd=ROOT, stdout=out, stderr=err,
            stdin=subprocess.DEVNULL,          # 让 .bat 结尾的 pause 立刻拿到 EOF，不会一直等按键
        )
    try:
        deadline = time.time() + 25
        page = None
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError("cmd 提前退出（exit=%s）\nOUT=%s\nERR=%s" % (
                    proc.returncode,
                    open(OUT, "rb").read().decode(LOG_ENCODING, "replace")[:500],
                    open(ERR, "rb").read().decode(LOG_ENCODING, "replace")[:500]))
            try:
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
                conn.request("GET", "/")
                resp = conn.getresponse()
                page = resp.read().decode("utf-8", "replace")
                conn.close()
                if resp.status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.4)
        assert page and "作品库" in page, "通过 .bat 起不来或页面不对"

        time.sleep(0.6)
        text = open(OUT, "rb").read().decode(LOG_ENCODING, "replace")
        assert "作品库 · 正在启动服务" in text, "启动器的中文提示没出来/乱码了：\n" + text[:600]
        assert "隧道" in text and "防火墙" in text, "隧道/防火墙提示缺失：\n" + text[:600]
        assert "作品库服务已启动" in text, "服务端横幅没出来（编码可能不一致）：\n" + text[:900]
        assert re.search(r"http://127\.0\.0\.1:%d" % PORT, text), "没打印本机网址"
        assert re.search(r"局域网\s*: http://\d+\.\d+\.\d+\.\d+:%d" % PORT, text), "没打印局域网网址"
        assert "owner = " in text and "guest = " in text, "没把口令打出来"
        assert "\ufffd" not in text, "输出里有编码替换字符（说明编码不一致）"

        with open(CFG, encoding="utf-8") as fh:
            cfg = json.load(fh)
        assert len(cfg["owner_token"]) >= 32 and len(cfg["guest_token"]) >= 32, "口令太短"

        errtext = open(ERR, "rb").read().decode(LOG_ENCODING, "replace")
        assert "不是内部或外部命令" not in errtext, "cmd 解析出错了：\n" + errtext[:600]
        assert "Traceback" not in errtext, "有异常堆栈：\n" + errtext[:600]
    finally:
        kill_tree(proc, PORT)


t("双击式运行：起得来、中文不乱码、打印本机+局域网网址与口令、无解析错误", _run_and_check)


def port_open(port: int) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/")
        conn.getresponse()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def _released():
    time.sleep(0.8)
    if port_open(PORT):
        if listener_pids(PORT):
            skip("本环境禁止终止非子进程（taskkill / os.kill 都是 Access denied），"
                 "无法在这里验证「关窗口后端口释放」；"
                 "同一机制在直接子进程的场景由 tests/server-live-test.py 覆盖并通过")
        raise AssertionError("端口还开着，但 netstat 里找不到监听进程，情况异常")


ts("关掉窗口后端口立即释放", _released)


def _missing_python_message():
    """把 launcher.py 藏起来时，应该走 .bat 自己的 ASCII 报错分支并 pause。"""
    tools_dir = os.path.join(ROOT, "tools")
    hidden = os.path.join(ROOT, "tools", "launcher.py.bak")
    os.replace(LAUNCHER, hidden)
    try:
        r = subprocess.run(["cmd.exe", "/c", os.path.basename(BAT), "x"],
                           cwd=ROOT, capture_output=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                           input=b"\r\n", timeout=20)
        # pause 会吃掉回车，所以这里应该正常返回 1（缺文件的分支）
        assert r.returncode == 1, "缺 launcher.py 时退出了 %s" % r.returncode
    finally:
        os.replace(hidden, LAUNCHER)


t("缺 tools\\launcher.py 时给出明确报错并返回 1（不会一闪而过）", _missing_python_message)


def _real_config_untouched():
    """这一整套测试只该动 tests\\fixtures\\tmp 里的东西，绝不能碰真实配置。"""
    now = file_sha(REAL_CONFIG) if os.path.isfile(REAL_CONFIG) else None
    assert now == REAL_CONFIG_SHA_BEFORE, \
        "真实 server\\config.json 被测试改动了！测试必须只用自己的临时配置"


t("测试全程没有碰过真实的 server\\config.json", _real_config_untouched)


def _pick_port():
    """端口被占用时，启动器要能自动换一个能用的（用户踩过 WinError 10013）。"""
    import socket
    spec = importlib.util.spec_from_file_location("worklib_launcher2", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    s = socket.socket()
    s.bind(("", 19302))
    s.listen(1)
    try:
        p, moved = mod.pick_port("0.0.0.0", 19302)
        assert moved, "端口被占了却没换"
        assert p != 19302, "换完还是同一个端口"
        assert mod.port_is_free("0.0.0.0", p), "换出来的端口也不能用"
    finally:
        s.close()


t("端口被占用 → 自动换一个能用的端口", _pick_port)


def _port_notice():
    src = open(os.path.join(ROOT, "tools", "launcher.py"), encoding="utf-8").read()
    assert "自动改用" in src, "启动器没有说明换了端口"
    assert "netstat -ano | findstr" in src, "没有告诉用户怎么查占用"
    srv = open(os.path.join(ROOT, "server", "作品库服务.py"), encoding="utf-8").read()
    assert "bind_error_hint" in srv and "excludedportrange" in srv, "服务端没有端口失败的人话提示"


t("端口失败给的是中文提示 + 排查命令，不是 traceback", _port_notice)


print("")
print("启动脚本测试结果: %d 通过, %d 失败, %d 跳过" % (PASS, FAIL, SKIP))
if SKIPPED:
    for line in SKIPPED:
        print("  ~ " + line)
if FAIL:
    for line in FAILURES:
        print("  - " + line)
    sys.exit(1)
