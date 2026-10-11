#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""用 Cloudflare API 自动把隧道建好 —— 代替面板上十几次点击。

你只需要一次性准备一个 **API 令牌**，权限（中文界面 → 英文）：
    账户 → Cloudflare Tunnel → 编辑        (Account → Cloudflare Tunnel → Edit)
    区域 → DNS → 编辑                      (Zone → DNS → Edit)
    区域 → 区域 → 读取                     (Zone → Zone → Read)   ← 用来把域名换成 Zone ID
把令牌存到本目录的 `.cf-api-token.txt`（一行，只放令牌）。**本脚本只在本机读它，不会外传。**

它会自动做四件事（原本要你在面板上点十几次）：
    1. 校验令牌
    2. 找到（没有就新建）名为 worklib 的隧道
    3. 取「连接器 token」写进 tunnel-token.txt     ← 原本要你手动复制粘贴
    4. 设好公共主机名（入口规则）+ 建好 DNS 记录（CNAME → <隧道id>.cfargotunnel.com，已代理）

跑完只需双击「启动-作品库（含外网）.bat」。

常用参数：
    --hostname worklib.example.com   指定网址（默认 = 域名的根，即 example.com）
    --zone example.com               指定域名（默认就是它）
    --tunnel-name worklib               隧道名字
    --target http://127.0.0.1:7878      本地服务地址
    --account <id>                      账号 ID（默认用你面板 URL 里那串）
    --dry-run                           只打印将要做什么，不真的改
