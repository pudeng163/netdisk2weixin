# 网盘拉新统一转存工具

把原来的「百度网盘转存」和「夸克网盘转存」两个独立项目，合并为一个统一项目。
一份配置、一个入口、统一的企业微信推送和 Web 管理界面，百度 / 夸克可分别或一起跑。

---

## 0. 这个工具是干嘛的

从在线聚合站抓取网盘分享链接 → 自动转存到**你自己的网盘** → 生成新的分享链接 → 推送到企业微信群。
用于「网盘拉新」：别人通过你转发的链接保存文件，你获得拉新奖励。

自动流程：
1. 请求聚合 API，拿到一批网盘资源（本地缓存 + 本地关键词过滤，避免频繁请求）
2. 随机抽取指定数量，逐个验证提取码 → 转存 → 生成分享链接
3. 夸克网盘还会把引流文件 `333333.pdf` 上传到分享文件夹
4. 结果统一推送到企业微信机器人

---

## 1. 环境要求

- **Python 3.8+**（本机是 3.10，已验证）
- 依赖库：见 `requirements.txt`

```bash
cd netdisk2weixin
pip install -r requirements.txt
```

> 夸克网盘首次登录需要 playwright 浏览器，安装一次即可：
> ```bash
> playwright install chromium
> ```

---

## 2. 配置（重要）

### 2.1 准备配置文件

```bash
# 项目里已有 config.yaml（含 Cookie），如果还没有，从样例复制一份：
cp config.example.yaml config.yaml
```

### 2.2 编辑 `config.yaml`

```yaml
# 全局默认
count: 5                    # 每次转存资源数量（可被各平台覆盖）
cache_expire_hours: 24      # API 缓存过期小时
target_folder: 转存资源     # 百度网盘目标文件夹

wechat:
  webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key"

baidu:
  cookie: "你的百度网盘Cookie"   # 必填，转存百度才需要

quark:
  cookie: ""                  # 夸克 Cookie，可选（见下面 2.4）
  # kuake_cli: ""             # 留空自动识别系统（Windows/Linux），自定义路径则原样使用
  folder_fid: "0"              # 转存目标文件夹 fid，"0" = 根目录
  yinliu_pdf: "assets/333333.pdf"
  yinliu_subdir: "自动转存"    # 引流 PDF 上传到 /{yinliu_subdir}/{title}/333333.pdf
  yinliu_probability: 0.0      # 引流文件添加概率 0~1
```

> **兼容旧项目 `.env`**：也支持用 `.env` 配置（字段名 `COOKIE`、`QUARK_COOKIES`、`WEBHOOK_URL` 等），环境变量优先级高于 yaml。但推荐统一用 `config.yaml`。

### 2.3 获取百度 Cookie

