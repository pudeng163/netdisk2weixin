"""百度网盘适配器。

迁移自 fresh_baidu2weixin.py，实现统一适配器接口 run()。
纯 requests 实现，无浏览器依赖。
"""
import re
import time
import random
import string

import requests

from core import cache
from core.config import load_config, resolve_path
from core.logger import log_print
from core.notifier import send_wechat_notification

BASE_URL = "https://pan.baidu.com"
HEADERS = {
    "Host": "pan.baidu.com",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/webp,image/apng,*/*;q=0.8,"
               "application/signed-exchange;v=b3;q=0.9"),
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "navigate",
    "Referer": "https://pan.baidu.com",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7,en-GB;q=0.6,ru;q=0.5",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/114.0.0.0 Safari/537.36"),
}
EXP_MAP = {"1天": 1, "7天": 7, "30天": 30, "永久": 0}
ERROR_CODES = {
    -1: "链接错误，链接失效或缺少提取码",
    -4: "转存失败，无效登录。请退出账号在其他地方的登录",
    -6: "转存失败，请用浏览器无痕模式获取 Cookie 后再试",
    -7: "转存失败，转存文件夹名有非法字符",
    -8: "转存失败，目录中已有同名文件或文件夹存在",
    -9: "链接错误，提取码错误",
    -10: "转存失败，容量不足",
    -12: "链接错误，提取码错误",
    -62: "转存失败，链接访问次数过多",
    0: "转存成功",
    2: "转存失败，目标目录不存在",
    4: "转存失败，目录中存在同名文件",
    12: "转存失败，转存文件数超过限制",
    20: "转存失败，容量不足",
    105: "链接错误，所访问的页面不存在",
    404: "转存失败，秒传无效",
}

SHARE_ID_REGEX = re.compile(r'"shareid":(\d+?),"')
USER_ID_REGEX = re.compile(r'"share_uk":"(\d+?)","')
FS_ID_REGEX = re.compile(r'"fs_id":(\d+?),"')
SERVER_FILENAME_REGEX = re.compile(r'"server_filename":"(.+?)","')
ISDIR_REGEX = re.compile(r'"isdir":(\d+?),"')


class Network:
    def __init__(self):
        self.s = requests.Session()
        self.headers = HEADERS.copy()
        self.bdstoken = ""
        requests.packages.urllib3.disable_warnings()

    def get_bdstoken(self):
        url = f"{BASE_URL}/api/gettemplatevariable"
        params = {
            "clienttype": "0",
            "app_id": "38824127",
            "web": "1",
            "fields": '["bdstoken","token","uk","isdocuser","servertime"]',
        }
        r = self.s.get(url=url, params=params, headers=self.headers, timeout=10,
                       allow_redirects=False, verify=False)
        if r.json()["errno"] != 0:
            return r.json()["errno"]
        return r.json()["result"]["bdstoken"]

    def get_dir_list(self, folder_name):
        url = f"{BASE_URL}/api/list"
        params = {
            "order": "time", "desc": "1", "showempty": "0", "web": "1",
            "page": "1", "num": "1000", "dir": folder_name, "bdstoken": self.bdstoken,
        }
        r = self.s.get(url=url, params=params, headers=self.headers, timeout=15,
                       allow_redirects=False, verify=False)
        if r.json()["errno"] != 0:
            return r.json()["errno"]
        return r.json()["list"]

    def create_dir(self, folder_name):
        url = f"{BASE_URL}/api/create"
        params = {"a": "commit", "bdstoken": self.bdstoken}
        data = {"path": folder_name, "isdir": "1", "block_list": "[]"}
        r = self.s.post(url=url, params=params, headers=self.headers, data=data,
                        timeout=15, allow_redirects=False, verify=False)
        return r.json()["errno"]

    def verify_pass_code(self, link_url, pass_code):
        url = f"{BASE_URL}/share/verify"
        params = {
            "surl": link_url[25:48], "bdstoken": self.bdstoken,
            "t": str(int(round(time.time() * 1000))),
            "channel": "chunlei", "web": "1", "clienttype": "0",
        }
        data = {"pwd": pass_code, "vcode": "", "vcode_str": ""}
        r = self.s.post(url=url, params=params, headers=self.headers, data=data,
                        timeout=10, allow_redirects=False, verify=False)
        if r.json()["errno"] != 0:
            return r.json()["errno"]
        return r.json()["randsk"]

    def get_transfer_params(self, url):
        r = self.s.get(url=url, headers=self.headers, timeout=15, verify=False)
        return r.content.decode("utf-8")

    def transfer_file(self, params_list, folder_name):
        url = f"{BASE_URL}/share/transfer"
        params = {
            "shareid": params_list[0], "from": params_list[1],
            "bdstoken": self.bdstoken, "channel": "chunlei", "web": "1",
            "clienttype": "0",
        }
        data = {"fsidlist": f"[{','.join(params_list[2])}]", "path": f"/{folder_name}"}
        r = self.s.post(url=url, params=params, headers=self.headers, data=data,
                        timeout=30, allow_redirects=False, verify=False)
        return r.json()["errno"]

    def create_share(self, fs_id, expiry, password):
        url = f"{BASE_URL}/share/set"
        params = {
            "channel": "chunlei", "bdstoken": self.bdstoken,
            "clienttype": "0", "app_id": "250528", "web": "1",
        }
        data = {
            "period": expiry, "pwd": password, "eflag_disable": "true",
            "channel_list": "[]", "schannel": "4", "fid_list": f"[{fs_id}]",
        }
        r = self.s.post(url=url, params=params, headers=self.headers, data=data,
                        timeout=15, allow_redirects=False, verify=False)
        if r.json()["errno"] != 0:
            return r.json()["errno"]
        return r.json()["link"]


def normalize_link(url_code):
    normalized = url_code.replace("share/init?surl=", "s/1")
    normalized = re.sub(r"[?&]pwd=", " ", normalized)
    normalized = re.sub(r"提取码*[：:]", " ", normalized)
    normalized = re.sub(r"^.*?(https?://)", "https://", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def parse_url_and_code(url_code):
    parts = url_code.strip().split(" ")
    url = parts[0]
    code = parts[1] if len(parts) > 1 else ""
    return url[:47], code[-4:] if code else ""


def parse_response(response):
    shareid_list = SHARE_ID_REGEX.findall(response)
    user_id_list = USER_ID_REGEX.findall(response)
    fs_id_list = FS_ID_REGEX.findall(response)
    server_filename_list = SERVER_FILENAME_REGEX.findall(response)
    isdir_list = ISDIR_REGEX.findall(response)
    if not all([shareid_list, user_id_list, fs_id_list, server_filename_list, isdir_list]):
        return -1
    return [shareid_list[0], user_id_list[0], fs_id_list,
            list(dict.fromkeys(server_filename_list)), isdir_list]


def update_cookie(bdclnd, cookie):
    cookies_dict = dict(map(lambda it: it.split("=", 1),
                            filter(None, cookie.split(";"))))
    cookies_dict["BDCLND"] = bdclnd
    return ";".join([f"{k}={v}" for k, v in cookies_dict.items()])


def generate_code():
    return "".join(random.choice(string.ascii_letters + string.digits)
                   for _ in range(4))


def run(count=None, kw=None, include=None, exclude=None, preview_only=False, force=False,
        selected_items=None, platform_cfg=None):
    """百度网盘转存分享主流程（统一接口）。"""
    config = load_config()
    baidu = config["baidu"]
    cookie = baidu.get("cookie", "")
    if count is None:
        count = baidu.get("count") or config["count"]
    target_folder = baidu.get("target_folder") or config["target_folder"]
    expire_hours = config.get("cache_expire_hours", 24)
    webhook_url = config.get("wechat", {}).get("webhook_url", "")

    if not cookie:
        log_print("错误：请配置百度网盘 COOKIE", "ERROR")
        return {"code": 400, "message": "未配置百度网盘 Cookie"}

    if selected_items is not None:
        resources = selected_items
    else:
        resources = cache.fetch_api_data("baidu", kw or "", expire_hours=expire_hours,
                                          force_refresh=force)

    if not resources:
        log_print("没有找到百度网盘资源", "WARNING")
        return {"code": 200, "message": "没有获取到资源", "items": [],
                "preview": preview_only, "share_results": []}

    random.shuffle(resources)
    resources = resources[:count]
    log_print(f"获取到 {len(resources)} 个百度网盘资源")

    # 预览模式：仅返回资源列表，不执行转存
    if preview_only:
        return {"code": 200, "message": "预览成功", "preview": True,
                "items": resources, "share_results": []}

    network = Network()
    network.headers["Cookie"] = cookie

    bdstoken = network.get_bdstoken()
    if isinstance(bdstoken, int):
        log_print(f"获取 bdstoken 失败，错误代码：{bdstoken}", "ERROR")
        return {"code": 500, "message": f"获取 bdstoken 失败: {bdstoken}"}
    network.bdstoken = bdstoken

    result = network.get_dir_list(f"/{target_folder}")
    if isinstance(result, int):
        return_code = network.create_dir(target_folder)
        if return_code != 0:
            log_print(f"创建目录失败，错误代码：{return_code}", "ERROR")
            return {"code": 500, "message": f"创建目录失败: {return_code}"}
    log_print(f"目标目录：{target_folder}")

    results = []
    for idx, resource in enumerate(resources, 1):
        url = resource.get("url", "")
        password = resource.get("password", "")
        note = resource.get("note", "")

        if not url or "pan.baidu.com" not in url:
            continue

        log_print(f"正在处理第 {idx} 个资源: {note}")
        try:
            normalized_link = normalize_link(f"{url} {password}")
            parsed_url, code = parse_url_and_code(normalized_link)

            if code:
                bdclnd = network.verify_pass_code(parsed_url, code)
                if isinstance(bdclnd, int):
                    log_print(f"验证提取码失败：{ERROR_CODES.get(bdclnd, bdclnd)}", "WARNING")
                    continue
                network.headers["Cookie"] = update_cookie(bdclnd, network.headers["Cookie"])

            response = network.get_transfer_params(parsed_url)
            parsed = parse_response(response)
            if not isinstance(parsed, list):
                log_print(f"解析链接失败：{ERROR_CODES.get(parsed, parsed)}", "WARNING")
                continue

            transfer_result = network.transfer_file(parsed, target_folder)
            if transfer_result != 0:
                log_print(f"转存失败：{ERROR_CODES.get(transfer_result, transfer_result)}", "WARNING")
                continue

            file_name = parsed[3][0] if parsed[3] else "未知文件"
            is_dir = parsed[4] == ["1"]

            dir_list = network.get_dir_list(f"/{target_folder}")
            if not isinstance(dir_list, list):
                continue

            share_url = None
            for item in dir_list:
                if item["server_filename"] == file_name and \
                        item["isdir"] == (1 if is_dir else 0):
                    share_password = generate_code()
                    share_result = network.create_share(item["fs_id"],
                                                        str(EXP_MAP["永久"]),
                                                        share_password)
                    if isinstance(share_result, str):
                        share_url = f"{share_result}?pwd={share_password}"
                    break

            if share_url:
                results.append({"note": note, "share_url": share_url})
                log_print(f"分享成功：{note} -> {share_url}", "SUCCESS")
            else:
                log_print(f"创建分享链接失败：{note}", "WARNING")

            time.sleep(random.uniform(1, 3))
        except Exception as e:
            log_print(f"处理资源失败 {note}：{e}", "ERROR")

    wechat_result = None
    if webhook_url:
        if results:
            wechat_result = send_wechat_notification(webhook_url, results, kw)
        elif resources:
            wechat_result = send_wechat_notification(webhook_url, [], kw, failed=True)

    log_print(f"完成！成功处理 {len(results)} 个资源")
    return {
        "code": 200,
        "message": "批量转存分享操作完成",
        "total_selected": count,
        "save_success_count": len(results),
        "share_success_count": len(results),
        "share_results": results,
        "wechat_result": wechat_result,
        "preview": preview_only,
        "items": None,
    }