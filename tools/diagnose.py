#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局域网访问自检：为什么手机/别的电脑打不开 http://192.168.x.x:7878。

    python tools\\诊断.py            （或双击「诊断-局域网.bat」）

依次检查：
  1. 服务在不在跑、监听在哪个地址（0.0.0.0 = 局域网可达；127.0.0.1 = 只能本机）
  2. 本机有哪些 IP、应该用哪个
  3. Windows 防火墙：各配置文件是否开启、有没有针对 7878 / python 的入站放行规则
  4. 当前网络是「专用」还是「公用」（公用网络默认挡入站，这就是最常见的坑）
最后给出结论和一条可直接粘贴的修复命令。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 7878
RULE_NAME = "worklib 7878"
NOSP = 0x08000000 if os.name == "nt" else 0

try:
    import json
    cfg = json.load(open(os.path.join(ROOT, "server", "config.json"), encoding="utf-8"))
    PORT = int(cfg.get("port") or PORT)
except Exception:                                                 # noqa: BLE001
    cfg = {}


def run(cmd, timeout=25):
    """跑命令并拿回输出（不弹窗）。"""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, creationflags=NOSP)
        for enc in ("gbk", "utf-8"):
            try:
                return p.stdout.decode(enc)
            except UnicodeDecodeError:
                continue
        return p.stdout.decode("utf-8", "replace")
    except Exception as exc:                                      # noqa: BLE001
        return "<<执行失败: %s: %s>>" % (type(exc).__name__, exc)


def hr(title):
    print("")
    print("-" * 62)
    print("  " + title)
    print("-" * 62)


problems = []
tips = []

# --------------------------------------------------------------------------- #
hr("1. 服务是否在运行、监听在哪个地址")

listening_on = []
netstat = run(["netstat", "-ano", "-p", "TCP"])
for line in netstat.splitlines():
    parts = line.split()
    if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
        if parts[1].endswith(":%d" % PORT):
            listening_on.append((parts[1], parts[4]))

if not listening_on:
    print("  ✗ 没有任何程序在监听 %d 端口 —— 服务没在跑。" % PORT)
    print("    （用工具扫端口当然显示「未开放」，因为确实没人监听）")
    problems.append("服务没启动")
    tips.append("先双击「启动-作品库.bat」，让那个黑窗口开着，再重试。")
else:
    for addr, pid in listening_on:
        print("  ✓ 正在监听：%s  （进程 PID %s）" % (addr, pid))
    if any(a.startswith("0.0.0.0") for a, _ in listening_on):
        print("    → 监听在 0.0.0.0，局域网可达 ✓")
    elif any(a.startswith("127.0.0.1") or a.startswith("[::1]") for a, _ in listening_on):
        print("    → 只监听 127.0.0.1，**只有本机能访问**，局域网肯定连不上 ✗")
        problems.append("只绑定本机回环地址")
        tips.append('把 server\\config.json 里的 "bind" 改成 "0.0.0.0" 后重启服务。')

try:
    with urllib.request.urlopen("http://127.0.0.1:%d/" % PORT, timeout=5) as r:
        print("  ✓ 本机访问 http://127.0.0.1:%d/ 返回 %s（%d 字节）" % (PORT, r.status, len(r.read())))
except Exception as exc:                                          # noqa: BLE001
    print("  ✗ 本机访问失败：%s" % exc)
    problems.append("本机都连不上")

# --------------------------------------------------------------------------- #
hr("2. 本机 IP（手机要用下面某个地址，且手机要跟它同一个网段）")

ips = []
out = run(["ipconfig"])
for m in re.finditer(r"(IPv4[^:]*|IPv4 Address[^:]*):\s*([0-9.]+)", out):
    ips.append(m.group(2))
lan_ips = [ip for ip in ips if not ip.startswith("127.")]

if lan_ips:
    for ip in lan_ips:
        print("  %-16s  →  http://%s:%d" % (ip, ip, PORT))
else:
    print("  没找到局域网 IP（网线/Wi-Fi 没连？）")
    problems.append("没有局域网 IP")

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    print("  （默认出口网卡：%s）" % s.getsockname()[0])
    s.close()
