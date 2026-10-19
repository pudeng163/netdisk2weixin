# netdisk2weixin — 网盘链接替换

当用户说「替换链接」「改链接」「转存这个」时，按以下规则执行。

## 核心命令

```bash
python main.py --text "把带网盘链接的完整文本贴这里"
```

或从文件读：

```bash
python main.py --text-file 文本文件名.txt
```

## 批量转存（按关键词搜）

```bash
python main.py quark --count 10 --include "语文"
python main.py quark --count 5 --force          # 强制跳过缓存重新转存
python main.py quark --count 3 --preview        # 只预览不转存
```

## 自动做的事（不用额外参数）

1. 夸克网盘转存到自己账号
2. 生成新的分享链接替换原链接
3. 上传引流文件（`assets/99999.pdf` 及 `.env` 里 `QUARK_YINLIU_FILES` 配置的其它文件，如视频教程）到转存文件夹内
4. 推送企业微信通知
5. 写入飞书多维表格

## 支持识别的网盘

- 夸克（`pan.quark.cn/s/xxx`）
- 百度（`pan.baidu.com/s/xxx`）

## 结果里能拿到什么

- 替换后的完整文本（直接复制就能用）
- 每个链接的原始 URL → 新 URL 映射
- 转存到的文件大小（如 `305.9MB`）
- 飞书写入状态

## 常见报错

| 报错 | 处理 |
|------|------|
| 账号被封/链接失效 | 跳过该条，换源或让用户换链接 |
| 引流文件 400 | 已知 bug，不影响主流程，忽略即可 |
| .env 没配 FEISHU | 飞书跳过，其他正常 |
| QUARK_COOKIE 过期 | 让用户更新 Cookie |
