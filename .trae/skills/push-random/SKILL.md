---
name: "push-random"
description: "从 PanSou 已获取的资源池中随机任挑 N 个，一键转存→引流清理→上传99999.pdf→永久分享→企业微信+飞书推送。Invoke when user asks to 随机挑/随机推/任挑 N 个/随便挑几个 资源，或说 from the pool pick N randomly."
---

# Push Random - 从资源池随机任挑 N 个推送

用户说"随机挑 N 个"、"任挑几个推"、"从池子里随便挑 N 个转存"时，**立即使用本 Skill**。

> **与 `push-resource` 的区别**：
> - `push-resource` = **AI 按类型挑选**（要"学习类"就逐条阅读挑出学习类），用于有明确类型要求的场景
> - `push-random` = **真·随机抽取**（不筛类型，由程序 `random.sample` 决定），用于"随便挑几个"、快速填充推送量、或做资源多样性测试
>
> 两者共用同一条转存→分享→推送链路，差别只在**选哪些**。

---

## 核心流程（三步）

```
① 检查/启动 PanSou  →  ② 空关键词拉全量池  →  ③ 不传 --include，程序自动随机抽 N 个并推送
```

> **关键点**：`main.py` 在**未指定 `--include`** 时，会对本地过滤后的资源池执行 `random.sample(池, N)`（见 `adapters/quark.py` 的 `selected = random.sample(quark_data, count)`）。
> 所以"随机任挑"**无需自己写随机逻辑** —— 只要保持 `--kw` 与 `--include` 都留空，程序天然就是随机。

---

## 0. 平台适配说明（重要）

本 Skill 的命令示例按当前运行环境给出。执行前先确认环境：

| 环境 | PanSou 启动方式 | Shell |
|---|---|---|
| **Linux / macOS（当前沙箱）** | `/tmp/run_pansou.sh` 或 `bash pansou/run.sh` | bash |
| Windows（用户本机） | `powershell -File .\start_pansou.ps1` | PowerShell |

> 若 `~/.env` / `.env` 里的 `QUARK_COOKIE` 过期，转存会报"获取 stoken 失败"，需提示用户重新抓 Cookie。

---

## 1. 前置条件检查

### 步骤 1：检测 PanSou 是否在跑

```bash
curl -s -m 5 "http://localhost:8888/api/search?kw=test&cloud_types=quark" -o /dev/null -w "PanSou: %{http_code}\n"
```

- 返回 `PanSou: 200` → 进入第 2 节
- 返回 `PanSou: 000`（连不上）→ 执行步骤 2

### 步骤 2：启动 PanSou（后台长驻）

```bash
# 若已存在启动脚本，直接复用
/tmp/run_pansou.sh
```

若脚本不存在，用下列内容创建 `/tmp/run_pansou.sh` 后执行（**插件列表必须带上 `ENABLED_PLUGINS`，否则搜索永远返回 0**）：

```bash
#!/bin/bash
cd /workspace/netdisk2weixin/pansou
export PORT=8888
export CHANNELS="<从 pansou/docker-compose.yml 复制的 CHANNELS 整行>"
export ENABLED_PLUGINS="<从 pansou/docker-compose.yml 复制的 ENABLED_PLUGINS 整行>"
export CACHE_ENABLED=true CACHE_PATH=/workspace/netdisk2weixin/pansou/cache CACHE_MAX_SIZE=100 CACHE_TTL=60
exec ./dist/pansou-linux-amd64
```

启动后轮询等待（最多 60 秒）：

```bash
sleep 12 && curl -s -m 40 "http://localhost:8888/api/search?kw=test&cloud_types=quark" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print('quark:', len(((j.get('data') or {}).get('merged_by_type') or {}).get('quark',[])))"
```

输出 `quark: <非0数字>` 即就绪。**注意**：日志里出现 `未设置插件列表 (ENABLED_PLUGINS)，未加载任何插件` 是**该次启动失败**的标志，此时搜索恒为 0，必须检查环境变量是否真正传入。

---

## 2. 拉全量池并查看规模

**推荐方式**：用项目自带工具导出可读清单（顺带确认池子规模）：

```bash
python tools/list_resources.py --refresh --json /tmp/pool.json
```

输出形如：

```
# 空关键词全量结果：236 条（platform=quark）
############################################################
[1] #短剧🗄 2026年10月8日 短剧更新目录1
[2] web前端全栈高薪就业课-大神coderwhy出品【313集完结】
...
```

**若用户指定了分类**（如"学习教育""运营副业"），可在 `/tmp/pool.json` 基础上做分类统计（见第 3 节）；**若用户只要"随便挑"**，跳过分类，直接进第 3 节。

> **提示**：全量池通常 200~240 条，其中短剧/影视占比 85%+。随机抽取会**如实反映池子构成**（大概率抽到短剧/影视）。如果用户其实想要"干货"，应改用 `push-resource` skill。

---

## 3. 执行随机推送

**核心：保持 `--kw` 与 `--include` 都留空**，程序自动 `random.sample`：

```bash
# 随机推 6 个（平台 quark）
python3 main.py quark --count 6
```

### 参数说明

