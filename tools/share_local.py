# -*- coding: utf-8 -*-
"""把网盘里已有的资源直接创建分享并推送（不转存、不占新容量）。

支持多账号：--account N 使用 .env 里的 KUAKE_COOKIE_N（默认 1 用 KUAKE_COOKIE），
查重记录也按账号分开（data/share/share_url_total.txt / share_url_total_2.txt）。

用法:
    python tools/share_local.py --random 6             # 账号1：列目录→查重→随机选6个→推送
    python tools/share_local.py --random 6 --account 2 # 账号2：同上
    python tools/share_local.py --names "名称1" "名称2" # 指定名称推送
    python tools/share_local.py --list                  # 仅列出 /自动转存 下全部目录名
    python tools/share_local.py --random 6 --with-fid 4bc2...   # 每个分享链接同时打包指定 fid
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import load_config
from core.logger import log_print
from core.notifier import send_wechat_notification
from adapters.quark import SHARE_TOTAL_FILE, get_kuake_cli, _upload_yinliu_files

YINLIU_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

_QUARK_HEADERS = {
    "referer": "https://drive-pc.quark.cn/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "content-type": "application/json",
}
_QUARK_PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}


def share_multi_fid(cookie, fids, title, expired_type=1):
    """用夸克 API 把多个 fid 打包成一个分享链接（kuake CLI 只支持单路径，故走 API）。"""
    headers = dict(_QUARK_HEADERS)
    headers["cookie"] = cookie
    r = requests.post("https://drive-pc.quark.cn/1/clouddrive/share",
                      params=_QUARK_PARAMS, headers=headers, timeout=60,
                      json={"fid_list": fids, "title": title, "url_type": 1,
                            "expired_type": expired_type})
    j = r.json()
    if j.get("status") != 200 or not (j.get("data") or {}).get("task_id"):
        raise ValueError(f"创建分享任务失败: {j.get('message')}")
    task_id = j["data"]["task_id"]
    for retry in range(5):
        time.sleep(2)
        t = requests.get("https://drive-pc.quark.cn/1/clouddrive/task",
                         params={**_QUARK_PARAMS, "task_id": task_id,
                                 "retry_index": str(retry)},
                         headers=headers, timeout=60).json()
        if (t.get("data") or {}).get("share_id"):
            share_id = t["data"]["share_id"]
            r2 = requests.post(
                "https://drive-pc.quark.cn/1/clouddrive/share/password",
                params=_QUARK_PARAMS, headers=headers, timeout=60,
                json={"share_id": share_id}).json()
            d = (r2.get("data") or {})
            share_url = d.get("share_url")
            if not share_url:
                raise ValueError(f"获取分享链接失败: {r2.get('message')}")
            if d.get("passcode"):
                share_url += f"?pwd={d['passcode']}"
            return {"share_url": share_url, "title": d.get("title") or title,
                    "file_num": d.get("file_num", len(fids))}
    raise ValueError("等待 share_id 超时")


def fid_of(kuake_cli, folder, name):
    """查询目录下某个子项的 fid（用 kuake info）。"""
    path = folder.rstrip("/") + "/" + name
    code, out, err = _kuake(kuake_cli, "info", path)
    data = _parse_json(out) if code == 0 else None
    if data and data.get("fid"):
        return data["fid"]
    # list 兜底
    for d in list_dirs_raw(kuake_cli, folder):
        if d.get("file_name") == name:
            return d.get("fid")
    return None


def list_dirs_raw(kuake_cli, folder_path):
    """列目录，返回原始 dict 列表（含 fid）。"""
    code, out, err = _kuake(kuake_cli, "list", folder_path, "--stream")
    if code != 0:
        return []
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = j.get("data") or {}
        if d.get("file_name"):
            items.append(d)
    return items


def _kuake(kuake_cli, *args, retries=3):
    """调用 kuake CLI，带重试（runner 上网络抖动会导致偶发失败/超时）。"""
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            r = subprocess.run([kuake_cli, *args], capture_output=True, text=True,
                               timeout=120, encoding="utf-8", errors="replace")
            out = (r.stdout or "").strip()
            if r.returncode == 0 or attempt == retries:
                return r.returncode, out, (r.stderr or "").strip()
            last_err = f"exit={r.returncode} {(r.stderr or '').strip()[:100]}"
        except subprocess.TimeoutExpired as e:
            last_err = f"超时({e.timeout}s)"
        except OSError as e:
            last_err = str(e)
        log_print(f"kuake 调用失败({last_err})，重试 {attempt}/{retries - 1}: {' '.join(args[:2])}", "WARN")
        time.sleep(3 * attempt)
    return 124, "", f"kuake 调用失败: {last_err}"


def _parse_json(out):
    # 优先整体解析（kuake 输出 pretty-printed 多行 JSON）
    try:
        j = json.loads(out)
        if isinstance(j, dict) and j.get("success"):
            return j.get("data") or {}
    except json.JSONDecodeError:
        pass
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(j, dict) and j.get("success"):
            return j.get("data") or {}
    return None


def list_dirs(kuake_cli, folder_path):
    code, out, err = _kuake(kuake_cli, "list", folder_path, "--stream")
    if code != 0:
        log_print(f"list 失败: {err[:200]}", "ERROR")
        return []
    dirs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = j.get("data") or {}
        if d.get("dir") and d.get("file_name"):
            dirs.append(d["file_name"])
    return dirs


def load_pushed_titles(record_file):
    """读取已推送记录，返回标准化标题集合（用于查重）。"""
    pushed = set()
    if os.path.exists(record_file):
        with open(record_file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split("|")
                if parts:
                    pushed.add(_norm(parts[0]))
    return pushed


def _norm(s):
    """标准化目录名：去空白、去常见后缀如 (1)。"""
    s = re.sub(r"\s+", "", s.strip())
    s = re.sub(r"\(\d+\)$", "", s)
    return s


def filter_unpushed(dirs, pushed):
    """过滤掉已推送过的目录，返回未推送列表。"""
    return [d for d in dirs if _norm(d) not in pushed]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", nargs="*", default=[], help="要分享的目录名（精确匹配）")
    ap.add_argument("--list", action="store_true", help="仅列出目录名")
    ap.add_argument("--random", type=int, metavar="N", help="随机选 N 个未推送的目录推送")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    ap.add_argument("--account", type=int, default=1, metavar="N",
                    help="夸克账号编号：1=KUAKE_COOKIE（默认），2=KUAKE_COOKIE_2，以此类推")
    ap.add_argument("--folder", default="/自动转存", help="资源所在目录")
    ap.add_argument("--with-fid", dest="with_fid", default=None, metavar="FID",
                    help="额外打包进每个分享链接的 fid（如防失联目录），可逗号分隔多个")
    ap.add_argument("--no-yinliu", action="store_true", help="不上传引流文件")
    args = ap.parse_args()

    # 多账号：把对应 Cookie 注入 KUAKE_COOKIE 环境变量（kuake CLI 从这里认证）
    def _load_env():
        _env = {}
        _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(_env_path):
            with open(_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        _env[k.strip()] = v.strip()
        return _env

    if not os.environ.get("KUAKE_COOKIE"):
        os.environ["KUAKE_COOKIE"] = _load_env().get("KUAKE_COOKIE", "")
    if args.account > 1:
        env_key = f"KUAKE_COOKIE_{args.account}"
        cookie = os.environ.get(env_key) or _load_env().get(env_key, "")
        if not cookie:
            log_print(f"账号 {args.account} 未配置 Cookie（请在 .env 填写 {env_key}）", "ERROR")
            sys.exit(1)
        os.environ["KUAKE_COOKIE"] = cookie
        log_print(f"已切换到夸克账号 {args.account}", "INFO")

    # 该账号专属的已推送记录文件
    if args.account > 1:
        record_file = re.sub(r"\.txt$", f"_{args.account}.txt", SHARE_TOTAL_FILE)
    else:
        record_file = SHARE_TOTAL_FILE

    # 确保记录目录存在（GitHub Actions 全新 checkout 时 data/share/ 不存在）
    try:
        os.makedirs(os.path.dirname(record_file), exist_ok=True)
    except OSError as e:
        log_print(f"创建推送记录目录失败: {e}", "WARNING")

    config = load_config()
    kuake_cli = get_kuake_cli(config)
    if not kuake_cli or not os.path.exists(kuake_cli):
        log_print("kuake CLI 不可用", "ERROR")
        sys.exit(1)

    if args.list:
        for n in list_dirs(kuake_cli, args.folder):
            print(n)
        return

    if not args.names and args.random is None:
        ap.print_help()
        sys.exit(1)

    # 随机模式：列目录 → 查重 → 随机选 N 个
    if args.random is not None:
        extra_fids_pre = [x.strip() for x in (args.with_fid or "").split(",") if x.strip()]
        dirs = list_dirs(kuake_cli, args.folder)
        if extra_fids_pre:
            # 排除被 --with-fid 打包的目录本身，避免单独再分享它
            extra_names = {d.get("file_name") for d in list_dirs_raw(kuake_cli, args.folder)
                           if d.get("fid") in extra_fids_pre}
            if extra_names:
                dirs = [d for d in dirs if d not in extra_names]
                log_print(f"已排除合并分享目录: {', '.join(extra_names)}")
        if not dirs:
            log_print("目录列表为空", "ERROR")
            sys.exit(2)
        pushed = load_pushed_titles(record_file)
        unpushed = filter_unpushed(dirs, pushed)
        log_print(f"目录总数 {len(dirs)}，已推送 {len(dirs) - len(unpushed)}，未推送 {len(unpushed)}")
        if args.seed is not None:
            random.seed(args.seed)
        if len(unpushed) < args.random:
            log_print(f"未推送目录不足 {args.random} 个，全部选取 {len(unpushed)} 个", "WARN")
        args.names = random.sample(unpushed, min(args.random, len(unpushed)))
        if not args.names:
            log_print("没有可推送的未推送目录", "ERROR")
            sys.exit(2)
        log_print("随机选中:\n  " + "\n  ".join(args.names), "INFO")

    # 引流文件
    yinliu_files = []
    if not args.no_yinliu:
        for fn in ("99999.pdf", "【2026最新】夸克网盘扩容1T教程.mp4"):
            p = os.path.join(YINLIU_ASSETS_DIR, fn)
            if os.path.exists(p):
                yinliu_files.append(p)
    if args.no_yinliu:
        log_print("已跳过引流文件上传 (--no-yinliu)")

    # 额外打包的 fid 列表
    extra_fids = [x.strip() for x in (args.with_fid or "").split(",") if x.strip()]
    # 当前账号 Cookie（多 fid 分享走 API）
    current_cookie = os.environ.get("KUAKE_COOKIE", "")

    share_results = []
    for name in args.names:
        if extra_fids:
            # 多 fid 合并分享：资源 fid + 额外 fid → 同一个链接
            fid = fid_of(kuake_cli, args.folder, name)
            if not fid:
                log_print(f"获取 fid 失败，跳过: {name}", "ERROR")
                continue
            data = None
            for attempt in range(1, 4):
                try:
                    data = share_multi_fid(current_cookie, [fid] + extra_fids, name)
                    break
                except Exception as e:
                    log_print(f"合并分享失败({attempt}/3): {name} | {e}", "WARN")
                    if attempt < 3:
                        time.sleep(5 * attempt)
            if not data:
                log_print(f"合并分享多次重试仍失败，跳过: {name}", "ERROR")
                continue
            share_url = data["share_url"]
            log_print(f"分享成功(含{data['file_num']}项): {name} - {share_url}", "SUCCESS")
        else:
            path = args.folder.rstrip("/") + "/" + name
            code, out, err = _kuake(kuake_cli, "share", path, "0", "false")
            data = _parse_json(out) if code == 0 else None
            if not data:
                log_print(f"分享失败: {name} | {err[:150] or out[:150]}", "ERROR")
                continue
            share_url = data.get("share_url") or data.get("url") or ""
            if not share_url:
                log_print(f"分享失败（无链接）: {name} | {out[:150]}", "ERROR")
                continue
            log_print(f"分享成功: {name} - {share_url}", "SUCCESS")

        # 上传引流文件
        _upload_yinliu_files(kuake_cli, yinliu_files, name, args.folder)

        # 写入已推送记录（按账号分文件；失败不阻断后续分享与推送）
        try:
            os.makedirs(os.path.dirname(record_file), exist_ok=True)
            with open(record_file, "a", encoding="utf-8") as f:
                f.write(f"{name}  |  {share_url}  |  DONE\n")
        except OSError as e:
            log_print(f"写入推送记录失败（不影响本次分享）: {e}", "WARNING")

        share_results.append({"note": name, "share_url": share_url,
                              "title": name, "size": ""})
        time.sleep(1)

    if not share_results:
        log_print("全部分享失败，不推送", "ERROR")
        sys.exit(2)

    # 企业微信
    webhook_url = config.get("wechat", {}).get("webhook_url", "")
    wechat_result = send_wechat_notification(webhook_url, share_results, kw="网盘自有资源")

    # 飞书
    feishu_result = None
    fs_cfg = config.get("feishu", {})
    if fs_cfg.get("enabled"):
        try:
            from core.feishu_client import batch_write_resources
            items = [{
                "name": r["title"],
                "url": r["share_url"],
                "size": "",
                "tag": "夸克",
                "search": r["title"],
                "update_time": time.strftime("%Y-%m-%d"),
            } for r in share_results]
            feishu_result = batch_write_resources(config, items)
        except Exception as e:
            log_print(f"飞书写入异常: {e}", "ERROR")

    print(json.dumps({
        "success_count": len(share_results),
        "shares": share_results,
        "wechat": wechat_result,
        "feishu": feishu_result,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
