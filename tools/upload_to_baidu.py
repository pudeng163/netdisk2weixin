"""本地文件 → 百度网盘：分块上传 + 创建分享链接。

项目原本的百度适配器只支持「转存他人分享 + 创建分享」，没有「本地文件上传」。
本脚本补齐这一环，供「把 GitHub 等外部资源落地到自己的百度网盘并分享」使用。

流程（百度 PCSS 标准分块上传）：
  precreate（取 uploadid） → upload 每块(4MB, type=tmpfile) → create（合并落盘）
上传成功后用 adapters.baidu.Network 的 create_share 生成带提取码的永久分享链接。

用法：
  python tools/upload_to_baidu.py <本地文件> [--dir 百度目录] [--name 远程文件名]
"""
import os
import sys
import json
import time
import hashlib

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.config import load_config
from adapters.baidu import BASE_URL, HEADERS

CHUNK = 4 * 1024 * 1024  # 4MB 分块
APP_ID = "250528"


def _session(cookie: str):
    s = requests.Session()
    headers = HEADERS.copy()
    headers["Cookie"] = cookie
    headers["Accept"] = "application/json, text/plain, */*"
    requests.packages.urllib3.disable_warnings()
    return s, headers


def _get_bdstoken(s, headers):
    r = s.get(
        f"{BASE_URL}/api/gettemplatevariable",
        params={"clienttype": "0", "app_id": "38824127", "web": "1",
                "fields": '["bdstoken","token","uk","isdocuser","servertime"]'},
        headers=headers, timeout=10, allow_redirects=False, verify=False,
    )
    data = r.json()
    if data.get("errno") != 0:
        raise RuntimeError(f"获取 bdstoken 失败: {data}")
    return data["result"]["bdstoken"]


def upload_file(cookie: str, local_path: str, remote_dir: str, remote_name: str = ""):
    """分块上传本地文件到百度网盘，返回 (remote_path, fs_id)。"""
    s, headers = _session(cookie)
    bdstoken = _get_bdstoken(s, headers)

    size = os.path.getsize(local_path)
    fname = remote_name or os.path.basename(local_path)
    remote_dir = remote_dir.strip().strip("/")
    remote_path = f"/{remote_dir}/{fname}" if remote_dir else f"/{fname}"

    # 读块并算每块 MD5
    blocks, md5s = [], []
    with open(local_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            blocks.append(chunk)
            md5s.append(hashlib.md5(chunk).hexdigest())
    print(f"[上传] {fname}  共 {len(blocks)} 块 / {size} 字节")

    common = {"method": "precreate", "app_id": APP_ID, "web": "1",
              "channel": "chunlei", "clienttype": "0", "bdstoken": bdstoken}
    # 1) precreate
    r = s.post(
        f"{BASE_URL}/rest/2.0/xpan/file",
        params=common,
        data={"path": remote_path, "size": str(size), "isdir": "0",
              "autoinit": "1", "block_list": json.dumps(md5s), "rtype": "1"},
        headers=headers, timeout=30, allow_redirects=False, verify=False,
    )
    data = r.json()
    if data.get("errno") != 0:
        raise RuntimeError(f"precreate 失败: {data}")
    uploadid = data.get("uploadid") or data.get("upload_id")
    print(f"[上传] precreate OK, uploadid={uploadid}")

    # 2) 逐块上传
    for i, chunk in enumerate(blocks):
        up = {"method": "upload", "type": "tmpfile", "app_id": APP_ID, "web": "1",
              "channel": "chunlei", "clienttype": "0", "bdstoken": bdstoken}
        r = s.post(
            f"{BASE_URL}/rest/2.0/xpan/file",
            params=up,
            data={"path": remote_path, "uploadid": uploadid, "partseq": str(i)},
            files={"file": (fname, chunk)},
            headers=headers, timeout=120, allow_redirects=False, verify=False,
        )
        data = r.json()
        if data.get("errno") != 0:
            raise RuntimeError(f"upload 第 {i} 块失败: {data}")
        # 校验服务端回写的 md5 与本地一致
        if data.get("md5") and data["md5"] != md5s[i]:
            raise RuntimeError(f"upload 第 {i} 块 MD5 不一致(本地{mds[i]} vs 服务端{data['md5']})")
        print(f"[上传] 块 {i + 1}/{len(blocks)} OK")

    # 3) create 合并落盘
    cr = {"method": "create", "app_id": APP_ID, "web": "1",
          "channel": "chunlei", "clienttype": "0", "bdstoken": bdstoken}
    r = s.post(
        f"{BASE_URL}/rest/2.0/xpan/file",
        params=cr,
        data={"path": remote_path, "size": str(size), "isdir": "0",
              "uploadid": uploadid, "block_list": json.dumps(md5s), "rtype": "1"},
        headers=headers, timeout=30, allow_redirects=False, verify=False,
    )
    data = r.json()
    if data.get("errno") != 0:
        raise RuntimeError(f"create 失败: {data}")
    fs_id = data.get("fs_id") or (data.get("info", [{}])[0].get("fs_id"))
    print(f"[上传] create OK, fs_id={fs_id}, path={data.get('path')}")
    return remote_path, fs_id


def create_share(cookie: str, fs_id, expiry="0", password=""):
    """用项目内置逻辑创建带提取码的永久分享链接，返回完整链接字符串。"""
    from adapters.baidu import Network, EXP_MAP
    net = Network()
    net.headers["Cookie"] = cookie
    bdstoken = net.get_bdstoken()
    if isinstance(bdstoken, int):
        raise RuntimeError(f"获取 bdstoken 失败: {bdstoken}")
    net.bdstoken = bdstoken
    if not password:
        import random, string
        password = "".join(random.choice(string.ascii_letters + string.digits)
                           for _ in range(4))
    link = net.create_share(fs_id, str(EXP_MAP["永久"]), password)
    if not isinstance(link, str):
        raise RuntimeError(f"create_share 失败 errno={link}")
    return f"{link}?pwd={password}"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="本地文件路径")
    ap.add_argument("--dir", default="github下载", help="百度网盘目标目录（默认 github下载）")
    ap.add_argument("--name", default="", help="远程文件名（默认用本地名）")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"文件不存在: {args.file}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    cookie = config["baidu"].get("cookie", "")
    if not cookie:
        print("未配置 BAIDU_COOKIE", file=sys.stderr)
        sys.exit(1)

    remote_path, fs_id = upload_file(cookie, args.file, args.dir, args.name)
    share_url = create_share(cookie, fs_id)
    print("=" * 60)
    print(f"远程路径: {remote_path}")
    print(f"分享链接: {share_url}")
    print("=" * 60)


if __name__ == "__main__":
    main()
