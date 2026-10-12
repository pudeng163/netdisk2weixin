# 网盘拉新统一转存工具

把原来的「百度网盘转存」和「夸克网盘转存」两个独立项目，合并为一个统一项目。
一份配置、一个入口、统一的企业微信推送和 Web 管理界面，百度 / 夸克可分别或一起跑。

搜索源采用 **PanSou 自建后端**（`http://localhost:8888`），22 个插件源 + 9 个 TG 频道，不依赖外部被封的聚合站，稳定可控。

---

## 0. 这个工具是干嘛的

从 **PanSou 自建 API** 抓取网盘分享链接 → 自动转存到**你自己的网盘** → 生成新的分享链接 → 推送到企业微信群 + 写入飞书多维表格。
用于「网盘拉新」：别人通过你转发的链接保存文件，你获得拉新奖励。

### 两种使用模式

| 模式 | 命令 | 场景 |
|------|------|------|
| 批量转存 | `python main.py quark --count 10` | 从 PanSou 自建后端自动抓一批资源转存 |
| 文本替换 | `python main.py --text "带链接的文本"` | 手里有现成链接文本，批量替换成自己的 |

自动流程：
1. 解析网盘链接 → 验证提取码 → 转存到自己网盘
2. **自动清理引流文件**（命中关键词的 PDF/图片/txt 等，见下方）
3. 夸克网盘自动上传你的引流文件 `99999.pdf` 到分享文件夹
4. 生成新的分享链接替换原链接
5. 结果统一推送到企业微信 + 写入飞书多维表格

---

## 1. 环境要求

- **Python 3.8+**（本机是 3.10，已验证）
- **Go 1.21+**（编译 PanSou 自建后端用，已编译好可跳过）
- 依赖库：见 `requirements.txt`

```bash
cd netdisk2weixin
pip install -r requirements.txt
```

> **本项目已移除 playwright**，夸克不再启动浏览器。
> 你只需提供 Cookie：把浏览器登录 pan.quark.cn 后的 Cookie 整串填到 `.env` 的 `QUARK_COOKIE`，
> 或保存到 `cookies/quark_cookie.txt`。两者都没有时程序会直接报错提示（不会静默失败）。
>
> 获取 Cookie 的方式：浏览器登录 pan.quark.cn → F12 → Network → 任意请求 → 复制请求头里的 `cookie` 整行。

### 1.1 PanSou 自建后端（重要！）

PanSou 是搜索源，**必须在跑项目前启动**。已编译好的可执行文件在 `pansou/pansou.exe`（Windows）。

```powershell
# Windows 启动（在项目根目录执行）
powershell -ExecutionPolicy Bypass -File .\start_pansou.ps1

# 看到 "PanSou starting on http://localhost:8888" 就启动成功了
# 打开浏览器访问 http://localhost:8888/api/health 返回 {"status":"ok"} 即正常
```

**每次电脑开机后需要手动启动一次**（后台长驻进程）。如果没启动，批量转存模式搜不到任何资源。

**重新编译（仅更新 PanSou 源码时需要）：**
```powershell
# Go 代理换国内七牛云，快很多
$env:GOPROXY = "https://goproxy.cn,direct"
cd pansou
go build -ldflags="-s -w" -o pansou.exe .
```

---

## 2. 配置（重要）

### 2.1 唯一配置文件

所有配置统一写在项目根目录的 **`.env`** 文件里，没有其他配置文件。

```bash
# 从样例复制一份：
cp .env.example .env
```

### 2.2 编辑 `.env`

