---
name: "push-random"
description: "从本地资源池（资源池.json）随机任挑 N 个，一键转存→引流清理→上传99999.pdf→永久分享→企业微信+飞书推送。Invoke when user asks to 随机挑/随机推/任挑 N 个/随便挑几个 资源，或说 from the pool pick N randomly."
---

# Push Random - 从资源池随机任挑 N 个推送

用户说"随机挑 N 个"、"任挑几个推"、"从池子里随便挑 N 个转存"时，**立即使用本 Skill**。

> **与 `push-resource` 的区别**：
> - `push-resource` = **AI 按类型挑选**（要"学习类"就逐条阅读挑出学习类），用于有明确类型要求的场景
> - `push-random` = **真·随机抽取**（不筛类型，由程序 `random.sample` 决定），用于"随便挑几个"、快速填充推送量、或做资源多样性测试
>
> 两者共用同一条转存→分享→推送链路，差别只在**选哪些**。

---

## 核心原理

`main.py` 在**未指定 `--include`** 时，会对本地过滤后的资源池执行 `random.sample(池, N)`（见 `adapters/quark.py` 的 `selected = random.sample(quark_data, count)`）。

资源池数据源优先级（`core/cache.py`）：
1. **本地资源池 JSON**（默认 `项目根/资源池.json`，`.env` 里 `LOCAL_POOL_ENABLED=true` 时启用）— **优先**，跳过 PanSou
2. **PanSou 聚合 API** — 本地池不可用时兜底
3. **磁盘缓存** — API 失败时的最后兜底

当前环境 `资源池.json` 已存在、`LOCAL_POOL_ENABLED=true`，**全程不依赖 PanSou**。

---

## 一条命令搞定

```powershell
# 随机推 N 个（默认平台 quark）
python main.py quark --count 6
```

就这么简单。程序自动从 `资源池.json` 读全量池 → 去重（已推送的跳过）→ `random.sample` 抽 N 个 → 转存 → 引流清理 → 上传 99999.pdf → 生成新分享链接 → 推企业微信+飞书。

### 参数说明

| 参数 | 默认 | 本 Skill 用法 |
|---|---|---|
| platform | — | `quark`（随机推送用 quark，百度无本地池） |
| `--count` | 6 | **N 填这里**（要几个就写几个） |
| `--kw` | 留空→忽略 | 本地池模式下**不起作用**，无需传 |
| `--include` | 无 | ⚠️ **必须留空**，传了就变成精确锁定、不再是随机 |
| `--exclude` | 无 | 可选：要剔除短剧可加 `--exclude 短剧 --exclude 集）` |
| `--preview` | false | 只随机抽不转存，用于先看抽到了什么 |
| `--force` | false | 本地池模式下无效（直接读 JSON） |

### 变体用法

**A. 先预览随机结果，确认后再推**（推荐先跑一次）：

```powershell
python main.py quark --count 6 --preview
# 看 output 里的 items 列表，满意再去掉 --preview 正式执行
```

**B. 随机但排除短剧**（"随便挑几个但别给我短剧"）：

```powershell
python main.py quark --count 6 --exclude 短剧 --exclude "集）" --exclude "更新目录"
```

---

## 执行时长

- 每个资源约 10~60 秒（含转存、引流清理、上传 PDF、生成分享链接）
- 6 个约需 **1~15 分钟**
- 成功率约 **60~80%**（部分源已被封/取消，属正常损耗）
- 若要凑够 N 个成功，重跑即可（已成功转存过的会被自动过滤，不会重复）

---

## 结果判读

### 成功标志

```
从本地资源池读取 XXXX 条
过滤已推送资源 NN 个，剩余 XXXX 个
转存成功 [N/M]: xxx
引流清理完成：检查 X 个，删除 Y 个
分享成功 [N]: xxx - https://pan.quark.cn/s/xxxx
飞书写入成功: N 条追加, M 条跳过
全部任务完成
```

### 失败处理

| 现象 | 含义 | 处理 |
|---|---|---|
| `获取 stoken 失败（分享者用户封禁）` | 源分享被封（夸克 41031） | 正常损耗，重跑会抽到新的一批 |
| `转存结果为空（可能已存在或未返回任务）` | 目录已有同名 | 正常跳过 |
| `分享失败: 'data'` | 转存内容为空（常因主文件被引流清理误删） | 换一批；若频繁出现需修引流规则 |
| `本地资源池不存在（xxx/资源池.json）` | 文件没找到 | 确认文件路径；可重新跑 `python tools/list_resources.py --refresh --json 资源池.json` 生成 |
| Cookie 过期 | 转存报"获取 stoken 失败" | 提示用户重新抓 `QUARK_COOKIE` |

> **随机抽取的成功率**：本地池中约 **60~80%** 的源可正常转存。若要求"必须凑够 N 个成功"，可**重跑直到凑够**（已推送的会被自动去重跳过）。

---

## 输出给用户的总结

```markdown
✅ 随机推送完成：成功 X / 失败 Y

| # | 资源名称 | 新分享链接 |
|---|---------|-----------|
| 1 | xxx | https://pan.quark.cn/s/xxx |
| 2 | yyy | 转存失败 |

- 候选池：本地资源池 X 条（随机抽取，未做类型筛选），已推送 NN 条自动过滤
- 引流清理：扫描 N 个文件，删除 M 个别人的引流文件
- 99999.pdf：已上传到 X 个目录
- 企业微信推送：✅ 已发送 / ❌ 失败
- 飞书表格：X 条追加 / Y 条跳过（重名）
```

---

## 注意事项

- **一条命令**：`python main.py quark --count N` 就够了，不需要检测 PanSou、不需要启动服务、不需要临时脚本
- **`--include` 必须留空**：传了就退化成精确锁定，不再是随机
- **随机 ≠ 高质量**：资源池 80%+ 是短剧/影视，随机结果大概率是这类；要干货请改用 `push-resource`
- **去重是自动的**：已推送过的资源会按标题指纹过滤，不会重复转存
- **引流清理在前，上传在后**：先删别人的引流文件，再上传自己的 99999.pdf，确保自己的 PDF 不被误删
- **资源池需要定期更新**：`python tools/list_resources.py --refresh --json 资源池.json` 重新从 PanSou 拉全量生成新清单；如果 PanSou 不在跑，可以手动更新 JSON 文件
- **Cookie 有效期**：夸克 Cookie 可能 1~2 周过期，过期后转存/分享报错，提示用户重新抓
