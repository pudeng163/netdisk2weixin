# netdisk2weixin 部署说明

> 项目已在本机（Linux 沙箱）克隆、装好依赖、生成 `.env`、运行 `python main.py diagnose` 通过（结论：环境正常）。
> 本说明覆盖两种运行方式：**本地运行** 与 **GitHub Actions 云端定时推送**，并说明如何获取所需的 Cookie / Webhook。

---

## 0. 当前已就绪状态

| 项 | 状态 |
|----|------|
| 项目目录 | `/workspace/netdisk2weixin` |
| Python 虚拟环境 | `.venv/`（已装 requests/httpx/dotenv/PyYAML/colorama/retrying/flask）|
| 依赖安装 | ✅ 已装好 |
| `.env` | ✅ 已根据 `.env.example` 生成（**密钥均为空，需你填写**）|
| kuake / pansou 二进制 | ✅ 已 `chmod +x`，架构匹配（linux/amd64）|
| 本地资源池 | ✅ `资源池.json` 374 条 |
| 环境诊断 | ✅ `python main.py diagnose` 退出码 0 |

> 注意：本沙箱**没有**你的网盘 Cookie 和企业微信 Webhook，所以只能验证“环境与代码结构可运行”，无法真正转存/推送。填好密钥后即可实跑。

---

## 1. 本地运行

### 1.1 激活环境
```bash
cd /workspace/netdisk2weixin
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
```

### 1.2 填写 `.env` 中的密钥
用编辑器打开 `.env`，至少填：
- `QUARK_COOKIE` 与 `KUAKE_COOKIE`（值相同）—— 夸克转存必需
- `WECHAT_WEBHOOK_URL` —— 企业微信机器人 Webhook（推送必需）

（百度模式额外要 `BAIDU_COOKIE`；飞书推送/多维表格按需要填 `FEISHU_*`）

### 1.3 常用命令
```bash
# 只预览（不转存，不消耗容量），先看会抽到哪些资源
python main.py quark --preview

# 正式转存 N 个并推送到企业微信
python main.py quark --count 6

# 文本替换模式：把一段带链接的文本批量换成你自己的链接
python main.py --text "幼小衔接合集： https://pan.quark.cn/s/xxxx"

# 启动 Web 管理界面（默认 http://localhost:5003）
python main.py web
```

---

## 2. 获取密钥（手把手）

### 夸克 Cookie（`QUARK_COOKIE` + `KUAKE_COOKIE`）
1. 浏览器登录 <https://pan.quark.cn>
2. `F12` → Network → 刷新页面 → 点任意请求
3. 复制请求头里 **`cookie`** 整行，原样粘贴到 `.env`（两个字段填**相同**值）
4. 验证：`python main.py diagnose` 不再报“未配置夸克 Cookie”

### 企业微信机器人（`WECHAT_WEBHOOK_URL`）
1. 企业微信 → 群聊 → 群设置 → 群机器人 → 添加自定义机器人
2. 复制 Webhook 地址（`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`）
3. 粘贴到 `.env` 的 `WECHAT_WEBHOOK_URL=`

### 百度 Cookie（仅百度模式）
浏览器**无痕模式**登录 <https://pan.baidu.com>，`F12` 复制 `Cookie` 整行到 `BAIDU_COOKIE=`。

---

## 3. GitHub Actions 云端定时推送（推荐自动化方案）

仓库自带 `.github/workflows/quark-push.yml`，每天 UTC 16:11（北京时间次日 00:11）自动运行 `联动分享.py`（**二号盘**模式，把「小学资料」目录资源联动打包「防失联目录」后推送到企业微信 + 飞书）。

### 3.1 把仓库推到 GitHub
Gitee 仓库不能直接跑 GitHub Actions，需先镜像到 GitHub：
```bash
git remote add github https://github.com/<你的用户名>/netdisk2weixin.git
git push -u github --force        # 包含已提交的 kuake linux 二进制与资源池.json
```

### 3.2 在 GitHub 仓库配置 Secrets
路径：仓库 `Settings → Secrets and variables → Actions → New repository secret`

| Secret 名 | 说明 | 是否必填 |
|-----------|------|----------|
| `WECHAT_WEBHOOK_URL` | 企业微信机器人 Webhook | ✅ 必填 |
| `KUAKE_COOKIE_2` | **二号盘**夸克 Cookie（`联动分享.py` 用 account 2）| ✅ 必填 |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook | 可选 |
| `FEISHU_APP_ID` | 飞书多维表格自建应用 ID | 可选 |
| `FEISHU_APP_SECRET` | 飞书多维表格应用 Secret | 可选 |
| `FEISHU_WIKI_NODE_TOKEN` | 飞书 wiki 节点 token | 可选 |
| `FEISHU_TABLE_ID` | 飞书多维表格 ID | 可选 |

> 脚本在 CI 里会自动把这些 Secrets 写入 `.env`（含 `FEISHU_ENABLED=true`），无需手动提交 `.env`。

### 3.3 启用定时任务
1. GitHub 仓库 `Actions` 标签页 → 找到 **网盘联动分享定时推送**
2. 若是 Fork/镜像，先点 `I understand my workflows, go ahead and enable them`
3. 默认每天自动跑；也可在页面右侧 `Run workflow` **手动触发**，并填推送数量（默认 6）

### 3.4 验证
- 手动 `Run workflow` 一次，看 Actions 日志是否成功推送到企业微信
- 成功后即可坐等每日自动推送

---

## 4. 一键环境脚本
新机器/重装时，直接跑：
```bash
bash setup_env.sh          # Linux/macOS：建 venv + 装依赖 + chmod + 生成 .env + 诊断
```
（Windows 无 bash 时，按脚本内等价命令在 PowerShell 执行。）

---

## 5. 常见问题

| 现象 | 原因 / 解决 |
|------|------------|
| 诊断报 “未配置夸克 Cookie” | 在 `.env` 填 `QUARK_COOKIE` 和 `KUAKE_COOKIE`（相同值）|
| `Exec format error` | 选错二进制；`python main.py diagnose` 会指出应选哪个，按平台/架构来 |
| PanSou 搜不到资源 | 本地资源池 `资源池.json` 优先；若关闭本地池，需先 `python main.py pansou start` |
| 推送 0 条 / 死链 | 开启的链接预检会跳过失效链接；查看 `data/share/dead_links.txt` |
| 企业微信收不到 | 检查 `WECHAT_WEBHOOK_URL` 是否填对、机器人是否仍在群内 |
| GitHub Actions 失败 | 确认 `KUAKE_COOKIE_2`、`WECHAT_WEBHOOK_URL` 两个 Secret 已填，且 `assets/bin/kuake-v1.5.0-linux-amd64` 已提交进仓库 |