| 参数 | 默认 | 本 Skill 用法 |
|---|---|---|
| platform | — | `quark` / `baidu` / `all` |
| `--count` | 6 | **N 填这里**（要几个就写几个） |
| `--kw` | 空→`1` | ⚠️ **必须留空**，留空=用全网最新全量池 |
| `--include` | 无 | ⚠️ **必须留空**，传了就变成精确锁定、不再是随机 |
| `--exclude` | 无 | 可选：要剔除短剧可加 `--exclude 短剧 --exclude 集）` |
| `--force` | false | 需要最新池子时加（跳过 12h 缓存） |
| `--preview` | false | 只随机抽不转存，用于先看抽到了什么 |

### 变体用法

**A. 先预览随机结果，确认后再推**（推荐先跑一次）：

```bash
python3 main.py quark --count 6 --preview --force
# 看 output 里的 items 列表，满意再去掉 --preview 正式执行
```

**B. 随机但排除短剧**（"随便挑几个但别给我短剧"）：

```bash
python3 main.py quark --count 6 --exclude 短剧 --exclude "集）" --exclude "更新目录"
```

**C. 指定分类随机**（"从运营副业里随机挑 3 个"）：
先用第 2 节的 JSON 按分类筛出候选标题，再用 `--include` 逐个锁定 —— **此时已非随机，建议改用 `push-resource` skill**；若坚持要"分类内随机"，可在筛选后的候选中自行 `random.sample`，把抽中的标题片段传给 `--include`。

### 执行时长

- 每个资源约 10~60 秒（含转存、引流清理、上传 PDF、生成分享链接）
- 6 个约需 **1~15 分钟**；≥10 个或含大目录（如 313 集课程）可能更久
- **必须用后台任务跑**，避免超时中断

---

## 4. 结果判读

### 成功标志

```
转存成功 [N/M]: xxx
引流清理完成：检查 X 个，删除 Y 个
分享成功 [N]: xxx - https://pan.quark.cn/s/xxxx
飞书写入成功: N 条追加, M 条跳过
全部任务完成
```

### 失败处理

| 现象 | 含义 | 处理 |
|---|---|---|
| `PanSou: 000` | PanSou 没在跑 | 回第 1 节启动 |
| `所有链接均失效，推送失败` | 随机抽中的源已被封/失效 | **正常损耗**（随机抽取无法预筛），重新执行一次即会抽到新的一批 |
| `获取 stoken 失败（分享者用户封禁）` | 源分享被封（夸克 41031） | 同上，重跑换一批 |
| `转存结果为空（可能已存在或未返回任务）` | 目录已有同名 | 正常跳过 |
| `分享失败: 'data'` | 转存内容为空（常因**主文件被引流清理误删**） | 换一批；若频繁出现需修引流规则 |
| `未加载任何插件` | PanSou 启动缺 `ENABLED_PLUGINS` | 重启 PanSou 并确认环境变量 |

> **随机抽取的成功率**：全量池中约 **60~80%** 的源可正常转存。若要求"必须凑够 N 个成功"，可**重跑直到凑够**，或改用 `push-resource`（能提前用 stoken 预检筛选）。

---

## 5. 预检技巧（可选，提高成功率）

随机抽到的源可能已被封。若要**先筛掉失效源再转存**，可在第 2 步后用 stoken 接口批量预检：

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "/workspace/netdisk2weixin")
import asyncio, json, random
from core.config import load_config
from adapters.quark import QuarkPanFileManager

pool = json.load(open("/tmp/pool.json"))   # tools/list_resources.py --json 的产物
cfg = load_config()
adapter = QuarkPanFileManager(cookie=cfg["quark"]["cookie"])

async def check(it):
    try:
        pwd = QuarkPanFileManager.get_pwd_id(it["url"])
        return it, bool(await adapter.get_stoken(pwd, ""))
    except Exception:
        return it, False

async def main():
    picks = random.sample(pool, min(20, len(pool)))      # 先随机抽一批候选
    res = await asyncio.gather(*[check(it) for it in picks])
    ok = [it for it, valid in res if valid]
    print(json.dumps([{"note": i.get("note","")[:60], "url": i["url"]} for i in ok[:6]],
                     ensure_ascii=False, indent=1))
asyncio.run(main())
PY
```

拿到有效列表后，用 `--include` 锁定推送（此时是"预筛后的随机"）。

---

## 6. 输出给用户的总结

```markdown
✅ 随机推送完成：成功 X / 失败 Y

| # | 资源名称 | 新分享链接 |
|---|---------|-----------|
| 1 | xxx | https://pan.quark.cn/s/xxx |

- 候选池：空关键词全量 N 条（随机抽取，未做类型筛选）
- 引流清理：扫描 N 个文件，删除 M 个别人的引流文件
- 99999.pdf：已上传到 X 个目录
- 企业微信推送：✅ 已发送 / ❌ 失败
- 飞书表格：X 条追加 / Y 条跳过（重名）
```

---

## 7. 注意事项

- **`--kw` 与 `--include` 双留空是"随机"的前提**：任一不为空都会退化成"搜索+过滤"或"精确锁定"，不再是随机抽取
- **随机 ≠ 高质量**：全量池 85%+ 是短剧/影视，随机结果大概率是这类；要干货请改用 `push-resource`
- **去重是自动的**：已推送过的资源会按标题指纹过滤（`load_pushed_records`），不会重复转存
- **引流清理在前，上传在后**：先删别人的引流文件，再上传自己的 99999.pdf，确保自己的 PDF 不被误删
- **PanSou 是长驻服务**：启动后保持运行，后续推送直接复用；重启环境后需重新拉起
- **`--force` 慎用**：会跳过 12 小时缓存重新全量请求，较慢；池子够新时不必加
