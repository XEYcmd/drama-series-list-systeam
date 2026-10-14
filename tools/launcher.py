#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动器：双击「启动-作品库.bat」时由它调用。

分工：
  * 启动-作品库.bat  —— 纯 ASCII（找 Python、转发参数、结束前 pause）。
    cmd.exe 按代码页逐行解析批处理，只要里面有中文就可能在别的代码页下变乱码，
    所以那里面一个中文都不放。
  * 本文件（.py，UTF-8）—— 所有中文提示都在这里。接控制台时 Python 走
    Unicode API 输出，永远不会花；被重定向时用系统默认编码，并且把同一个
    编码传给子进程，保证整个日志编码一致。
  * server\\作品库服务.py —— 真正的服务端。

用法（一般由 .bat 调起，也可以直接跑）：
    python tools\\launcher.py
    python tools\\launcher.py --with-tunnel
    python tools\\launcher.py --port 9000
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "server", "作品库服务.py")
CONFIG = os.path.join(ROOT, "server", "config.json")
TUNNEL_NAME = "worklib"
# 把 Cloudflare 面板上那串连接器 token 存到这个文件，就能用 token 方式起隧道
# （面板托管的隧道不需要 cloudflared tunnel login；文件内容是机密，别外发）
TOKEN_FILE = os.path.join(ROOT, "tunnel-token.txt")


