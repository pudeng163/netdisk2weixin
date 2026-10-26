"""统一推送模块。

- 企业微信机器人（send_wechat_notification）
- 飞书群机器人（send_feishu_notification）
- 双通道聚合（send_notifications / send_raw_text）：同时推送企微 + 飞书

注意：本文件曾被某次提交退化为仅企业微信单通道，导致依赖
send_notifications / send_raw_text 的模块（baidu/quark/share_local/
complete_pending_push/text_replacer）无法 import。此处补回双通道实现，
飞书只要配置了 webhook_url 即推送（与历史行为一致）。
"""
import requests
from .logger import log_print


def _get_share_url(share: dict) -> str:
    return share.get("share_url") or share.get("share_link") or ""


def _build_content(shares, kw=None, failed=False):
    """构建推送正文（企微/飞书共用）。"""
    lines = []
    title = kw if kw else "默认"
    if failed:
        for share in shares:
            note = share.get("note", "未知标题")
            share_url = _get_share_url(share)
            lines.append(f"❤️ {note}")
            lines.append(f"   {share_url}")
            lines.append("")
        lines.append(f"【{title}搜索结果 · 全部失效】")
        lines.append("")
        lines.append("所有链接均失效，推送失败")
    else:
        for share in shares:
            note = share.get("note", "未知标题")
            share_url = _get_share_url(share)
            lines.append(f"❤️ {note}")
            lines.append(f"   {share_url}")
            lines.append("")
        lines.append(f"【{title}搜索结果 · {len(shares)}条】")
    return "\n".join(lines)


def send_wechat_notification(webhook_url, shares, kw=None, failed=False):
    """发送企业微信通知。

    :param webhook_url: 企微机器人 webhook 地址
    :param shares: [{"note": ..., "share_url": ...}, ...]
    :param kw: 搜索关键词（用于标题）
    :param failed: 是否所有链接均失效
    """
    if not webhook_url:
        msg = "未提供企业微信 webhook 地址"
        log_print(msg, "WARNING")
        return {"success": False, "message": msg}

    content_lines = []
    search_title = kw if kw else "默认"

    if failed:
        for share in shares:
            note = share.get("note", "未知标题")
            share_url = _get_share_url(share)
            content_lines.append(f"❤️ {note}")
            content_lines.append(f"   {share_url}")
            content_lines.append("")
        content_lines.append(f"【{search_title}搜索结果 · 全部失效】")
        content_lines.append("")
        content_lines.append("所有链接均失效，推送失败")
    else:
        if not shares:
            return {"success": False, "message": "没有成功分享的链接"}
        for share in shares:
            note = share.get("note", "未知标题")
            share_url = _get_share_url(share)
            content_lines.append(f"❤️ {note}")
            content_lines.append(f"   {share_url}")
            content_lines.append("")
        content_lines.append(f"【{search_title}搜索结果 · {len(shares)}条】")

    content = "\n".join(content_lines)

    log_print("=" * 60)
    log_print("即将发送到企业微信的内容:")
    log_print(content)
    log_print("=" * 60)

    wechat_data = {
        "msgtype": "text",
        "text": {"content": content.strip()},
    }

    try:
        log_print(f"正在发送企业微信通知到: {webhook_url[:50]}...")
        response = requests.post(webhook_url, json=wechat_data, timeout=10)
        response.raise_for_status()
        message = "企业微信通知发送成功"
        log_print(message, "SUCCESS")
        return {"success": True, "message": message}
    except Exception as e:
        message = f"企业微信通知发送失败: {str(e)}"
        log_print(message, "ERROR")
        return {"success": False, "message": message}


def send_feishu_notification(webhook_url, content):
    """发送飞书群机器人（自定义机器人，无需签名）文本消息。

    :param webhook_url: 飞书自定义机器人 webhook
    :param content: 纯文本消息体
    :return: {"success": bool, "message": str}
    """
    if not webhook_url:
        msg = "未提供飞书 webhook 地址"
        log_print(msg, "WARNING")
        return {"success": False, "message": msg}

    feishu_data = {
        "msg_type": "text",
        "content": {"text": content.strip()},
    }

    try:
        log_print(f"正在发送飞书通知到: {webhook_url[:50]}...")
        response = requests.post(webhook_url, json=feishu_data, timeout=10)
        response.raise_for_status()
        body = response.json()
        if body.get("code") == 0:
            log_print("飞书通知发送成功", "SUCCESS")
            return {"success": True, "message": "飞书通知发送成功"}
        msg = f"飞书返回非 0: {body}"
        log_print(msg, "ERROR")
        return {"success": False, "message": msg}
    except Exception as e:
        msg = f"飞书通知发送失败: {str(e)}"
        log_print(msg, "ERROR")
        return {"success": False, "message": msg}


def send_notifications(cfg, shares, kw=None, failed=False):
    """双通道聚合推送：企业微信 + 飞书。

    返回 {"wechat": {...}, "feishu": {...}}，单个通道未配置或失败不影响另一个。
    """
    cfg = cfg or {}
    wechat_url = (cfg.get("wechat", {}) or {}).get("webhook_url", "")
    feishu_cfg = cfg.get("feishu", {}) or {}
    feishu_url = feishu_cfg.get("webhook_url", "")

    content = _build_content(shares, kw, failed)

    wechat_res = None
    if wechat_url:
        wechat_res = send_wechat_notification(wechat_url, shares, kw, failed)
    else:
        log_print("未配置企业微信 webhook，跳过企微推送", "WARNING")

    feishu_res = None
    if feishu_url:
        feishu_res = send_feishu_notification(feishu_url, content)
    else:
        log_print("未配置飞书 webhook，跳过飞书推送", "WARNING")

    return {"wechat": wechat_res, "feishu": feishu_res}


def send_raw_text(cfg, text):
    """双通道推送原始文本（用于文本替换后的完整文案）。"""
    cfg = cfg or {}
    wechat_url = (cfg.get("wechat", {}) or {}).get("webhook_url", "")
    feishu_url = (cfg.get("feishu", {}) or {}).get("webhook_url", "")

    wechat_res = None
    if wechat_url:
        try:
            response = requests.post(
                wechat_url,
                json={"msgtype": "text", "text": {"content": text.strip()}},
                timeout=10,
            )
            response.raise_for_status()
            wechat_res = {"success": True, "message": "企业微信通知发送成功"}
            log_print("企业微信原始文本推送成功", "SUCCESS")
        except Exception as e:
            wechat_res = {"success": False, "message": f"企业微信通知发送失败: {str(e)}"}
            log_print(wechat_res["message"], "ERROR")
    else:
        log_print("未配置企业微信 webhook，跳过企微推送", "WARNING")

    feishu_res = None
    if feishu_url:
        feishu_res = send_feishu_notification(feishu_url, text)
    else:
        log_print("未配置飞书 webhook，跳过飞书推送", "WARNING")

    return {"wechat": wechat_res, "feishu": feishu_res}