1. 浏览器**无痕模式**打开 [pan.baidu.com](https://pan.baidu.com) 并登录
2. 按 F12 → Network 面板
3. 刷新页面，点任意请求，复制请求头里的 `Cookie` 整个值
4. 粘贴到 `config.yaml` 的 `baidu.cookie`

> ⚠️ 如果转存时报错 `-6`，说明 Cookie 已失效或非无痕环境获取，重新用无痕模式抓一份即可。

### 2.4 获取夸克 Cookie（三选一）

**方式 A — 填 config.yaml**（推荐，最省事）：
把夸克 Cookie 字符串填到 `quark.cookie`。

**方式 B — 浏览器登录自动保存**：
`quark.cookie` 留空，运行时会弹出浏览器让你登录，登录态自动保存到
`data/cache/cookies.txt`。

**方式 C — 手动放 cookies.txt**：
把旧的夸克 `cookies.txt`（`cookie` 字符串或 JSON 数组格式都行）复制到
`netdisk2weixin/data/cache/cookies.txt`。

### 2.5 kuake CLI（夸克网盘命令行工具）

项目自带两个版本，**自动按操作系统选择**，无需配置：

| 系统 | 自动使用路径 |
|---|---|
| Windows | `assets/bin/kuake-v1.5.0-windows-amd64.exe` |
| Linux | `assets/bin/kuake-v1.5.0-linux-amd64` |

如果要换自定义版本，在 `config.yaml` 填 `quark.kuake_cli: "你的路径"` 即可，自定义路径不会被覆盖。

**kuake CLI 用途：** 上传引流 PDF 到网盘（比 API 上传文件快，且不依赖浏览器登录态）。

**首次使用需登录：**
```bash
# Windows
.\assets\bin\kuake-v1.5.0-windows-amd64.exe login

# Linux
./assets/bin/kuake-v1.5.0-linux-amd64 login
```

登录后凭证存在 `~/.config/kuake/`，CLI 会自动复用。

---

## 3. 使用

### 3.1 命令行

```bash
cd netdisk2weixin

# 基础用法
python main.py baidu              # 只跑百度
python main.py quark              # 只跑夸克
python main.py all                # 百度 + 夸克 一起跑
python main.py web                # 启动 Web 管理界面（默认端口 5003）
python main.py web --port 8080    # 自定义端口

# 覆盖转存数量
python main.py quark --count 10

# 本地关键词过滤（在 API 返回的结果里筛选）
python main.py quark --include 小学              # 标题含"小学"
python main.py quark --include 小学 --include 数学  # 同时含多个关键词
python main.py quark --include 小学 --exclude 答案    # 含"小学"但排除"答案"

# API 关键词（部分 API 不支持中文 kw，建议用 --include 代替）
python main.py quark --kw 小学资料

# 强制跳过缓存，重新请求在线 API
python main.py quark --force

# 只预览不执行（Web 界面也支持）
python main.py quark --preview
```

### 3.2 本地过滤 vs API 关键词

推荐用 **`--include` / `--exclude`**，它在本地对 API 返回的完整结果做 Python 字符串过滤。而 `--kw` 是直接传给远端 API 的搜索参数，很多 API 只支持英文或数字 kw。

```
                    本地过滤 (--include)              API 关键词 (--kw)
──────────────────────────────────────────────────────────────────────────────
请求方式            API 返回全量 → 本地筛选           API 直接传 kw 搜索
中文支持            ✅ 完全支持                       ❌ 多数 API 返回 400
缓存粒度            kw="" 共用缓存                    按 kw 独立缓存文件
推荐度              ⭐⭐⭐ 首选                         ⭐ 仅用于英文/数字场景
```

### 3.3 Web 管理界面

```bash
python main.py web
# 浏览器打开 http://localhost:5003
```

界面里可以：
- 选择平台（夸克 / 百度）
- 配置关键词、包含/排除、数量
- 夸克：先「预览」看资源列表 → 点「确认」执行
- 百度：无预览机制，点运行直接转存
- 结果统一推送到企业微信

### 3.4 API 缓存机制

```
缓存文件：data/cache/api_cache_{cloud_type}_{kw_safe}.json
          例：api_cache_quark_小学资料.json
          例：api_cache_baidu_1.json
过期时间：默认 24 小时（config.yaml 的 cache_expire_hours）
缓存内容：{timestamp, expire_at, data, count}

请求流程：
┌─ 缓存文件存在 且 未过期 ──→ 直接返回缓存
│
└─ 不存在 / 过期 / --force ──→ 请求在线 API
                                 ↓
                          成功 → 写入新缓存
                          失败 → 回退该 kw 的过期缓存（有就用，没有就报错）
```

### 3.5 API 重试策略

在线 API 请求失败时自动重试：

```
第 1/3 次 → 失败 → 等待 6 秒
第 2/3 次 → 失败 → 等待 6 秒
第 3/3 次 → 还失败 → 回退过期缓存 / 返回空
```

超时设置 30 秒，对每个 HTTP 请求生效。

---

## 4. 夸克网盘目录结构说明

### 4.1 转存目标

在 `config.yaml` 配置 `quark.folder_fid`：

| 值 | 含义 |
|---|---|
| `"0"` | 根目录（默认） |
| `"0df18b5a1b264b86a1c46ae5d9ebc748"` | 某个子文件夹的 fid |

### 4.2 转存后目录结构

```
{fid 对应的目录}/
├── 小树老师《小学数学课程合集》.../   ← 转存的分享文件夹（新建分享时自动创建）
│   ├── [原始资源文件]
│   └── 333333.pdf                 ← 引流 PDF（上传到 yinliu_subdir）
├── 《小学教材全解精析》1-6年级/
│   ├── ...
│   └── 333333.pdf
└── ...
```

### 4.3 引流 PDF 上传路径

由 `quark.yinliu_subdir` 控制。设为 `"自动转存"` 时，PDF 上传路径为：

```
/{yinliu_subdir}/{title}/333333.pdf
例：/自动转存/小树老师《小学数学课程合集》.../333333.pdf
```

> 注意：kuake CLI 上传时如果父目录不存在会自动创建，所以 `yinliu_subdir` 可以随便设。

---

## 5. 目录结构

```
netdisk2weixin/
├── main.py              # 统一入口
├── config.yaml          # 你的配置（已 gitignore，含 Cookie）
├── config.example.yaml  # 配置样例
├── adapters/
│   ├── baidu.py         # 百度网盘适配器
│   └── quark.py         # 夸克网盘适配器
├── core/
│   ├── config.py        # 配置加载（支持 yaml + .env）
│   ├── cache.py         # API 缓存 + 本地过滤 + 重试
│   ├── notifier.py      # 企业微信推送
│   └── logger.py        # 日志（Windows GBK 安全输出）
├── web/
│   ├── web_app.py       # Web 界面后端
│   └── templates/       # 前端页面
├── assets/
│   ├── 333333.pdf       # 夸克引流文件
│   └── bin/             # kuake CLI（Windows + Linux 两个版本）
│       ├── kuake-v1.5.0-windows-amd64.exe
│       └── kuake-v1.5.0-linux-amd64
└── data/                # 运行时数据（自动生成）
    ├── cache/           # API 缓存、夸克 cookies.txt
    ├── logs/            # 运行日志
    └── share/           # 分享链接历史记录
```

---

## 6. 常见问题

**Q：报「未配置夸克 Cookie」？**
按上面 2.4 任选一种方式配置。

**Q：百度报错误码 `-6`？**
Cookie 失效或非无痕环境获取，重新抓一份。

**Q：转存数量想改？**
改 `config.yaml` 的 `count`，或命令行 `--count N`。

**Q：Cookie 会泄露到 git 吗？**
不会。`config.yaml`、`.env`、`data/` 都已在 `.gitignore`。

**Q：想定时自动跑？**
可以配合系统的 cron / Windows 任务计划，例如每天 8 点跑一次：
```bash
# Linux cron
0 8 * * * cd /path/to/netdisk2weixin && python3 main.py all >> data/logs/cron.log 2>&1

# Windows 任务计划（PowerShell）
python main.py quark --include 小学 --count 5
```

**Q：Windows 上引流 PDF 上传失败？**
确认 kuake CLI 是 Windows 版（项目会自动选），且已执行过 `.\assets\bin\kuake-v1.5.0-windows-amd64.exe login` 登录。

**Q：API 一直超时 / 400？**
重试机制会自动跑 3 次每次等 6 秒。全部失败后会回退到缓存（如果有）。中文 `--kw` 参数很多 API 不支持，改用 `--include` 本地过滤即可。

**Q：怎么找夸克文件夹的 fid？**
浏览器打开夸克网盘，进入目标文件夹，URL 里会有 `fid=xxxxxx` 之类的参数，或者用 kuake CLI 的 `list` 命令查看。

---

## 7. 与原项目的对应关系

| 原项目 | 合并后 |
|---|---|
| `fresh_baidu2weixin/fresh_baidu2weixin.py` | `adapters/baidu.py` |
| `fresh_quark2weixin/fresh_quark2weixin.py` | `adapters/quark.py` |
| `fresh_quark2weixin/web_app.py` | `web/web_app.py`（加 platform 分发） |
| 两份企业微信推送逻辑 | `core/notifier.py` |
| 两份缓存逻辑 | `core/cache.py` |
| `333333.pdf`、kuake 二进制 | `assets/` |