def read_tunnel_token():
    """读 tunnel-token.txt：跳过 # 注释与空行，容忍外面包了引号。"""
    if not os.path.isfile(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                return line.strip('"').strip("'").strip()
    except OSError:
        return None
    return None


def find_cloudflared():
    """先找项目里自带的 cloudflared.exe（winget 装不上时的手动方案），再找 PATH。"""
    for cand in (os.path.join(ROOT, "cloudflared.exe"),
                 os.path.join(ROOT, "bin", "cloudflared.exe"),
                 os.path.join(HERE, "cloudflared.exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("cloudflared")


def tunnel_lines(with_tunnel: bool):
    """返回要打印的隧道状态行（顺便按需把隧道拉起来）。"""
    cf = find_cloudflared()
    if not cf:
        return ["  隧道   : 未检测到 cloudflared（只做局域网访问可以不管它）",
                "           要外网访问：winget install --id Cloudflare.cloudflared",
                "           或者把 cloudflared.exe 直接丢进本目录（网站版\\）也能认出来；",
                "           建隧道的步骤见「方案-做成网站.md」第「六之二」节"]
    token = read_tunnel_token()
    if not with_tunnel:
        return ["  隧道   : 已检测到 cloudflared",
                "           需要外网时，改双击「启动-作品库（含外网）.bat」；",
                "           或手动：cloudflared tunnel run " +
                ("--token <token>" if token else TUNNEL_NAME)]
    cmd = [cf, "tunnel", "run", "--token", token] if token else [cf, "tunnel", "run", TUNNEL_NAME]
    try:
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(cmd, creationflags=flags)
    except OSError as exc:
        return ["  隧道   : 启动失败：%s" % exc]
    if token:
        return ["  隧道   : 已在新窗口启动（用 tunnel-token.txt 的 token，面板托管的隧道）",
                "           外网地址 = https://<你在面板上配的子域名>.<你的域名>"]
    return ["  隧道   : 已在新窗口启动（命名隧道 %s）" % TUNNEL_NAME,
            "           外网地址 = https://<你绑定的子域名>.<你的域名>"]


def find_cpolar():
    """找 cpolar.exe（先 PATH，再看常见安装位置）。"""
    w = shutil.which("cpolar")
    if w:
        return w
    for cand in (r"C:\path\to\cpolar.exe",
                 os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "cpolar", "cpolar.exe"),
                 os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "cpolar", "cpolar.exe"),
                 os.path.expanduser(r"~\cpolar\cpolar.exe")):
        if os.path.isfile(cand):
            return cand
    return None


def cpolar_lines(with_cpolar: bool, port: int):
    """cpolar 是国内服务，比走 Cloudflare 快得多（不绕国外）。

    只临时起一条 http 隧道指向本机端口，不动你 cpolar.yml 里已有的隧道。
    """
    exe = find_cpolar()
    if not exe:
        return ["  cpolar  : 没找到 cpolar.exe（只做局域网访问可以不管它）",
                "           装了的话：winget install cpolar，或手动指定路径"]
    if not with_cpolar:
        return ["  cpolar  : 已检测到（国内穿透，手机用它比 Cloudflare 快）",
                "           需要时改双击「启动-作品库（含cpolar）.bat」；",
                "           或手动：cpolar http %d" % port]
    try:
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen([exe, "http", str(port)], creationflags=flags)
    except OSError as exc:
        return ["  cpolar  : 启动失败：%s" % exc]
    return ["  cpolar  : 已在新窗口启动（http 隧道 → 本机 %d 端口）" % port,
            "           手机请打开那个窗口里打印的网址（形如 https://xxxx.cpolar.cn）",
            "           免费版每次重启网址可能会变，以那个窗口打印的为准"]


def ipv6_access_lines(enabled: bool, port: int):
    """手机流量访问：自己的域名 → 家里公网 IPv6 → 本机端口。

    ① IPv6:port 转发到 IPv4 本机（服务只监听 IPv4）
    ② 防火墙放行该端口
    ③ 有 .cf-api-token.txt 就把 lib.<域名> 的 AAAA 记录更新成当前 IPv6
    需要管理员权限（由「启动-作品库（一键）.bat」提权后调用）。
    """
    if not enabled:
        return []
    lines = []
    try:
        subprocess.run(["netsh", "interface", "portproxy", "delete", "v6tov4",
                        "listenport=%d" % port, "listenaddress=::"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        r = subprocess.run(["netsh", "interface", "portproxy", "add", "v6tov4",
                            "listenport=%d" % port, "listenaddress=::",
                            "connectport=%d" % port, "connectaddress=127.0.0.1"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        lines.append("  IPv6 转发: " + ("已开启（IPv6:%d → 127.0.0.1:%d）" % (port, port) if r.returncode == 0
                                        else "设置失败（没提权？或 IP Helper 服务未启动）"))
    except Exception as exc:                                      # noqa: BLE001
        lines.append("  IPv6 转发: 设置失败：%s" % exc)
    try:
        subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule",
                        "name=worklib %d any" % port],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        r2 = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule",
                             "name=worklib %d any" % port, "dir=in", "action=allow",
                             "protocol=TCP", "localport=%d" % port, "profile=any"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        lines.append("  防火墙  : " + ("已放行 %d（所有网络类型）" % port if r2.returncode == 0
                                       else "放行失败（需要管理员）"))
    except Exception:                                             # noqa: BLE001
        lines.append("  防火墙  : 放行失败（需要管理员）")

    dns = os.path.join(HERE, "cf_dns.py")
    token = os.path.join(ROOT, ".cf-api-token.txt")
    if os.path.isfile(dns) and os.path.isfile(token):
        lines.append("  域名    : 正在把 lib.example.com 指向本机当前 IPv6 …")
        try:
            subprocess.call([sys.executable, dns], cwd=ROOT)
        except Exception as exc:                                  # noqa: BLE001
            lines.append("  域名    : 更新失败：%s" % exc)
        lines.append("           手机（4G/5G）：http://lib.example.com:%d" % port)
    else:
        lines.append("  域名    : 还没建 .cf-api-token.txt（建法见「方案-做成网站.md」）")
        lines.append("           建好后重跑本脚本，手机就能用 http://lib.example.com:%d" % port)
    return lines


def port_is_free(host, port):
    """试着绑一下这个端口，判断能不能用（10013 = 被系统保留，10048 = 被占用）。"""
    s = socket.socket()
    try:
        s.bind(("" if host in ("0.0.0.0", "::") else host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def pick_port(host, want, offsets=(0, 1, 2, 3, 11, 12, 101, 1001)):
    """优先用配置里的端口；用不了就往后找一个能用的。

    返回 (实际端口, 是否换了端口)。Windows 上端口可能落在 Hyper-V/WSL/Docker
    预留的"保留端口段"里，这时绑定会报 WinError 10013 —— 与其让用户看 traceback，
    不如自动换一个并在横幅里说清楚。
    """
    for off in offsets:
        p = want + off
        if p > 65535:
            continue
        if port_is_free(host, p):
            return p, off != 0
    return want, False


def main() -> int:
    args = list(sys.argv[1:])

    print("=" * 62)
    print("  作品库 · 正在启动服务")
    print("=" * 62)

    if not os.path.isfile(SERVER):
        print("  [错误] 找不到 %s" % SERVER)
        print("         请确认 server 目录与本启动器在同一个文件夹里（默认：本仓库目录")
        return 2

    # 端口被占用/被系统保留时自动换一个（用户没显式指定 --port 才这么做）
    cfg_path = CONFIG
    for _i, _a in enumerate(args):
        if _a == "--config" and _i + 1 < len(args):
            cfg_path = args[_i + 1]
        elif _a.startswith("--config="):
            cfg_path = _a.split("=", 1)[1]
    cfg = {}
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:                                             # noqa: BLE001
        cfg = {}
    if not any(a == "--port" or a.startswith("--port=") for a in args):
        want = int(cfg.get("port") or 7878)
        host = str(cfg.get("bind") or "0.0.0.0")
        actual, moved = pick_port(host, want)
        if not moved and not port_is_free(host, want):
            print("  [警告] %d 和后面几个备用端口都绑不上。" % want)
            print("         如果每个端口都不行，通常不是「被占用」，而是安全软件/防火墙")
            print("         拦住了程序监听端口 —— 请把 python.exe 或本目录加入白名单。")
            print("-" * 62)
        elif moved:
            args += ["--port", str(actual)]
            print("  [提示] 端口 %d 用不了（被别的程序占用，或被 Windows 保留了这一段），" % want)
            print("         本次自动改用 %d。" % actual)
            print("         想固定下来：把 server\\config.json 里的 \"port\" 改成 %d。" % actual)
            print("         想看是谁占着：netstat -ano | findstr :%d" % want)
            print("-" * 62)

    for line in tunnel_lines("--with-tunnel" in args):
        print(line)
    final_port = int(cfg.get("port") or 7878)
    for _i, _a in enumerate(args):
        if _a == "--port" and _i + 1 < len(args):
            final_port = int(args[_i + 1])
        elif _a.startswith("--port="):
            final_port = int(_a.split("=", 1)[1])
    for line in cpolar_lines("--with-cpolar" in args, final_port):
        print(line)
    for line in ipv6_access_lines("--with-ipv6" in args, final_port):
        print(line)
    args = [a for a in args if a not in ("--with-tunnel", "--with-cpolar", "--with-ipv6")]
    print("  防火墙 : 首次运行 Windows 可能弹窗，请只勾「专用网络」")
    print("  提示   : 关掉这个窗口 = 停止服务")
    print("-" * 62)
    sys.stdout.flush()

    # 让子进程用与本进程相同的输出编码：重定向到文件时整篇日志才不会一半乱码
    env = dict(os.environ)
    if not sys.stdout.isatty() and not env.get("PYTHONIOENCODING"):
        env["PYTHONIOENCODING"] = getattr(sys.stdout, "encoding", None) or "utf-8"

    try:
        return subprocess.call([sys.executable, SERVER] + args, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