```env
# ====== 全局默认 ======
COUNT=5                  # 每次转存资源数量（可被各平台覆盖）
CACHE_EXPIRE_HOURS=24    # API 缓存过期小时
TARGET_FOLDER=转存资源    # 百度网盘目标文件夹

# ====== 企业微信机器人 ======
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key

# ====== 百度网盘 ======
BAIDU_COOKIE=            # 必填，转存百度才需要
# BAIDU_COUNT=           # 留空则用全局 COUNT
# BAIDU_TARGET_FOLDER=   # 留空则用全局 TARGET_FOLDER

# ====== 夸克网盘 ======
QUARK_COOKIE=            # Python 主流程用的夸克 Cookie
KUAKE_COOKIE=            # kuake CLI 专用（填和 QUARK_COOKIE 相同的值即可）
# QUARK_COUNT=           # 留空则用全局 COUNT
# QUARK_KUAKE_CLI=       # 留空则自动选系统默认路径，换版本时自定义
QUARK_FOLDER_FID=0       # 转存目标文件夹 fid，"0" = 根目录
QUARK_YINLIU_PDF=assets/99999.pdf

# ====== 飞书多维表格（可选）======
FEISHU_ENABLED=false
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_WIKI_NODE_TOKEN=    # wiki 页面 URL 里 /wiki/ 后面那串
FEISHU_TABLE_ID=            # 多维表格 URL 里 ?table= 后面那串
```

### 2.3 获取百度 Cookie

