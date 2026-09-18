"""统一企业微信推送。

合并自两个项目各自的 send_wechat_notification 逻辑：
- 百度版：每条 {note, share_link}
- 夸克版：每条 {note, share_url}，带标题与失败分支

统一后接受 shares 列表，元素字段优先取 share_url，回退 share_link。
"""
import requests
from .logger import log_print


def _get_share_url(share: dict) -> str:
    return share.get("share_url") or share.get("share_link") or ""


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
        content_lines.append(f"【{search_title}搜索结果】")
        content_lines.append("")
        content_lines.append("所有链接均失效，推送失败")
        content_lines.append("")
        shares = []
    else:
        if not shares:
            return {"success": False, "message": "没有成功分享的链接"}
        content_lines.append(f"【{search_title}搜索结果{len(shares)}】")
        content_lines.append("")

    for share in shares:
        note = share.get("note", "未知标题")
        share_url = _get_share_url(share)
        content_lines.append(f"❤️ {note}")
        content_lines.append(f"   {share_url}")
        content_lines.append("")

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