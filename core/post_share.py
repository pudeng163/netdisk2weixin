# -*- coding: utf-8 -*-
"""统一收口：转存/分享后的「消息推送 + 飞书表格写入」。

所有场景（夸克/百度/迅雷批量、盘搜文本替换、盘内直推）都经此模块，
保证三条通道行为一致、配置单一：

  1. 企业微信        —— webhook 非空即推
  2. 飞书群机器人     —— 受 FEISHU_ENABLED 总开关控制，需配 FEISHU_WEBHOOK_URL
  3. 飞书多维表格     —— 受 FEISHU_ENABLED 总开关控制，需配 app 凭据

开关语义（单一来源 FEISHU_ENABLED，见 .env / DEPLOY.md）：
  - True  ：飞书 bot 与 bitable 都尝试（各自仍需对应凭据非空）
  - False ：两者都跳过

更早的实现里 bot 只看 webhook 是否非空、bitable 只看 enabled，两者各自为政，
导致「只推 bot 不写表 / 反之」等不一致；现统一收口到 enabled。

tag 列统一为平台中文名（夸克/百度/迅雷），不再出现「夸克」「baidu」混写。
"""
import time

from core.logger import log_print
from core.notifier import (send_notifications, send_raw_text,
                            send_feishu_notification)
from core.feishu_client import batch_write_resources

# 平台中文名映射（飞书 bitable 的 tag 列统一口径）
PLATFORM_CN = {"quark": "夸克", "baidu": "百度", "xunlei": "迅雷"}


def _normalize_shares(raw_shares, platform=None):
    """把各 adapter 各异的 share_results 规整成统一结构。

    统一字段：note/title（展示名）、share_url（优先 share_url 回退 share_link）、
    pass_code、size、tag（来源标签）、platform、search。
    """
    out = []
    for r in raw_shares or []:
        if not isinstance(r, dict):
            continue
        url = r.get("share_url") or r.get("share_link") or ""
        if not url:
            continue
        note = r.get("note") or r.get("title") or "未知标题"
        pf = r.get("platform") or platform
        tag = r.get("tag") or PLATFORM_CN.get(pf, pf or "")
        out.append({
            "note": note,
            "title": r.get("title") or r.get("note") or note,
            "share_url": url,
            "pass_code": r.get("pass_code") or r.get("passcode") or "",
            "size": r.get("size", ""),
            "tag": tag,
            "search": r.get("search") or note,
            "platform": pf,
        })
    return out


def send_messages(config, shares, *, kw=None, failed=False, raw_text=None, platform=None):
    """统一发「企业微信 + 飞书群机器人」消息。

    - raw_text 提供时，直接推送原始文案（文本替换模式用），不再按 shares 生成摘要卡片；
    - 否则按 shares 生成摘要卡片（send_notifications）。
    飞书 bot 受 feishu.enabled 总开关控制（notifier 内部二次校验）。
    """
    feishu = (config.get("feishu", {}) or {})
    feishu_on = bool(feishu.get("enabled"))
    wechat_url = (config.get("wechat", {}) or {}).get("webhook_url", "")

    out = {"wechat": None, "feishu": None}
    if raw_text is not None:
        if not wechat_url and not (feishu_on and feishu.get("webhook_url")):
            return out
        res = send_raw_text(config, raw_text)
        out["wechat"] = res.get("wechat")
        out["feishu"] = res.get("feishu")
        return out

    if not (failed or shares):
        return out
    res = send_notifications(config, shares, kw, failed, push_feishu=feishu_on)
    out["wechat"] = res.get("wechat")
    out["feishu"] = res.get("feishu")
    return out


def send_feishu_bot(config, shares, *, kw=None, failed=False):
    """仅推飞书群机器人（不推企业微信），受 FEISHU_ENABLED 控制。

    用于「企微已由其它环节推送、只需补飞书」的场景（如 联动分享.py
    在 share_local.py 让出飞书后统一补推），避免重复推企微。
    """
    fs = (config.get("feishu", {}) or {})
    if not (fs.get("enabled") and fs.get("webhook_url")):
        return None
    try:
        return send_feishu_notification(fs["webhook_url"], shares, kw, failed)
    except Exception as e:
        log_print(f"飞书机器人推送异常: {e}", "ERROR")
        return None


def write_feishu_bitable(config, shares, platform=None):
    """统一写飞书多维表格（去重追加）。受 FEISHU_ENABLED 控制。"""
    fs_cfg = (config.get("feishu", {}) or {})
    if not fs_cfg.get("enabled"):
        return None
    norm = _normalize_shares(shares, platform)
    if not norm:
        return None
    items = [{
        "name": r["note"],
        "url": r["share_url"],
        "size": r.get("size", ""),
        "tag": r["tag"],
        "search": r.get("search") or r["note"],
        "update_time": time.strftime("%Y-%m-%d"),
    } for r in norm]
    try:
        return batch_write_resources(config, items)
    except Exception as e:
        log_print(f"飞书写入异常: {e}", "ERROR")
        return None