1. 浏览器**无痕模式**打开 [pan.baidu.com](https://pan.baidu.com) 并登录
2. 按 F12 → Network 面板
3. 刷新页面，点任意请求，复制请求头里的 `Cookie` 整个值
4. 粘贴到 `.env` 的 `BAIDU_COOKIE=`

> 如果转存时报错 `-6`，说明 Cookie 已失效或非无痕环境获取，重新用无痕模式抓一份即可。

### 2.4 获取夸克 Cookie

夸克 Cookie **要同时填两个字段**：`QUARK_COOKIE` 和 `KUAKE_COOKIE`，值完全相同。

1. 浏览器打开 [pan.quark.cn](https://pan.quark.cn) 并登录
2. 按 F12 → Network 面板
3. 刷新页面，点任意请求，复制请求头里的 `Cookie` 整个字符串
4. 粘贴到 `.env`：
   ```
   QUARK_COOKIE=__sdid=...; isg=...; ...
   KUAKE_COOKIE=__sdid=...; isg=...; ...
   ```

> 两个字段用途不同：
> - `QUARK_COOKIE` — Python 主流程用 httpx 调夸克 API（转存、分享）
> - `KUAKE_COOKIE` — `kuake-v1.5.0-*.exe` CLI 用（引流 PDF 上传、fid 转路径）
>
> Cookie 刷新时记得**两个一起更新**。

### 2.5 飞书多维表格（可选）

自动把转存记录写入飞书多维表格。需要：

1. 在 [飞书开放平台](https://open.feishu.cn/app) 创建自建应用
2. 权限：`wiki:wiki:readonly` + `bitable:app`
3. 把应用添加为目标 wiki 文档的协作者（右上角「...」→ 添加文档应用 → 给可编辑）
4. 在 `.env` 里开启：
   ```
   FEISHU_ENABLED=true
   FEISHU_APP_ID=cli_xxxxx
   FEISHU_APP_SECRET=xxxxx
   FEISHU_WIKI_NODE_TOKEN=OAS1wpySSiedCDkgnjycCza8nFf
   FEISHU_TABLE_ID=tblgsMxc3clOlIc5
   ```

多维表格需要 4 个字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 资源名称 | 文本（主键，用于去重） | 转存后的资源标题 |
| 资源链接 | URL | 你自己的新夸克分享链接 |
| 文件大小 | 文本 | 夸克 API 返回的大小（如 305.9MB） |
| 更新时间 | 文本 | 当天日期 |

> 写入是**追加模式**——每次写入前先拉已有记录，跳过重名的（按"资源名称"匹配），不会覆盖已有数据。

### 2.6 kuake CLI（夸克网盘命令行工具）

项目自带两个版本，**自动按操作系统选择**，无需配置：

| 系统 | 自动使用路径 |
|---|---|
| Windows | `assets/bin/kuake-v1.5.0-windows-amd64.exe` |
| Linux | `assets/bin/kuake-v1.5.0-linux-amd64` |

如果要换自定义版本，在 `.env` 填 `QUARK_KUAKE_CLI=你的路径` 即可。

**kuake CLI 用途：** 上传引流 PDF 到网盘（比 API 上传文件快，且不依赖浏览器登录态）。

**CLI 认证方式：** 读取项目根目录 `.env` 里的 `KUAKE_COOKIE`（v1.5.0 不再用 config.json）。
可以直接测试是否生效：

```bash
.\assets\bin\kuake-v1.5.0-windows-amd64.exe user
```

返回 `success: true` 就是通的。

---

## 3. 使用

### 3.1 两种模式

```bash
cd netdisk2weixin

# ============ 批量转存模式（从 PanSou 自建后端自动抓）============
python main.py quark                       # 只跑夸克，默认 COUNT
python main.py quark --count 10            # 转存 10 个
python main.py quark --include 小学         # 本地关键词过滤
python main.py quark --force               # 强制跳过缓存
python main.py quark --preview             # 只预览不转存
python main.py baidu                       # 只跑百度
python main.py all                         # 百度 + 夸克 一起跑

# ============ 文本替换模式（手里有现成链接文本）============
python main.py --text "把带网盘链接的完整文本贴这里"
python main.py --text-file resource.txt    # 从文件读取文本
```

### 3.2 文本替换模式详解

这是最常用的模式。给一段带网盘链接的文本，自动替换成你的：

```bash
python main.py --text "幼小衔接合集： https://pan.quark.cn/s/98dbff2e31a7"
```

**自动做的事：**
1. 识别文本里所有网盘链接（夸克 / 百度）
2. 逐个转存到你自己的网盘
3. 生成新分享链接替换原链接
4. 上传引流 PDF 到转存文件夹
5. 推送企业微信 + 写入飞书多维表格

**支持多个链接一次性替换：**

```bash
python main.py --text "
链接1： https://pan.quark.cn/s/b6155f77518f
链接2： https://pan.quark.cn/s/629f2aa4ba88
链接3： https://pan.quark.cn/s/f8ba0e7ec629
"
```

**从文件读取：**

```bash
python main.py --text-file resources.txt
```

**返回的 `results` 数组里每个元素包含：**
- `original_url` — 原始链接
- `new_url` — 替换后的新链接
- `title` — 转存到的文件名
- `size` — 文件大小（如 `305.9MB`）
- `status` — success / error
- `feishu_result` — 飞书写入状态

### 3.3 批量转存模式详解

```bash
# 基础用法
python main.py baidu
python main.py quark
python main.py all
python main.py web                # 启动 Web 管理界面（默认端口 5003）
python main.py web --port 8080   # 自定义端口

# 覆盖转存数量
python main.py quark --count 10

# 本地关键词过滤（推荐）
python main.py quark --include 小学
python main.py quark --include 小学 --include 数学
python main.py quark --include 小学 --exclude 答案

# API 关键词（多数 API 只支持英文/数字）
python main.py quark --kw 小学资料

# 强制跳过缓存
python main.py quark --force

# 只预览不执行
python main.py quark --preview
```

### 3.4 本地过滤 vs API 关键词

推荐用 **`--include` / `--exclude`**，它在本地对 PanSou 返回的完整结果做 Python 字符串过滤。而 `--kw` 是直接传给 PanSou 的搜索参数，PanSou 会转发给各插件源，部分插件对中文 kw 支持不好。

```
                    本地过滤 (--include)              API 关键词 (--kw)
──────────────────────────────────────────────────────────────────────────────
请求方式            PanSou 返回全量 → 本地筛选         PanSou 直接传 kw 给插件
中文支持            ✅ 完全支持                        ⚠️ 部分插件返回空结果
缓存粒度            kw="" 共用缓存                    按 kw 独立缓存文件
推荐度              ⭐⭐⭐ 首选                         ⭐ 仅用于英文/数字场景
```

### 3.5 Web 管理界面

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

### 3.6 API 缓存机制

```
缓存文件：data/cache/api_cache_{cloud_type}_{kw_safe}.json
          例：api_cache_quark_小学资料.json
过期时间：默认 24 小时（.env 的 CACHE_EXPIRE_HOURS）
缓存内容：{timestamp, expire_at, data, count}

请求流程：
┌─ 缓存文件存在 且 未过期 ──→ 直接返回缓存
│
└─ 不存在 / 过期 / --force ──→ 请求 PanSou 自建后端
                                 ↓
                          成功 → 写入新缓存
                          失败 → 回退该 kw 的过期缓存（有就用，没有就报错）
```

### 3.7 API 重试策略

PanSou 请求失败时自动重试：

```
第 1/3 次 → 失败 → 等待 6 秒
第 2/3 次 → 失败 → 等待 6 秒
第 3/3 次 → 还失败 → 回退过期缓存 / 返回空
```

超时设置 30 秒，对每个 HTTP 请求生效。

---

## 4. 夸克网盘目录结构说明

### 4.1 转存目标

在 `.env` 配置 `QUARK_FOLDER_FID`：

| 值 | 含义 |
|---|---|
| `"0"` | 根目录（默认） |
| `"0df18b5a1b264b86a1c46ae5d9ebc748"` | 某个子文件夹的 fid |

### 4.2 转存后目录结构（含引流 PDF）

```
{fid 对应的目录}/
├── 自动转存/                           ← QUARK_FOLDER_FID 指向这里
│   ├── 小树老师《小学数学课程合集》.../   ← 转存的分享文件夹（对外分享的是这个）
│   │   ├── [原始资源文件...]
│   │   └── 99999.pdf                    ← 引流 PDF 在分享文件夹内 ✅
│   ├── 《小学教材全解精析》1-6年级/
│   │   ├── [原始资源文件...]
│   │   └── 99999.pdf
│   └── ...
```

引流 PDF **上传到每个转存的资源文件夹内**，这样用户打开你的分享链接时，能同时看到原始文件和引流 PDF。

### 4.3 引流 PDF 说明

| 项 | 值 |
|---|---|
| 默认文件 | `assets/99999.pdf` |
| 配置 | `.env` 的 `QUARK_YINLIU_PDF` |
| 上传方式 | kuake CLI（秒传，不占空间） |
| 上传路径 | `{QUARK_FOLDER_FID}/{转存的资源名}/99999.pdf` |
| 关闭 | 把 `QUARK_YINLIU_PDF=` 留空 |

---

## 5. 目录结构

```
netdisk2weixin/
├── main.py              # 统一入口
├── SKILL.md             # AI 使用指南（Trae 自动加载）
├── start_pansou.ps1     # PanSou 自建后端启动脚本（Windows）
├── README.md            # 本文件
├── .env                 # 唯一配置文件（已 gitignore，含 Cookie）
├── .env.example         # 配置样例
├── adapters/
│   ├── baidu.py         # 百度网盘适配器
│   └── quark.py         # 夸克网盘适配器
├── core/
│   ├── config.py        # 配置加载（只从 .env 读）
│   ├── cache.py         # API 缓存 + 本地过滤 + 重试（API_URL 指向 PanSou）
│   ├── text_replacer.py # 文本替换（网盘链接识别 + 批量转存 + 飞书写入）
│   ├── feishu_client.py # 飞书多维表格写入客户端（追加模式，按名称去重）
│   ├── notifier.py      # 企业微信推送
│   └── logger.py        # 日志（Windows GBK 安全输出）
├── web/
│   ├── web_app.py       # Web 界面后端
│   └── templates/       # 前端页面
├── assets/
│   ├── 99999.pdf        # 夸克引流文件（自动上传到每个分享文件夹）
│   └── bin/             # kuake CLI
│       ├── kuake-v1.5.0-windows-amd64.exe
│       └── kuake-v1.5.0-linux-amd64
├── pansou/              # PanSou 自建后端（搜索源）
│   ├── pansou.exe       # 已编译好的 Windows 可执行文件
│   ├── start.ps1        # PanSou 自带启动脚本（可忽略，用根目录的 start_pansou.ps1）
│   ├── cache/           # PanSou 运行时缓存（自动生成）
│   ├── go.mod / go.sum  # Go 模块依赖
│   └── ...              # PanSou 源码
└── data/                # 运行时数据（自动生成）
    ├── cache/           # API 缓存
    ├── logs/            # 运行日志
    └── share/           # 分享链接历史记录
```

---

## 6. 常见问题

**Q：报「未配置夸克 Cookie」？**
在 `.env` 填写 `QUARK_COOKIE=`。如果 kuake CLI 也报 `KUAKE_COOKIE is not set`，再填 `KUAKE_COOKIE=`（和前者填相同的值）。

**Q：百度报错误码 `-6`？**
Cookie 失效或非无痕环境获取，重新抓一份。

**Q：转存数量想改？**
改 `.env` 的 `COUNT=`，或命令行 `--count N`。

**Q：Cookie 会泄露到 git 吗？**
不会。`.env`、`data/` 都已在 `.gitignore`。

**Q：想定时自动跑？**
可以配合系统的 cron / Windows 任务计划：
```bash
# Linux cron
0 * * * * cd /path/to/netdisk2weixin && python3 main.py quark --count 6 >> data/logs/cron.log 2>&1

# Windows 任务计划（PowerShell）
python main.py quark --include 小学 --count 6
```

**Q：Windows 上引流 PDF 上传失败 / 报 `KUAKE_COOKIE is not set`？**
v1.5.0 的 kuake CLI **不再读 `assets/bin/config.json`**，它要读项目根目录 `.env` 里的 `KUAKE_COOKIE`。确认 `.env` 里有这一行：
```
KUAKE_COOKIE=你的完整cookie字符串
```
然后用 `.\assets\bin\kuake-v1.5.0-windows-amd64.exe user` 测试，返回 `success: true` 即生效。

**Q：API 一直超时 / 400？**
重试机制会自动跑 3 次每次等 6 秒。全部失败后会回退到缓存（如果有）。如果是 PanSou 自建后端返回的 400，多半是插件源临时抽风，换个关键词或等几分钟再试。

**Q：搜不到任何资源 / 返回空？**
先确认 PanSou 自建后端是否在跑：
```powershell
curl http://localhost:8888/api/health
```
返回 `{"status":"ok"}` 才正常。如果没启动，执行：
```powershell
powershell -ExecutionPolicy Bypass -File .\start_pansou.ps1
```
**每次电脑开机后需要手动启动一次**（后台长驻进程）。没启动的话批量转存模式必然搜不到任何东西。

**Q：PanSou 启动报端口占用 / address already in use？**
默认端口 8888 被占用了。两种解决方式：
1. 关掉占用 8888 端口的进程：`netstat -ano | findstr :8888` → 拿到 PID → `taskkill /PID 进程号 /F`
2. 改端口：编辑 `start_pansou.ps1` 里的 `$env:PORT = "8888"` 换成别的（比如 8889），同时改 `core/cache.py` 里的 `API_URL = "http://localhost:8889/api/search"`

**Q：`start_pansou.ps1` 启动后一闪而过？**
在 PowerShell 里直接执行看完整错误信息，常见原因：
- `pansou/pansou.exe` 不存在 → 需要先编译或确保文件没被杀毒软件隔离
- 路径问题 → 必须从项目根目录（`start_pansou.ps1` 所在目录）执行，不要在其他目录用绝对路径调用
- Go 版本不够 → 仅编译时需要 Go 1.21+，运行已编译好的 exe 不需要 Go

**Q：PanSou 搜不到某个特定关键词？**
PanSou 有 22 个插件源 + 9 个 TG 频道，不同插件覆盖不同资源类型。多换几个关键词试试，或者用 `--include` 本地过滤把 PanSou 返回的结果再精准筛选一遍。

**Q：怎么找夸克文件夹的 fid？**
浏览器打开夸克网盘，进入目标文件夹，URL 里会有 `fid=xxxxxx` 之类的参数，或者用 kuake CLI：
```bash
.\assets\bin\kuake-v1.5.0-windows-amd64.exe list /
```

**Q：kuake CLI 版本想换？**
替换 `assets/bin/` 下对应平台的二进制文件名（保持 `kuake-v1.5.0-windows-amd64.exe` / `kuake-v1.5.0-linux-amd64` 命名），或者在 `.env` 填 `QUARK_KUAKE_CLI=D:/tools/你的版本.exe` 指定自定义路径。

**Q：`.env` 里 QUARK_COOKIE 和 KUAKE_COOKIE 为什么要填两次？**
两个程序读的字段名不同：Python 主流程读 `QUARK_COOKIE`，kuake CLI 读 `KUAKE_COOKIE`（这是 CLI 自身的字段约定）。两个值完全相同，是同一份 cookie 字符串。

**Q：飞书写入报错 `99991672 无权限`？**
两个原因：① 自建应用没开 wiki 权限（`wiki:wiki:readonly`）；② 应用没加到目标 wiki 文档的协作者。都确认一下。

**Q：飞书 Url 字段写入失败？**
飞书的 URL 类型字段（type=15）不能传纯字符串，必须传 `{"link": "https://...", "text": "下载链接"}` 对象。代码已处理，正常用就行。

**Q：替换的时候引流 PDF 上传报 `400 目标位置不是目录`？**
已修复。原因是旧代码上传路径不对——直接往 `QUARK_FOLDER_FID/` 根目录丢 PDF，用户分享时看不到。现在改成上传到**转存的资源文件夹内**（和原始资源同一层级），用户打开分享链接时能同时看到原始文件和引流 PDF。

**Q：引流文件清理会不会删掉我自己的正经 PDF？**
默认保守模式**不会**——只删文件名含引流关键词的（如"微信加我.pdf"），正经资料如"2024考研数学真题.pdf"完全安全。如果开了激进模式（`CLEAN_YINLIU_AGGRESSIVE=true`），则小体积 PDF/图片也会删，可能误伤，谨慎开启。

**Q：我想保留某些带"微信"字样的文件怎么办？**
把关键词加到 `CLEAN_YINLIU_EXCLUDES`：
```env
CLEAN_YINLIU_EXCLUDES=99999,微信收款码
```
命中排除词的文件直接跳过，不做任何判断。

**Q：引流清理功能怎么关闭？**
`.env` 里加一行：
```env
CLEAN_YINLIU=false
```

---

## 7. 与原项目的对应关系

| 原项目 | 合并后 |
|---|---|
| `fresh_baidu2weixin/fresh_baidu2weixin.py` | `adapters/baidu.py` |
| `fresh_quark2weixin/fresh_quark2weixin.py` | `adapters/quark.py` |
| `fresh_quark2weixin/web_app.py` | `web/web_app.py`（加 platform 分发） |
| 两份企业微信推送逻辑 | `core/notifier.py` |
| 两份缓存逻辑 | `core/cache.py` |
| `333333.pdf`、kuake 二进制 | `assets/`（PDF 已改名为 `99999.pdf`） |
| （新增） | `core/text_replacer.py` — 文本替换功能 |
| （新增） | `core/feishu_client.py` — 飞书多维表格写入 |
| （新增） | `SKILL.md` — AI 自动调用指南 |
