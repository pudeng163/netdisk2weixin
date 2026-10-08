# AI 客户端使用 PanSou 插件开发 Skill 指南

本文说明如何在主流 AI 编程客户端中复用 PanSou 插件开发 Skill，让 AI 在开发、调试、审查 `plugin/*` 插件时遵循本仓库的接口、过滤、缓存、路由和校验规范。

## 相关文件

- `docs/pansou-plugin-developer-SKILL.md`：PanSou 插件开发 Skill 原文，适合直接安装到支持 Skill 的客户端。
- `docs/插件开发指南.md`：完整插件系统说明，包含接口、优先级、过滤机制和开发流程。
- `plugin/plugin.go`、`model/response.go`、`model/plugin_result.go`：AI 开始改插件前应优先读取的核心代码。

## 通用使用方式

如果客户端不原生支持 Skill，可以把 `docs/pansou-plugin-developer-SKILL.md` 当作项目规则或上下文文档使用。建议在每次开发插件时明确要求 AI 读取这些文件：

```text
请按照 docs/pansou-plugin-developer-SKILL.md 和 docs/插件开发指南.md 开发/修改 PanSou 插件。
开始前先阅读 plugin/plugin.go、model/response.go、model/plugin_result.go，以及目标插件目录。
完成后运行 go test ./plugin/<插件名> 或 go build ./...，并说明验证结果。
```

## OpenAI Codex

Codex 支持个人 Skill 时，推荐把 Skill 安装为本地个人 skill：

```bash
CODEX_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/pansou-plugin-developer"
mkdir -p "$CODEX_SKILL_DIR"
cp docs/pansou-plugin-developer-SKILL.md "$CODEX_SKILL_DIR/SKILL.md"
```

之后在 PanSou 仓库中发起任务时，可以直接要求：

```text
使用 pansou-plugin-developer skill，帮我新增一个 xxx 搜索插件。
```

如果使用的 Codex 入口不读取个人 Skill，则在提示词中显式引用仓库内文档：

```text
请先阅读 docs/pansou-plugin-developer-SKILL.md，再修改 plugin/xxx。
```

## Claude Code / Claude

如果当前 Claude 客户端支持 Skills，可以按客户端要求新建 `pansou-plugin-developer` skill，并把 `docs/pansou-plugin-developer-SKILL.md` 作为 `SKILL.md`。

如果使用 Claude Code 的项目记忆方式，建议在仓库根目录的 `CLAUDE.md` 中加入：

```markdown
处理 PanSou 插件开发、调试或审查任务时，先阅读：

- docs/pansou-plugin-developer-SKILL.md
- docs/插件开发指南.md
- plugin/plugin.go
- model/response.go
- model/plugin_result.go

新增插件优先使用 BaseAsyncPlugin，返回结果必须有稳定 UniqueID、空 Channel、非空 Links，并按插件类型选择 Service 层过滤策略。
```

## Cursor

Cursor 推荐使用项目规则文件。可以创建 `.cursor/rules/pansou-plugin-developer.mdc`，内容示例：

```markdown
---
description: PanSou plugin development rules
globs:
  - plugin/**/*.go
  - model/**/*.go
  - service/**/*.go
alwaysApply: false
---

When developing, debugging, or reviewing PanSou plugins, first read:

- docs/pansou-plugin-developer-SKILL.md
- docs/插件开发指南.md
- plugin/plugin.go
- model/response.go
- model/plugin_result.go

Follow the Skill rules for BaseAsyncPlugin usage, UniqueID format, Link.Type validation, WorkTitle, SkipServiceFilter, bounded concurrency, web route namespacing, and focused Go validation.
```

使用时可以在 Cursor Chat 中说明：

```text
按 PanSou plugin development rules 给我实现 plugin/xxx。
```

## Windsurf

Windsurf 可以通过 Workspace Rules 使用这份 Skill。推荐在客户端的规则界面添加一条工作区规则，或创建 `.windsurf/rules/pansou-plugin-developer.md`：

```markdown
# PanSou Plugin Development

For plugin development, debugging, and review, read and follow:

- docs/pansou-plugin-developer-SKILL.md
- docs/插件开发指南.md

Before editing, inspect plugin/plugin.go, model/response.go, model/plugin_result.go, and the target plugin directory. Prefer existing plugin patterns and run focused Go validation before finishing.
```

如果客户端版本仍使用单文件规则，可以把同样内容放入 `.windsurfrules`。

## GitHub Copilot Chat

Copilot Chat 可通过仓库自定义指令复用这份规则。推荐在 `.github/copilot-instructions.md` 中加入：

```markdown
When working on PanSou plugins under plugin/*, follow docs/pansou-plugin-developer-SKILL.md and docs/插件开发指南.md.

Before editing plugin code, inspect plugin/plugin.go, model/response.go, model/plugin_result.go, and the target plugin directory.

New plugins should use BaseAsyncPlugin, return stable UniqueID values prefixed by plugin name, leave Channel empty, return non-empty Links, validate Link.Type, set WorkTitle when needed, choose the correct SkipServiceFilter strategy, and run focused Go validation.
```

在 Copilot Chat 中继续明确任务范围：

```text
请按仓库自定义指令审查 plugin/xxx 是否符合 PanSou 插件规范。
```

## Cline / Roo Code

这类 VS Code agent 客户端通常通过项目规则文件控制行为。

- Cline：可放入 `.clinerules`。
- Roo Code：可放入 `.roo/rules/pansou-plugin-developer.md`。

规则内容可以直接使用：

```markdown
PanSou plugin tasks must follow docs/pansou-plugin-developer-SKILL.md.

Read docs/插件开发指南.md and the shared plugin/model files before editing. Prefer existing plugin patterns, use BaseAsyncPlugin for new plugins, validate result shape, keep detail-page concurrency bounded, and run focused Go tests or build commands before finishing.
```

## 推荐任务模板

开发新插件：

```text
请按照 docs/pansou-plugin-developer-SKILL.md 新增 plugin/xxx。
数据源是 <URL/API说明>，结果需要提取 <网盘类型> 链接。
完成后运行 go test ./plugin/xxx；如果没有测试，运行 go build ./...。
```

修复已有插件：

```text
请按照 docs/pansou-plugin-developer-SKILL.md 修复 plugin/xxx 的 <问题描述>。
不要改无关插件。完成后说明根因、修改点和验证命令。
```

代码审查：

```text
请按 docs/pansou-plugin-developer-SKILL.md 审查 plugin/xxx。
优先检查 UniqueID、Channel、Links、Link.Type、WorkTitle、SkipServiceFilter、并发控制、错误处理和测试覆盖。
```

## 维护建议

- 当插件接口、过滤策略、支持的网盘类型或 Web 路由约定变化时，同步更新 `docs/pansou-plugin-developer-SKILL.md`。
- 如果 AI 客户端已经安装了个人 Skill，也建议保留仓库内这份文档，便于团队成员和不支持 Skill 的客户端复用。
- 客户端私有规则文件是否提交到仓库，应按团队协作习惯决定；公共规则优先沉淀到 `docs/`。