"""

from __future__ import annotations

import argparse
import json
import os
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
TOKEN_FILE = os.path.join(ROOT, ".cf-api-token.txt")
TUNNEL_TOKEN_FILE = os.path.join(ROOT, "tunnel-token.txt")
API_BASE_DEFAULT = "https://api.cloudflare.com/client/v4"

# 你面板 URL 里那串（dash.cloudflare.com/<这里>/example.com/...）
DEFAULT_ACCOUNT = "YOUR_CLOUDFLARE_ACCOUNT_ID"
DEFAULT_ZONE = "example.com"

STEP = [0]


def say(msg=""):
    print(msg, flush=True)


def step(title):
    STEP[0] += 1
    say("")
    say("[%d] %s" % (STEP[0], title))


def read_secret_line(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                return line.strip('"').strip("'").strip()
    return None


def api(method, path, token, base, body=None, timeout=30):
    """返回 (状态码, 解析后的 JSON)。网络错误返回 (0, {...})。"""
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}
    except Exception as exc:                                       # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(exc).__name__, exc)}


def api_errors(obj):
    errs = obj.get("errors") or []
    if not errs and obj.get("raw"):
        return obj["raw"][:400]
    return "；".join("%s(%s)" % (e.get("message", "?"), e.get("code", "?")) for e in errs) or "（无错误信息）"


def write_tunnel_token(path, value):
    """写 tunnel-token.txt：保留开头的说明注释，只替换/追加 token 行（原文件备份为 .bak）。"""
    lines = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        if not os.path.isfile(path + ".bak"):
            with open(path + ".bak", "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
    kept = [l for l in lines if (not l.strip()) or l.startswith("#")]
    if not kept:
        kept = ["# Cloudflare 隧道的连接器 token（本行以下第一个非注释行就是它）"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + "\n" + value + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="用 Cloudflare API 自动建隧道")
    ap.add_argument("--account", default=DEFAULT_ACCOUNT)
    ap.add_argument("--zone", default=DEFAULT_ZONE)
    ap.add_argument("--hostname", default="")
    ap.add_argument("--tunnel-name", default="worklib")
    ap.add_argument("--target", default="http://127.0.0.1:7878")
    ap.add_argument("--api-base", default=API_BASE_DEFAULT)
    ap.add_argument("--token", default="")
    ap.add_argument("--token-file", default=TOKEN_FILE)
    ap.add_argument("--tunnel-token-file", default=TUNNEL_TOKEN_FILE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hostname = args.hostname or args.zone

    say("=" * 62)
    say("  作品库 · 自动创建 Cloudflare 隧道")
    say("=" * 62)
    say("  域名    : %s" % args.zone)
    say("  网址    : https://%s" % hostname)
    say("  隧道名  : %s" % args.tunnel_name)
    say("  本地服务: %s" % args.target)
    say("  接口    : %s" % args.api_base)
    if args.dry_run:
        say("  （--dry-run：只演示，不会真的改）")

    token = args.token or read_secret_line(args.token_file)
    if not token:
        say("")
        say("  [错误] 没找到 API 令牌。")
        say("         请在 Cloudflare 面板建一个令牌（权限见本文件开头的说明），")
        say("         存到 %s" % args.token_file)
        say("         或者直接：python tools\\创建隧道.py --token <你的令牌>")
        return 2

    # ---- 1. 校验令牌 ----
    step("校验 API 令牌")
    status, obj = api("GET", "/user/tokens/verify", token, args.api_base)
    if status != 200 or not obj.get("success"):
        say("  失败：HTTP %s  %s" % (status, api_errors(obj)))
        say("  提示：401/403 一般是令牌没勾对权限，或复制时少了字符。")
        return 3
    say("  OK（令牌状态：%s）" % (obj.get("result") or {}).get("status", "?"))

    # ---- 2. 找 Zone ID ----
    step("把域名换成 Zone ID（需要「区域 → 区域 → 读取」）")
    status, obj = api("GET", "/zones?name=" + urllib.parse.quote(args.zone), token, args.api_base)
    if status == 200 and obj.get("success") and obj.get("result"):
        zone_id = obj["result"][0]["id"]
        say("  OK  %s -> %s" % (args.zone, zone_id))
    else:
        say("  失败：HTTP %s  %s" % (status, api_errors(obj)))
        say("  提示：给令牌加一条「区域 → 区域 → 读取」，或在面板「概述」右下角复制 Zone ID 后用 --zone-id 传入。")
        say("        （若你只想手动加公共主机名，也可以跳过脚本的第 4 步，我另给步骤。）")
        return 4

    # ---- 3. 隧道：有就复用，没有就新建 ----
    step("查找 / 新建隧道「%s」（需要「账户 → Cloudflare Tunnel → 编辑」）" % args.tunnel_name)
    tunnel_id = None
    status, obj = api("GET", "/accounts/%s/cfd_tunnel?name=%s&is_deleted=false"
                      % (args.account, urllib.parse.quote(args.tunnel_name)), token, args.api_base)
    if status == 200 and obj.get("success"):
        for item in obj.get("result") or []:
            if item.get("name") == args.tunnel_name and not item.get("deleted_at"):
                tunnel_id = item["id"]
                say("  已存在，复用：%s" % tunnel_id)
                break
    elif status:
        say("  查询失败：HTTP %s  %s" % (status, api_errors(obj)))

    if not tunnel_id:
        if args.dry_run:
            say("  （dry-run）将新建隧道 %s" % args.tunnel_name)
            tunnel_id = "DRYRUN-TUNNEL-ID"
        else:
            status, obj = api("POST", "/accounts/%s/cfd_tunnel" % args.account, token, args.api_base,
                              {"name": args.tunnel_name, "config_src": "cloudflare"})
            if status != 200 or not obj.get("success"):
                say("  新建失败：HTTP %s  %s" % (status, api_errors(obj)))
                return 5
            tunnel_id = obj["result"]["id"]
            say("  已新建：%s" % tunnel_id)

    # ---- 4. 取连接器 token，写进 tunnel-token.txt ----
    step("取连接器 token 并写入 tunnel-token.txt")
    connector = None
    if args.dry_run:
        say("  （dry-run）将写入一个假 token")
        connector = "DRYRUN-CONNECTOR-TOKEN"
    else:
        status, obj = api("GET", "/accounts/%s/cfd_tunnel/%s/token" % (args.account, tunnel_id),
                          token, args.api_base)
        if status != 200 or not obj.get("success"):
            say("  获取失败：HTTP %s  %s" % (status, api_errors(obj)))
            return 6
        connector = obj["result"]
    if args.dry_run:
        say("  （dry-run）会写到 %s" % args.tunnel_token_file)
    else:
        write_tunnel_token(args.tunnel_token_file, connector)
        say("  已写入 %s（%d 字符；原文件已备份为 .bak）" % (args.tunnel_token_file, len(connector)))

    # ---- 5. 入口规则 = 面板上的「公共主机名」 ----
    step("设置入口规则（公共主机名 → %s）" % args.target)
    if args.dry_run:
        say("  （dry-run）跳过")
    else:
        ingress = {"config": {"ingress": [
            {"hostname": hostname, "service": args.target,
             "originRequest": {"noTLSVerify": False}},
            {"service": "http_status:404"},
        ]}}
        status, obj = api("PUT", "/accounts/%s/cfd_tunnel/%s/configurations" % (args.account, tunnel_id),
                          token, args.api_base, ingress)
        if status != 200 or not obj.get("success"):
            say("  失败：HTTP %s  %s" % (status, api_errors(obj)))
            say("  → 这一步等价于在面板上「添加公共主机名」。你可以手动加：")
            say("     子域留空、域=%s、服务=HTTP、URL=%s" % (args.zone, args.target.replace("http://", "")))
        else:
            say("  OK")

    # ---- 6. DNS 记录：CNAME → <隧道id>.cfargotunnel.com（代理开启） ----
    step("建 DNS 记录（需要「区域 → DNS → 编辑」）")
    cname = "%s.cfargotunnel.com" % tunnel_id
    if args.dry_run:
        say("  （dry-run）将创建 CNAME %s -> %s（已代理）" % (hostname, cname))
    else:
        record = {"type": "CNAME", "name": hostname, "content": cname,
                  "proxied": True, "ttl": 1, "comment": "worklib tunnel"}
        status, obj = api("POST", "/zones/%s/dns_records" % zone_id, token, args.api_base, record)
        if status == 200 and obj.get("success"):
            say("  已创建：%s -> %s" % (hostname, cname))
        else:
            say("  创建返回 HTTP %s：%s" % (status, api_errors(obj)))
            say("  尝试改成「更新已有记录」…")
            status2, obj2 = api("GET", "/zones/%s/dns_records?name=%s"
                                % (zone_id, urllib.parse.quote(hostname)), token, args.api_base)
            recs = (obj2.get("result") or []) if status2 == 200 else []
            if recs:
                rid = recs[0]["id"]
                status3, obj3 = api("PUT", "/zones/%s/dns_records/%s" % (zone_id, rid),
                                    token, args.api_base, record)
                if status3 == 200 and obj3.get("success"):
                    say("  已更新：%s -> %s" % (hostname, cname))
                else:
                    say("  更新失败：HTTP %s  %s" % (status3, api_errors(obj3)))
                    return 7
            else:
                say("  没找到已有记录，也没能创建。请把上面的错误发我。")
                return 7

    say("")
    say("=" * 62)
    if args.dry_run:
        say("  演示结束（没有真的改动）。去掉 --dry-run 就会真跑。")
    else:
        say("  全部完成 ✅")
        say("")
        say("  下一步：双击「启动-作品库（含外网）.bat」")
        say("  然后用 4G 打开  https://%s  ，输 owner / guest 口令" % hostname)
    say("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
