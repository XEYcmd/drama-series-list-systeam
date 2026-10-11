#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印手机（IPv6）该访问的网址。

    python tools\\ipv6url.py [端口]

只报「公网 IPv6」（2xxx/3xxx 开头），并优先给非临时的那个 —— 临时地址过段时间会变。
属于物理网卡的才要，隧道网卡（Teredo/隧道适配器）不算。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def cfg_port() -> int:
    try:
        with open(os.path.join(ROOT, "server", "config.json"), encoding="utf-8") as fh:
            return int(json.load(fh).get("port") or 7878)
    except Exception:                                             # noqa: BLE001
        return 7878


def scan():
    out = subprocess.run(["ipconfig", "/all"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT).stdout.decode("gbk", "replace")
    cur = ""
    stable, temp = [], []
    for line in out.splitlines():
        if re.match(r"^\S*(适配器|adapter)\s", line.strip(), re.I):
            cur = line.strip()
            continue
        if "IPv6" not in line:
            continue
        m = re.search(r"([0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){4,})", line)
        if not m:
            continue
        addr = m.group(1)
        if not addr.lower().startswith(("2", "3")):
            continue
        if re.search(r"隧道|Tunnel|Teredo|isatap", cur, re.I):
            continue
        is_temp = ("临时" in line) or ("Temporary" in line)
        (temp if is_temp else stable).append((cur, addr))
    return stable, temp


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else cfg_port()
    stable, temp = scan()
    print("=" * 62)
    print("  手机（4G/5G 流量）访问地址 —— 走公网 IPv6，不过 Cloudflare")
    print("=" * 62)
    if not stable and not temp:
        print("  没找到公网 IPv6。可能是：光猫/路由没下发、或网卡没拿到 2xxx 地址。")
        print("  可以先用局域网地址，或者改用 Tailscale 之类的方案。")
        return 1
    for _c, a in stable:
        print("  http://[%s]:%d" % (a, port))
    if stable:
        print("  ↑ 这个（非临时地址）是首选，一般能长期用。")
    if temp:
        print("")
        print("  临时地址（隐私扩展，过一段时间会自动变，变了就用上面那条）：")
        for _c, a in temp:
            print("  http://[%s]:%d" % (a, port))
    print("")
    print("  注意：端口 %d 要在防火墙放行；本次用的端口以服务窗口打印的为准。" % port)
    print("  用法：手机浏览器地址栏直接粘贴 http://[…] 这一整串（方括号要带上）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
