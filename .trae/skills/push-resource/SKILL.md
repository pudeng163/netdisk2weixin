---
name: "push-resource"
description: "一键推送网盘资源：自动检测/启动 PanSou 本地盘搜服务，执行转存→清理引流→上传99999.pdf→生成新链接→企业微信推送。Invoke when user asks to push/transfer/转存/推送 网盘资源."
---

# Push Resource - 一键推送网盘资源

用户说"帮我推送 N 个资源"、"转存 N 个资源"、"推送资源不要短剧"时，**立即使用本 Skill**。

## 1. 前置条件检查（必须先做）

### 步骤 1：检测 PanSou 盘搜服务是否在跑

```powershell
# RunCommand — blocking=true
try { (Invoke-WebRequest -Uri "http://localhost:8888/api/health" -UseBasicParsing -TimeoutSec 3).Content } catch { "NOT_RUNNING" }
```

- 如果返回 JSON 且 `status: "ok"` → 跳过步骤 2，直接进入第 2 节
- 如果返回 `"NOT_RUNNING"` 或报错 → 执行步骤 2

### 步骤 2：启动 PanSou（后台长驻）

```powershell
# RunCommand — blocking=false, command_type=web_server, wait_ms_before_async=4000
cd D:\网盘拉新项目\netdisk2weixin
powershell -ExecutionPolicy Bypass -File .\start_pansou.ps1
```

然后每 2 秒轮询一次 health 接口，最多等 60 秒直到返回 `{"status":"ok"}`。
超时则报错并终止，不要继续执行转存流程。

## 2. 执行转存推送

核心命令格式：

```powershell
python main.py <platform> --count <N> [--exclude <关键词>] [--include <关键词>] [--kw <API关键词>] [--force] [--preview]
```

### 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| platform | — | `quark` / `baidu` / `all` |
| --count | 6 | 转存数量 |
| --exclude | `短剧` | 排除关键词（可重复指定） |
| --include | 无 | 必须包含关键词（可重复指定，本地过滤） |
| --kw | 无 | 直接传给 PanSou 的搜索词 |
| --force | false | 跳过本地缓存 |
| --preview | false | 只预览，不真转存 |

### 常见用户意图 → 命令映射

| 用户说 | 生成命令 |
|---|---|
| 推送 6 个资源，不要短剧 | `python main.py quark --count 6 --exclude 短剧` |
| 推 10 个百度网盘资源 | `python main.py baidu --count 10` |
| 推 5 个考研资料 | `python main.py quark --count 5 --include 考研 --exclude 短剧` |
| 推 6 个，不要短剧不要电影 | `python main.py quark --count 6 --exclude 短剧 --exclude 电影` |
| 预览下会抓到什么 | `python main.py quark --count 3 --preview` |
| 两个平台都推 | `python main.py all --count 5 --exclude 短剧` |

### 执行方式

```powershell
# RunCommand — blocking=true, 等待完整输出
python main.py quark --count 6 --exclude 短剧
```

## 3. 结果判读

### 成功标志
- 看到 `转存成功 [N/M]` 日志
- 看到 `引流清理完成：检查 X 个，删除 Y 个`
- 看到 `分享成功 [N]: xxx - https://pan.quark.cn/s/xxxx`
- 最后输出 `全部任务完成`

### 失败处理
| 现象 | 含义 | 处理 |
|---|---|---|
| `WinError 10061` | PanSou 没在跑 | 回到第 1 节启动 |
| `API 返回总数: 0` | 没搜到资源 | 换 `--kw` 关键词或等会儿再试 |
| `获取 stoken 失败（分享不存在）` | 源分享已失效 | 正常损耗，跳过即可 |
| `转存失败 -8` | 目录已有同名 | 正常，换新资源 |
| `转存失败 -6`（百度） | Cookie 失效 | 提示用户重新抓 Cookie |

## 4. 输出给用户的总结

执行完后，整理以下信息以表格呈现：

```
✅ 推送完成：成功 X / 失败 Y

| # | 资源名称 | 新分享链接 |
|---|---------|-----------|
| 1 | xxx | https://pan.quark.cn/s/xxx |
| 2 | xxx | https://pan.quark.cn/s/yyy |

- 引流清理：扫描 N 个文件，删除 M 个别人的引流文件
- 99999.pdf：已全部上传到 X 个目录
- 企业微信推送：✅ 已发送 / ❌ 失败
- 飞书表格：X 条追加 / Y 条跳过（重名）
```

## 5. 注意事项

- **PanSou 是长驻服务**，启动后不要关闭那个终端窗口，下次推送直接复用
- **Cookie 有效期**：夸克 Cookie 可能 1~2 周过期，过期后转存/分享会报错，提示用户重新抓
- **重复推送**：已推送过的资源会自动过滤（按标题匹配），不会重复转存
- **引流清理在前，上传在后**：先删掉别人的，再上传自己的 99999.pdf，确保你的 PDF 不会被自己的清理逻辑误删