except OSError:
    pass

# --------------------------------------------------------------------------- #
hr("3. Windows 防火墙")

fw = run(["netsh", "advfirewall", "show", "allprofiles", "state"])
profiles = {}
cur = None
for line in fw.splitlines():
    if re.search(r"配置文件|Profile Settings|配置文件设置", line):
        m = re.search(r"(域|专用|公用|Domain|Private|Public)", line)
        cur = m.group(1) if m else cur
    m2 = re.search(r"(状态|State)\s*[:：]?\s*(打开|关闭|ON|OFF)", line, re.I)
    if m2 and cur:
        profiles[cur] = m2.group(2).upper()
if profiles:
    for k, v in profiles.items():
        print("  %-14s 防火墙：%s" % (k, v))
else:
    print("  读不到防火墙状态（这条需要管理员权限，不是故障）：")
    print("  " + fw.strip().replace("\n", "\n  ")[:280])
    print("  想细看：右键「诊断-局域网.bat」→ 以管理员身份运行；")
    print("  或者干脆双击「开放防火墙-7878.bat」，它会自己提权并把 7878 放行。")

rules = run(["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in"], timeout=60)
has_port_rule = False
has_python_rule = False
rule_profiles = set()
for block in rules.split("\n\n"):
    if ("7878" in block) or re.search(r"python", block, re.I):
        if re.search(r"(允许|Allow)", block, re.I) and re.search(r"(入站|Inbound)", block, re.I):
            if "7878" in block:
                has_port_rule = True
            if re.search(r"python", block, re.I):
                has_python_rule = True
            m = re.search(r"(配置文件|Profiles?)\s*[:：]\s*(.+)", block, re.I)
            if m:
                rule_profiles.add(m.group(2).strip())

print("  已有针对 7878 的入站放行规则：%s" % ("有 ✓" if has_port_rule else "没有"))
print("  已有针对 python 的入站放行规则：%s" % ("有" if has_python_rule else "没有"))
if rule_profiles:
    print("  这些规则适用的网络类型：%s" % "、".join(sorted(rule_profiles)))

# --------------------------------------------------------------------------- #
hr("4. 当前 Wi-Fi/网线属于「专用」还是「公用」")

conn = run(["powershell", "-NoProfile", "-Command",
            "Get-NetConnectionProfile | Select-Object -Property Name,InterfaceAlias,NetworkCategory | Format-Table -AutoSize | Out-String"])
if "执行失败" in conn or not conn.strip():
    conn = run(["netsh", "advfirewall", "show", "currentprofile"])
cat = conn
print("  " + conn.strip().replace("\n", "\n  ")[:500])
if re.search(r"Public|公用", cat) and not re.search(r"Private|专用", cat):
    problems.append("网络被当作「公用」")
    tips.append("把 Wi-Fi 改成「专用网络」：设置 → 网络和 Internet → Wi-Fi/WLAN → 你的网络 → 网络配置文件类型 → 专用")

# --------------------------------------------------------------------------- #
hr("结论")

if not problems:
    print("  没发现明显问题。如果手机还是打不开，请依次确认：")
    print("    1) 手机连的是**同一个 Wi-Fi**（不是流量、不是访客网络/独立网段）")
    print("    2) 输入的地址是上面第 2 节里那个（带端口 %d）" % PORT)
    print("    3) 手机上装的安全软件/路由器「AP 隔离」没挡住设备互访")
else:
    print("  发现 %d 个问题：" % len(problems))
    for i, p in enumerate(problems, 1):
        print("    %d) %s" % (i, p))
    print("")
    print("  建议按顺序处理：")
    for i, tip in enumerate(tips, 1):
        print("    %d) %s" % (i, tip))

print("")
print("  防火墙一键修复（**管理员**窗口里跑，或双击「开放防火墙-7878.bat」）：")
print('    netsh advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d profile=private' % (RULE_NAME, PORT))
print("")
print("  如果上面那条加完还不行，而你的网络被判定为「公用」，可临时放宽到所有网络类型")
print("  （安全性差一些，只建议在家用：profile=any）")
print("")
