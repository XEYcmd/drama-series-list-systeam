#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Cloudflare 上的一条 AAAA 记录更新成「本机当前的公网 IPv6」。

    python tools\\更新域名记录.py                 # 自动检测 IPv6 并更新
    python tools\\更新域名记录.py --dry-run        # 只看要做什么，不动 DNS
    python tools\\更新域名记录.py --ip 2408:...    # 手动指定地址
    python tools\\更新域名记录.py --name lib.example.com --zone example.com

为什么要它：家里宽带的 IPv6 会随重连变化，手动去面板改太麻烦。
它只做一件事 —— 让  lib.你的域名  始终指向你家现在的 IPv6（灰云 / DNS only，
不经过 Cloudflare 代理，所以手机是直连你家，快）。

token 从哪来：面板 → 我的个人资料 → API 令牌 → 创建令牌 → 模板选
「编辑区域 DNS」→ 区域选你的域名；另外再加一个「区域 → 区域 → 读取」权限。
把生成的令牌存到 网站版\\.cf-api-token.txt（一行，只放本机，别外发）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_ZONE = "example.com"
DEFAULT_NAME = "lib.example.com"
TOKEN_FILE = os.path.join(ROOT, ".cf-api-token.txt")
API_BASE = "https://api.cloudflare.com/client/v4"


def read_token(path):
    """读令牌文件：跳过 # 注释与空行，容忍引号。"""
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().strip('"').strip("'").strip()
            if line and not line.startswith("#"):
                return line
    return None


def detect_ipv6():
    """返回本机公网 IPv6（优先非临时地址）；找不到返回 None。"""
    try:
        out = subprocess.run(["ipconfig", "/all"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=20).stdout.decode("gbk", "replace")
    except Exception:                                             # noqa: BLE001
        return None
    cur, stable, temp = "", [], []
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
        (temp if ("临时" in line or "Temporary" in line) else stable).append(addr)
    if stable:
        return stable[0]
    return temp[0] if temp else None


def api(method, path, token, body=None, base=None):
    """调 Cloudflare API，返回 (ok, data)。"""
    url = (base or API_BASE).rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return True, json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return False, json.loads(raw)
        except ValueError:
            return False, {"errors": [{"message": "HTTP %s: %s" % (exc.code, raw[:200])}]}
    except Exception as exc:                                      # noqa: BLE001
        return False, {"errors": [{"message": "%s: %s" % (type(exc).__name__, exc)}]}


def err_text(data):
    msgs = []
    for e in (data or {}).get("errors") or []:
        msgs.append(str(e.get("message") or e))
    return "；".join(msgs) or "未知错误"


def find_zone(token, zone, base=None):
    ok, data = api("GET", "/zones?name=" + urllib.parse.quote(zone), token, base=base)
    if not ok:
        return None, err_text(data)
    res = (data or {}).get("result") or []
    if not res:
        return None, "这个令牌看不到区域 %s（权限不够？还是域名不在这个账号里？）" % zone
    return res[0].get("id"), None


def find_record(token, zone_id, name, base=None):
    ok, data = api("GET", "/zones/%s/dns_records?type=AAAA&name=%s"
                   % (zone_id, urllib.parse.quote(name)), token, base=base)
    if not ok:
        return None, err_text(data)
    res = (data or {}).get("result") or []
    return (res[0] if res else None), None


def main() -> int:
    ap = argparse.ArgumentParser(description="把一条 AAAA 记录更新成本机当前公网 IPv6")
    ap.add_argument("--zone", default=DEFAULT_ZONE)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--ip", default=None, help="手动指定 IPv6（默认自动检测）")
    ap.add_argument("--token-file", default=TOKEN_FILE)
    ap.add_argument("--api-base", default=API_BASE, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=" * 62)
    print("  更新域名解析：%s  →  本机公网 IPv6" % args.name)
    print("=" * 62)

    ip = args.ip or detect_ipv6()
    if not ip:
        print("  [失败] 没检测到公网 IPv6。检查：光猫/路由是否下发 IPv6、网卡是否拿到 2xxx 地址。")
        return 1
    print("  当前 IPv6 : %s" % ip)

    token = read_token(args.token_file)
    if not token:
        print("  [失败] 没找到 API 令牌文件：%s" % args.token_file)
        print("         建法：面板 → 我的个人资料 → API 令牌 → 创建令牌 →")
        print("         模板「编辑区域 DNS」（区域选 %s），再加一个「区域 → 区域 → 读取」；" % args.zone)
        print("         然后把令牌粘进那个文件（只放本机，别外发）。")
        return 1
    print("  令牌      : 已读取（%d 字符，不外传）" % len(token))

    zone_id, err = find_zone(token, args.zone, args.api_base)
    if err:
        print("  [失败] %s" % err)
        return 2
    rec, err = find_record(token, zone_id, args.name, args.api_base)
    if err:
        print("  [失败] %s" % err)
        return 2

    body = {"type": "AAAA", "name": args.name, "content": ip, "ttl": 60, "proxied": False}
    if rec:
        if rec.get("content") == ip and not rec.get("proxied"):
            print("  DNS 已经是对的（%s，灰云），不用改。" % ip)
            return 0
        if args.dry_run:
            print("  [dry-run] 会把已有记录 %s 改成 %s（灰云）" % (rec.get("content"), ip))
            return 0
        ok, data = api("PUT", "/zones/%s/dns_records/%s" % (zone_id, rec["id"]), token, body, args.api_base)
        what = "已更新"
    else:
        if args.dry_run:
            print("  [dry-run] 会新建记录 %s → %s（灰云）" % (args.name, ip))
            return 0
        ok, data = api("POST", "/zones/%s/dns_records" % zone_id, token, body, args.api_base)
        what = "已新建"
    if not ok:
        print("  [失败] %s" % err_text(data))
        return 2

    print("  %s AAAA 记录（灰云 / TTL 60）" % what)
    print("")
    print("  手机（4G/5G）访问：http://%s:%d" % (args.name, 7878))
    print("  （端口以服务窗口打印的为准；在家 WiFi 还是用局域网地址最快）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
