"""统一推送：企业微信机器人 + 飞书自定义机器人。

合并自两个项目各自的 send_wechat_notification 逻辑：
- 百度版：每条 {note, share_link}
- 夸克版：每条 {note, share_url}，带标题与失败分支

统一后接受 shares 列表，元素字段优先取 share_url，回退 share_link。

2026-10-21：新增飞书群机器人（.env 的 FEISHU_WEBHOOK_URL），与企业微信并行推送。
飞书自定义机器人协议：POST {"msg_type": "text", "content": {"text": "..."}}，
以 HTTP 200 且 body.code == 0 表示成功（错误也常返回 200，故需校验 body）。
"""
import requests
from .logger import log_print

# 单条消息正文上限（企微 4096 字节硬上限，留安全余量）
MAX_LEN = 3800


def _get_share_url(share: dict) -> str:
    return share.get("share_url") or share.get("share_link") or ""


def build_content(shares, kw=None, failed=False):
    """构建推送正文（企业微信/飞书共用）。

    :returns: 正文字符串；无内容时返回 ""
    """
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
            return ""
        content_lines.append(f"【{search_title}搜索结果{len(shares)}】")
        content_lines.append("")

    for share in shares:
        note = share.get("note", "未知标题")
        share_url = _get_share_url(share)
        content_lines.append(f"❤️ {note}")
        content_lines.append(f"   {share_url}")
        content_lines.append("")

    return "\n".join(content_lines).strip()


def _split_chunks(text, max_len=MAX_LEN):
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


def _wechat_payload(text):
    return {"msgtype": "text", "text": {"content": text}}


def _feishu_payload(text):
    return {"msg_type": "text", "content": {"text": text}}


def _check_feishu_response(resp):
    """飞书自定义机器人错误也可能返回 HTTP 200，需校验 body.code。"""
    try:
        data = resp.json()
    except Exception:
        return
    code = data.get("code", data.get("StatusCode"))
    if code not in (None, 0):
        raise RuntimeError(
            f"飞书返回错误: code={code}, msg={data.get('msg') or data.get('StatusMessage')}"
        )


_VERIFIERS = {"飞书": _check_feishu_response}


def _send_chunks(webhook_url, chunks, make_payload, label, add_page_marks=True):
    """把分片逐条 POST 出去，返回 {success, message, chunks, sent}。"""
    if not webhook_url:
        msg = f"未提供{label} webhook 地址"
        log_print(msg, "WARNING")
        return {"success": False, "message": msg}

    total = len(chunks)
    ok = 0
    for idx, chunk in enumerate(chunks):
        body = chunk
        if total > 1 and add_page_marks:
            body = f"[ {idx+1}/{total} ]\n\n" + chunk
        try:
            log_print(f"正在发送{label}通知到: {webhook_url[:50]}... ({idx+1}/{total})")
            resp = requests.post(webhook_url, json=make_payload(body.strip()), timeout=10)
            resp.raise_for_status()
            verifier = _VERIFIERS.get(label)
            if verifier:
                verifier(resp)
            ok += 1
            log_print(f"{label}通知发送成功 ({idx+1}/{total})", "SUCCESS")
        except Exception as e:
            log_print(f"{label}通知发送失败 ({idx+1}/{total}): {e}", "ERROR")

    if ok == 0:
        return {"success": False, "message": f"{label}通知发送失败", "chunks": total}
    return {"success": True, "message": f"{label}通知发送成功",
            "chunks": total, "sent": ok}


def send_wechat_notification(webhook_url, shares, kw=None, failed=False):
    """发送企业微信通知。

    :param webhook_url: 企微机器人 webhook 地址
    :param shares: [{"note": ..., "share_url": ...}, ...]
    :param kw: 搜索关键词（用于标题）
    :param failed: 是否所有链接均失效
    """
    if not failed and not shares:
        return {"success": False, "message": "没有成功分享的链接"}

    content = build_content(shares, kw, failed)

    log_print("=" * 60)
    log_print("即将发送到企业微信的内容:")
    log_print(content)
    log_print("=" * 60)

    return _send_chunks(webhook_url, _split_chunks(content),
                        _wechat_payload, "企业微信")


def send_feishu_notification(webhook_url, shares, kw=None, failed=False):
    """发送飞书群机器人通知（参数同 send_wechat_notification）。"""
    if not failed and not shares:
        return {"success": False, "message": "没有成功分享的链接"}

    content = build_content(shares, kw, failed)

    log_print("=" * 60)
    log_print("即将发送到飞书的内容:")
    log_print(content)
    log_print("=" * 60)

    return _send_chunks(webhook_url, _split_chunks(content),
                        _feishu_payload, "飞书")


def send_notifications(config, shares, kw=None, failed=False):
    """按 config 并行推送企业微信 + 飞书机器人。

    :returns: {"wechat": {...}|None, "feishu": {...}|None}
              未配置对应 webhook 时该通道为 None。
    """
    if not failed and not shares:
        return {"wechat": {"success": False, "message": "没有成功分享的链接"},
                "feishu": None}

    wechat_url = (config.get("wechat", {}) or {}).get("webhook_url", "")
    feishu_url = (config.get("feishu", {}) or {}).get("webhook_url", "")

    out = {"wechat": None, "feishu": None}
    if wechat_url:
        out["wechat"] = send_wechat_notification(wechat_url, shares, kw, failed)
    if feishu_url:
        out["feishu"] = send_feishu_notification(feishu_url, shares, kw, failed)
    if not wechat_url and not feishu_url:
        log_print("未配置任何 webhook（企业微信/飞书），跳过推送", "WARNING")
    return out


def send_raw_text(config, text, max_len=MAX_LEN, add_page_marks=True):
    """把整段文案原样推送到企业微信 + 飞书（用于替换后的完整文案直推）。

    :returns: {"wechat": {...}|None, "feishu": {...}|None, "chunks": n}
    """
    chunks = _split_chunks(text, max_len)
    if not chunks:
        return {"wechat": None, "feishu": None, "chunks": 0}

    wechat_url = (config.get("wechat", {}) or {}).get("webhook_url", "")
    feishu_url = (config.get("feishu", {}) or {}).get("webhook_url", "")

    out = {"wechat": None, "feishu": None, "chunks": len(chunks)}
    if wechat_url:
        out["wechat"] = _send_chunks(wechat_url, chunks, _wechat_payload,
                                     "企业微信", add_page_marks)
    if feishu_url:
        out["feishu"] = _send_chunks(feishu_url, chunks, _feishu_payload,
                                     "飞书", add_page_marks)
    if not wechat_url and not feishu_url:
        log_print("未配置任何 webhook（企业微信/飞书），跳过推送", "WARNING")
    return out
