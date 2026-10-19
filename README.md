# 网盘拉新统一转存工具

把原来的「百度网盘转存」和「夸克网盘转存」两个独立项目，合并为一个统一项目。
一份配置、一个入口、统一的企业微信推送和 Web 管理界面，百度 / 夸克可分别或一起跑。

搜索源采用 **PanSou 自建后端**（`http://localhost:8888`），22 个插件源 + 9 个 TG 频道，不依赖外部被封的聚合站，稳定可控。

---

## 资源互助群 & 打赏支持

**学习休闲资源互助公益分享群** —— 国内外影剧、动漫、综艺、图书、课程全覆盖。
在公众号里发送【加群】，拉你进群 👇

![学习休闲资源互助公益分享群](https://ik.imagekit.io/duan/typora-images/clipboard-2026-10-17T08-02-08-139Z-30bca8c7_ybfbrzCkS.jpg)

如果这个项目对你有帮助，欢迎扫码打赏支持 ❤️

| 微信支付 | 支付宝 |
|:---:|:---:|
| ![微信支付](https://ik.imagekit.io/duan/typora-images/mmexport1792066824132_2lkwHLKGI.jpg) | ![支付宝](https://ik.imagekit.io/duan/typora-images/mmexport1792066822824_suCsQ0-Ie.jpg) |

---

## 0. 这个工具是干嘛的

**网盘拉新统一工具**：自动转存 → 引流清理 → 上传自己的引流 PDF → 生成新分享链接 → 推送到企业微信 + 飞书多维表格。

用于「网盘拉新」：别人通过你转发的链接保存文件，你获得拉新奖励。

### 资源池来源（三层优先级）

程序加载资源池按以下优先级，**命中即停**：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| **1（优先）** | **本地资源池 JSON**（`项目根/资源池.json`） | `.env` 的 `LOCAL_POOL_ENABLED=true` 时启用，**完全跳过 PanSou**。当前环境默认启用，1000+ 条资源 |
| 2 | PanSou 自建 API（`http://localhost:8888`） | 本地池不存在/关闭时的兜底，22+ 插件源 + 9 个 TG 频道 |
| 3 | 磁盘缓存 | API 也挂了时的最后兜底 |

> **默认行为已变**：当前环境 `资源池.json` 存在 + `LOCAL_POOL_ENABLED=true`，`python main.py quark --count N` **全程不依赖 PanSou**，直接从本地 JSON 读。PanSou 只在你用 `tools/list_resources.py --refresh` 需要刷新池子时才用到。

### 三种使用模式

| 模式 | 命令 | 场景 |
|------|------|------|
| 批量转存（随机） | `python main.py quark --count 6` | 从本地资源池随机抽 N 个转存 |
| 批量转存（AI 挑） | 同上 + 在 Trae 里说"推 6 个学习类" | AI 按类型从池子挑选 |
| 文本替换 | `python main.py --text "带链接的文本"` | 手里有现成链接文本，批量替换成自己的 |
| **盘内直推（不转存）** | `python tools/share_local.py --random 6` | 网盘容量已满时，直接分享网盘已有资源 |

### 自动流程（不管哪种模式都一样）

1. **加载资源池**（本地 JSON → PanSou → 缓存，三层优先级）
2. **去重过滤**：抽中过的（不管转存成功与否）自动跳过，按原始 URL 精确匹配 + 标题模糊匹配
3. 解析网盘链接 → 验证提取码 → 转存到自己网盘
4. **自动清理引流文件**（命中关键词的 PDF/图片/txt 等）
5. **夸克自动上传** `99999.pdf` 到每个转存的分享文件夹
6. 生成新的分享链接
7. 结果统一推送到企业微信 + 写入飞书多维表格
8. **记录推送历史**：抽中即记 `PICKED`（原始 URL），分享成功追加 `DONE`（新链接）——下次绝对不会重复抽到同一条

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

PanSou 是搜索源，**必须在跑项目前启动**。程序会自动识别当前系统与 CPU 架构，
从 `pansou/dist/` 里挑出对应的二进制（Windows / Linux x64 / Linux arm64 /
macOS x64 / macOS arm64 / Android arm64 全平台覆盖）。

```bash
# 启动（自动选二进制、注入插件列表、后台运行）
python main.py pansou start

# 查看状态（会显示插件数，必须 > 0 才有搜索能力）
python main.py pansou status

# 停止
python main.py pansou stop
```

首次启动建议先跑一次环境诊断，它会打印「当前平台 → 选中哪个二进制 →
架构是否匹配 → 能否执行 → 服务与插件状态」：

```bash
python main.py diagnose
```

**每次电脑开机后需要手动启动一次**（后台长驻进程）。如果没启动，批量转存模式搜不到任何资源。

> **常见坑**：PanSou 没拿到 `ENABLED_PLUGINS` 时会打印
> 「插件列表为空，未加载任何插件」，此时 `/api/health` 依然返回 200，
> 但搜索永远返回 0 条。启动器会主动校验插件数，遇到这种情况直接报错拦截，
> 不会让你在「服务明明起来了却搜不到东西」上浪费时间。
>
> 插件列表默认从 `pansou/docker-compose.yml` 读取，可在 `.env` 用
> `PANSOU_ENABLED_PLUGINS` 覆盖。

**Windows 用户也可以继续用原有脚本**（内部已改为调用 Python 启动器）：
```powershell
powershell -ExecutionPolicy Bypass -File .\start_pansou.ps1
```

**修改端口**只需在 `.env` 里改 `PANSOU_PORT`，Python 侧与启动器会自动跟随，
不需要改任何代码。

**重新编译（仅更新 PanSou 源码时需要）：**
```bash
bash pansou/build_pansou.sh    # 交叉编译全平台，产物在 pansou/dist/
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
QUARK_YINLIU_FILES=assets/99999.pdf,assets/【2026最新】夸克网盘扩容1T教程.mp4
KUAKE_COOKIE_2=          # 第二个夸克账号（盘内直推 --account 2 用，见 3.10 节）

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

> **多账号**：盘内直推模式支持多号，见 [3.10 节](#310-盘内直推模式share_localpy--联动分享py)。
> 在 `.env` 加 `KUAKE_COOKIE_2=`（第二号的 Cookie），运行时用 `--account 2` 切换。

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

项目自带多平台版本，**按当前系统与 CPU 架构自动选择**，无需配置：

| 系统 / 架构 | 自动使用路径 |
|---|---|
| Windows x64 | `assets/bin/kuake-v1.5.0-windows-amd64.exe` |
| Linux x64 | `assets/bin/kuake-v1.5.0-linux-amd64` |
| Linux arm64 | `assets/bin/kuake-v1.5.0-linux-arm64` |
| macOS x64 | `assets/bin/kuake-v1.5.0-darwin-amd64` |
| macOS arm64（M 系列） | 自动回退 `darwin-amd64`，依赖 Rosetta 2 |

选择逻辑不只看操作系统，还会读取二进制的文件头校验真实架构 —— 防止在
Linux ARM 上误选 x86-64 的 ELF 后报出难以排查的 `Exec format error`。

要换自定义版本，在 `.env` 填 `QUARK_KUAKE_CLI=你的路径` 即可（填了就不再自动识别）。
用 `python main.py diagnose` 可以看到实际选中的是哪个文件。

> macOS ARM 没有 kuake 原生版，程序会回退到 x86-64 版本并要求系统装有 Rosetta 2。
> 未安装时会明确提示执行 `softwareupdate --install-rosetta`。

**kuake CLI 用途：** 上传引流文件（PDF / 视频等）到网盘（比 API 上传文件快，且不依赖浏览器登录态）。

**CLI 认证方式：** 读取项目根目录 `.env` 里的 `KUAKE_COOKIE`（v1.5.0 不再用 config.json）。
可以直接测试是否生效：

```bash
# Windows
.\assets\bin\kuake-v1.5.0-windows-amd64.exe user
# Linux / macOS
./assets/bin/kuake-v1.5.0-linux-amd64 user
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
python main.py quark --preview             # 只预览不转存（不推进游标，预览后可直接正式推同一批）
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

## 3.8 本地资源池（资源池.json）

### 生成 / 刷新

```bash
# 从 PanSou 自建后端拉最新全量池，同时导出 JSON
python tools/list_resources.py --refresh --json 资源池.json

# 只读本地已有池子，不请求 PanSou（快）
python tools/list_resources.py --from-pool
```

### 关闭本地池（回退 PanSou API）

`.env` 里加：`LOCAL_POOL_ENABLED=false`

---

## 3.9 去重机制

程序自动维护 `data/share/share_url_total.txt`，**三重过滤**保证不重复推同一条：

| 匹配方式 | 说明 |
|----------|------|
| **原始 URL 精确匹配** | 只要抽中过（不管转存成功与否），下次不会再抽到 |
| **标题精确匹配** | 同标题不同链接也过滤 |
| **标题模糊匹配** | 去掉空白/标点/全角后互相包含也过滤 |

### 记录格式

```
# 抽中瞬间写入（保证下次被过滤）
标题 | 原始链接 | PICKED

# 分享成功后追加
标题 | 新分享链接 | DONE
```

**关键原则**：抽中即记 PICKED，转存失败也不丢。去重过滤看的是 PICKED 里的原始 URL。

---

## 3.10 盘内直推模式（share_local.py / 联动分享.py）

**网盘容量已满、无法再转存时**，直接分享网盘里已有的资源（不转存、不占容量、秒级完成）。

### 基本用法（`tools/share_local.py`）

```bash
# 列出 /自动转存 下全部目录
python tools/share_local.py --list

# 随机推 6 个（自动查重：已推送过的不会重复推）
python tools/share_local.py --random 6
python tools/share_local.py --random 6 --seed 42      # 固定种子，可复现

# 指定名称推送
python tools/share_local.py --names "资源A" "资源B"
```

### 多账号

`.env` 里为每个号配一份 Cookie（`KUAKE_COOKIE_2`、`KUAKE_COOKIE_3`…），用 `--account N` 切换：

```bash
python tools/share_local.py --account 2 --folder / --random 6
```

- 查重记录按账号分文件（`share_url_total_2.txt` 等），互不干扰
- 2 号盘资源在根目录，记得带 `--folder /`

### 联动分享（多资源打包进同一个链接）

`--with-fid` 把指定 fid（如防失联目录）打包进**每个**分享链接（走夸克原生多 fid 分享 API，kuake CLI 只支持单路径）：

```bash
python tools/share_local.py --account 2 --folder "/小学资料（2026年最新）" \
    --random 6 --with-fid 4bc25d8f3d7c4d3d8686e227cb53147d
```

随机模式会自动排除被 `--with-fid` 打包的目录本身，不会单独再分享它。

### 联动分享.py（预设一键版）

预设了「二号盘 + 小学资料目录 + 防失联目录联动 + 不上传引流文件」，直接跑：

```bash
python 联动分享.py            # 默认随机 6 个
python 联动分享.py 10         # 随机 10 个
python 联动分享.py --names "田连元评书大合集"   # 指定名称
```

### 其他开关

| 参数 | 说明 |
|---|---|
| `--folder` | 资源所在目录（默认 `/自动转存`） |
| `--no-yinliu` | 不上传引流文件（容量满时上传会报 `capacity limit`，加上此开关即可） |

> 已推送记录：账号 1 写 `data/share/share_url_total.txt`，账号 N 写 `share_url_total_N.txt`，
> 格式与转存模式相同（标题 | 链接 | DONE）。查重时去空格、去 `(1)` 这类后缀后匹配。

---

## 3.11 Skill 功能（Trae AI 自动调用）

项目内置 3 个 Skill（`.trae/skills/`），在 Trae 里直接用自然语言就能触发。

| Skill | 选资源的逻辑 | 一句话区别 |
|-------|------------|-----------|
| **push-random** | 程序真·随机抽 random.sample | 随便挑 |
| **push-resource** | AI 按预设类型表逐条阅读 | "我要学习类" |
| **push-from-cache** | AI 按你当场说的临时领域判断 | "选小学语文英语类的" |

### 触发示例

**push-random** — "随机推 6 个" / "任挑几个推"
```bash
python main.py quark --count 6
python main.py quark --count 6 --exclude 短剧
```

**push-resource** — "推 8 个自我提升类的"

**push-from-cache** — "按运营副业领域筛 3 个"

### 成功率提示

- 随机抽取成功率约 60~80%（源被封属正常损耗）
- 要凑够 N 个成功就**重跑**（已抽中的会自动跳过）
- 资源池 80%+ 是短剧/影视，要干货请用 push-resource

---



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
│   │   ├── 99999.pdf                    ← 引流 PDF 在分享文件夹内 ✅
│   │   └── 【2026最新】夸克网盘扩容1T教程.mp4   ← 引流视频同样投放 ✅
│   ├── 《小学教材全解精析》1-6年级/
│   │   ├── [原始资源文件...]
│   │   └── 99999.pdf
│   └── ...
```

引流 PDF **上传到每个转存的资源文件夹内**，这样用户打开你的分享链接时，能同时看到原始文件和引流 PDF。

### 4.3 引流文件说明（支持 PDF + 视频）

| 项 | 值 |
|---|---|
| 默认文件 | `assets/99999.pdf` |
| 配置 | `.env` 的 `QUARK_YINLIU_FILES`（逗号分隔，可多个文件） |
| 兼容旧配置 | `QUARK_YINLIU_PDF`（未配 `QUARK_YINLIU_FILES` 时生效） |
| 上传方式 | kuake CLI（秒传，不占空间） |
| 上传路径 | `{QUARK_FOLDER_FID}/{转存的资源名}/{文件名}` |
| 关闭 | 把 `QUARK_YINLIU_FILES=` 留空并删掉 `QUARK_YINLIU_PDF` |

配置示例（同时投放 PDF 和视频教程）：

```bash
QUARK_YINLIU_FILES=assets/99999.pdf,assets/【2026最新】夸克网盘扩容1T教程.mp4
```

所有文件都会上传到**每一个**转存的资源文件夹内，同名自动跳过不存在的文件。
自己的引流文件会自动加入清理排除名单，不会被引流清理规则误删。

---

## 5. 目录结构

```
netdisk2weixin/
├── main.py              # 统一入口（所有模式都走这里）
├── README.md            # 本文件
├── 联动分享.py          # 一键联动分享（二号盘小学资料 + 防失联目录打包）
├── .env                 # 唯一配置文件（已 gitignore，含 Cookie）
├── .env.example         # 配置样例
├── 资源池.json          # 本地资源池（默认启用，1000+ 条夸克资源）
├── start_pansou.ps1     # PanSou 启动脚本（Windows 兼容入口）
│
├── .trae/skills/        # Trae AI Skill（自然语言触发的推送能力）
│   ├── push-random/     #   随机抽取 N 个
│   ├── push-resource/   #   AI 按类型挑
│   └── push-from-cache/ #   AI 按临时领域挑
│
├── adapters/            # 平台适配器
│   ├── __init__.py      #   平台路由注册
│   ├── baidu.py         #   百度网盘转存+分享
│   └── quark.py         #   夸克网盘转存+分享（核心，约 900+ 行）
│
├── core/                # 核心服务层
│   ├── config.py        #   .env 配置加载 + 全局默认值
│   ├── cache.py         #   资源池加载（三层优先级：本地 JSON → PanSou → 磁盘缓存）+ 重试
│   ├── binresolver.py   #   多平台二进制自动识别（按 os + arch 选文件）
│   ├── logger.py        #   日志（Windows GBK 安全输出）
│   ├── notifier.py      #   企业微信 Webhook 推送
│   ├── feishu_client.py #   飞书多维表格写入（追加模式，按名称去重）
│   ├── yinliu_filter.py #   引流文件自动清理（保守/激进两种模式）
│   ├── platform_probe.py#   平台探测（Cookie 有效性、API 可达性预检）
│   ├── text_replacer.py #   文本替换模式（识别文本里的网盘链接→转存→替换→推送）
│   └── pansou_launcher.py # PanSou 启动/停止/状态/重启 + 自动选二进制
│
├── tools/               # 辅助工具（独立运行的脚本）
│   ├── share_local.py   #   盘内直推：分享网盘已有资源（多账号/随机查重/多fid联动）
│   ├── list_resources.py #  导出空关键词全量资源清单（刷新/导出 JSON）
│   └── diagnose.py      #  环境诊断（平台→二进制→服务→插件一条龙）
│
├── web/                 # Web 管理界面
│   ├── web_app.py       #   Flask 后端（平台分发：夸克/百度 + 预览 + 执行）
│   └── templates/       #   前端页面
│
├── assets/              # 静态资源
│   ├── 99999.pdf        #   夸克引流文件（自动上传到每个分享文件夹）
│   ├── *.mp4           #   其他引流文件（视频教程等，同样自动上传）
│   └── bin/             #   kuake CLI（多平台，自动识别）
│       ├── kuake-v1.5.0-windows-amd64.exe
│       ├── kuake-v1.5.0-linux-amd64
│       ├── kuake-v1.5.0-linux-arm64
│       └── kuake-v1.5.0-darwin-amd64
│
├── pansou/              # PanSou 自建后端（搜索源）
│   ├── dist/            #   多平台二进制（启动器自动识别，只认这里）
│   │   ├── pansou-windows-amd64.exe
│   │   ├── pansou-windows-arm64.exe
│   │   ├── pansou-linux-amd64
│   │   ├── pansou-linux-arm64
│   │   ├── pansou-darwin-amd64
│   │   ├── pansou-darwin-arm64
│   │   └── pansou-android-arm64
│   ├── build_pansou.sh  #   交叉编译脚本（一次产出 dist/ 全平台）
│   ├── cache/           #   PanSou 运行时缓存（自动生成）
│   ├── go.mod / go.sum  #   Go 模块依赖
│   └── ...              #   PanSou 源码（api/ plugin/ service/ model/ util/）
│
├── tests/               # 单元测试
│   └── test_platform_resolve.py
│
└── data/                # 运行时数据（自动生成，已 gitignore）
    ├── cache/           #   API 缓存
    ├── logs/            #   运行日志（含 pansou.log、yinliu_clean_日期.txt）
    ├── share/           #   推送历史记录（share_url_total.txt，标题 | url | PICKED/DONE）
    └── pansou.pid       #   PanSou 进程号（供 stop 使用）
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

**Q：网盘容量满了转存不了怎么办？**
用盘内直推模式（见 [3.10 节](#310-盘内直推模式share_localpy--联动分享py)）：直接分享网盘里已有的资源，不转存、不占容量：
```bash
python tools/share_local.py --random 6
```
引流文件上传也会报 `capacity limit`，加 `--no-yinliu` 跳过即可。

**Q：API 一直超时 / 400？**
重试机制会自动跑 3 次每次等 6 秒。全部失败后会回退到缓存（如果有）。如果是 PanSou 自建后端返回的 400，多半是插件源临时抽风，换个关键词或等几分钟再试。

**Q：搜不到任何资源 / 返回空？**
先跑诊断，它会一次性告诉你二进制、服务、插件是否都正常：
```bash
python main.py diagnose
```
重点看 **插件数**。如果服务在跑但插件数为 0，说明 `ENABLED_PLUGINS` 没传进去，
搜索会永远返回 0 条 —— 这是最容易踩的坑。正常启动一份带插件的实例：
```bash
python main.py pansou start
python main.py pansou status     # 确认「插件数」是个正数
```
**每次电脑开机后需要手动启动一次**（后台长驻进程）。没启动的话批量转存模式必然搜不到任何东西。

**Q：PanSou 启动报端口占用 / address already in use？**
默认端口 8888 被占用了。两种解决方式：
1. 关掉占用端口的进程。Windows：`netstat -ano | findstr :8888` → 拿到 PID → `taskkill /PID 进程号 /F`；Linux/macOS：`lsof -i :8888` 或 `fuser -k 8888/tcp`
2. 改端口：在 `.env` 里写 `PANSOU_PORT=8889` 即可，**不需要改任何代码**，
   搜索地址与启动器会自动跟随。

**Q：启动时报 `Exec format error`？**
选中的二进制与当前平台架构不匹配。跑 `python main.py diagnose`，
它会打印每个候选文件的真实架构。常见于：手动在 `.env` 指定了
`QUARK_KUAKE_CLI` 却填了别的平台的版本，或 `assets/bin/`、
`pansou/dist/` 里的文件被替换成了错误架构的构建。

**Q：`start_pansou.ps1` 启动后一闪而过？**
在 PowerShell 里直接执行看完整错误信息，或改用
`python main.py pansou start` 获取更清晰的错误提示（会打印日志末尾若干行）。
常见原因：
- 对应的 `pansou/dist/pansou-<os>-<arch>` 不存在 → 跑 `bash pansou/build_pansou.sh` 重新编译
- 端口被占用 → 见上一条
- Go 版本不够 → 仅编译时需要 Go 1.21+，运行已编译好的二进制不需要 Go

**Q：PanSou 搜不到某个特定关键词？**
PanSou 有 22 个插件源 + 9 个 TG 频道，不同插件覆盖不同资源类型。多换几个关键词试试，或者用 `--include` 本地过滤把 PanSou 返回的结果再精准筛选一遍。

**Q：怎么找夸克文件夹的 fid？**
浏览器打开夸克网盘，进入目标文件夹，URL 里会有 `fid=xxxxxx` 之类的参数，或者用 kuake CLI：
```bash
# Windows
.\assets\bin\kuake-v1.5.0-windows-amd64.exe list /
# Linux / macOS
./assets/bin/kuake-v1.5.0-linux-amd64 list /
```

**Q：kuake CLI 版本想换？**
把新文件放进 `assets/bin/`，然后二选一：
1. 保持项目使用的命名规则（`kuake-v1.5.0-<os>-<arch>[.exe]`），自动识别即可生效
2. 在 `.env` 填 `QUARK_KUAKE_CLI=D:/tools/你的版本.exe` 显式指定路径

注意：显式指定时会**完全跳过**自动识别，程序只校验文件是否存在，不再校验架构。
如果指定了与当前平台不匹配的二进制，运行时会出现 `Exec format error`。

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
