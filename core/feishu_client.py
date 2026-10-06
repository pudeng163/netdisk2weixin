import time
import json
import requests
from core.logger import log_print


_TOKEN = None
_TOKEN_EXPIRE_AT = 0
_APP_TOKEN_CACHE = {}


def _get_tenant_token(app_id, app_secret):
    global _TOKEN, _TOKEN_EXPIRE_AT
    now = time.time()
    if _TOKEN and now < _TOKEN_EXPIRE_AT - 60:
        return _TOKEN
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"飞书 token 失败: {j}")
    _TOKEN = j["tenant_access_token"]
    _TOKEN_EXPIRE_AT = now + j.get("expire", 7200)
    return _TOKEN


def _get_app_token(token, wiki_node_token):
    if wiki_node_token in _APP_TOKEN_CACHE:
        return _APP_TOKEN_CACHE[wiki_node_token]
    r = requests.get(
        "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node",
        params={"token": wiki_node_token},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"飞书 wiki get_node 失败: {j}")
    node = j["data"]["node"]
    if node.get("obj_type") != "bitable":
        raise RuntimeError(f"wiki 节点类型不是 bitable: {node.get('obj_type')}")
    app_token = node["obj_token"]
    _APP_TOKEN_CACHE[wiki_node_token] = app_token
    return app_token


def _list_existing_names(token, app_token, table_id):
    """查询多维表格里已有的「资源名称」集合，用于追加前去重。"""
    existing = set()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    page_token = None
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        j = r.json()
        if j.get("code") != 0:
            break
        for rec in j.get("data", {}).get("items", []):
            name = rec.get("fields", {}).get("资源名称", "")
            if name:
                existing.add(str(name))
        if not j.get("data", {}).get("has_more"):
            break
        page_token = j.get("data", {}).get("page_token")
    return existing


def batch_write_resources(config, items):
    """把一批资源**追加**到飞书多维表格（重名跳过，不会覆盖）。

    items: list of dict, 每个含 name, url, size, tag, search, update_time
    """
    fs = config.get("feishu", {})
    if not fs.get("enabled"):
        return {"success": False, "message": "feishu 未启用"}

    app_id = fs.get("app_id", "")
    app_secret = fs.get("app_secret", "")
    wiki_token = fs.get("wiki_node_token", "")
    table_id = fs.get("table_id", "")

    if not all([app_id, app_secret, wiki_token, table_id]):
        return {"success": False, "message": "飞书配置不完整"}

    try:
        token = _get_tenant_token(app_id, app_secret)
    except Exception as e:
        return {"success": False, "message": f"获取 token 失败: {e}"}

    try:
        app_token = _get_app_token(token, wiki_token)
    except Exception as e:
        return {"success": False, "message": f"获取 app_token 失败: {e}"}

    existing = _list_existing_names(token, app_token, table_id)
    log_print(f"飞书已有记录: {len(existing)} 条", "INFO")

    new_items = [it for it in items if it.get("name", "") not in existing]
    skipped = len(items) - len(new_items)
    if skipped > 0:
        log_print(f"跳过重名: {skipped} 条", "INFO")

    if not new_items:
        log_print("飞书写入: 没有新资源需要追加", "INFO")
        return {"success": True, "count": 0, "skipped": skipped}

    records = []
    for it in new_items:
        fields = {
            "资源名称": it.get("name", "未知"),
            "资源链接": {"link": it.get("url", ""), "text": "下载链接"},
            "文件大小": it.get("size", "") or "",
            "更新时间": it.get("update_time", "") or time.strftime("%Y-%m-%d"),
        }
        records.append({"fields": fields})

    body = json.dumps({"records": records}, ensure_ascii=False).encode("utf-8")
    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
        f"/tables/{table_id}/records/batch_create",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=body,
        timeout=20,
    )
    j = r.json()
    if j.get("code") == 0:
        created = len(j.get("data", {}).get("records", []))
        log_print(f"飞书写入成功: {created} 条追加, {skipped} 条跳过", "SUCCESS")
        return {"success": True, "count": created, "skipped": skipped}
    else:
        log_print(f"飞书写入失败: {j}", "ERROR")
        return {"success": False, "message": str(j), "skipped": skipped}
