---
name: pansou-plugin-developer
description: Build, extend, review, debug, or package PanSou Go search plugins under plugin/*, including AsyncSearchPlugin implementations, BaseAsyncPlugin usage, Service-layer filter strategy, Web management routes, logged-in source plugins, magnet/torrent plugins, net-disk link extraction, work_title handling, and PanSou plugin tests or documentation.
---

# PanSou Plugin Developer

## Core Workflow

1. Read the target plugin files and the shared framework before editing:
   - `plugin/plugin.go`
   - `model/response.go`
   - `model/plugin_result.go`
   - `docs/插件开发指南.md` when available
2. Read `references/plugin-patterns.md` for implementation rules and validation checks.
3. Read `references/plugin-inventory.md` when selecting an existing plugin to copy, compare, or review. It lists every plugin directory observed when this skill was created.
4. Choose the closest local pattern before inventing a new one:
   - HTML list plus detail pages: use a goquery plugin with bounded detail-page concurrency.
   - JSON API: use `pansou/util/json`, typed response structs, and strict link validation.
   - Magnet/torrent or broad foreign search: use `NewBaseAsyncPluginWithFilter(..., true)` and filter inside the plugin.
   - Login/session source: implement `InitializablePlugin` and `PluginWithWebHandler`.
5. Implement the narrowest change, then run focused validation:
   - `go test ./plugin/<name>` if tests exist.
   - `go test ./...` for shared framework or broad contract changes.
   - `go build ./...` when adding imports, packages, or route integrations.

## Required Plugin Shape

Use `BaseAsyncPlugin` unless maintaining a legacy edge case. New plugins should expose both methods:

```go
func (p *MyPlugin) Search(keyword string, ext map[string]interface{}) ([]model.SearchResult, error) {
	result, err := p.SearchWithResult(keyword, ext)
	if err != nil {
		return nil, err
	}
	return result.Results, nil
}

func (p *MyPlugin) SearchWithResult(keyword string, ext map[string]interface{}) (model.PluginSearchResult, error) {
	return p.AsyncSearchWithResult(keyword, p.searchImpl, p.MainCacheKey, ext)
}
```

Register in `init()`:

```go
func init() {
	plugin.RegisterGlobalPlugin(NewMyPlugin())
}
```

Return only valid results:

- `UniqueID` must be stable and start with the plugin name, usually `fmt.Sprintf("%s-%s", p.Name(), id)`.
- `Channel` must be `""` for plugin results.
- `Links` must be non-empty.
- Use supported `Link.Type` values such as `quark`, `uc`, `baidu`, `aliyun`, `guangya`, `xunlei`, `tianyi`, `115`, `123`, `mobile`, `pikpak`, `magnet`, `ed2k`, or `others`.
- Set `Link.WorkTitle` when one result contains links for multiple works, magnet filenames need a user-facing title, or an episode/line label disambiguates a link.

## Filter Strategy

Use standard Service-layer filtering for ordinary Chinese net-disk plugins:

```go
plugin.NewBaseAsyncPlugin("myplugin", 3)
```

Skip Service-layer filtering only for sources whose useful results would be removed by the global title filter:

```go
plugin.NewBaseAsyncPluginWithFilter("mymagnet", 3, true)
```

When skipping Service filtering, the plugin must still call `plugin.FilterResultsByKeyword` internally using the same effective search keyword. If `ext["title_en"]` is used for search, filter by that effective keyword after normalizing titles.

## HTTP And Parsing Rules

Use request contexts, realistic headers, bounded response sizes where useful, and retry with cloned requests. Avoid plain `client.Get(url)` in new code. Close every response body.

Prefer:

- `goquery` for HTML pages.
- `pansou/util/json` for JSON APIs.
- Precompiled regexes for repeated link/password extraction.
- `sync.WaitGroup` plus a semaphore channel for detail-page fanout.
- Stable cache keys for detail pages or expensive computed values.

## Web Route Plugins

For account/session plugins, implement:

- `InitializablePlugin.Initialize()` for loading persisted state, creating cache directories, and starting keepalive jobs.
- `PluginWithWebHandler.RegisterWebRoutes(router *gin.RouterGroup)` with a plugin-name route prefix.
- A single JSON action endpoint pattern, as used by `qqpd`, `weibo`, `gying`, and `panlian`.

Do not mix per-user state into package globals without a mutex or `sync.Map`. Persist cookies/config under the repo cache pattern already used by the source plugin.

## Review Checklist

Before finishing plugin work, check:

- The plugin is registered exactly once.
- Priority matches source quality: 1 high, 2 good, 3 normal, 4+ low or risky.
- `SkipServiceFilter` matches source type.
- Every returned result has at least one valid link and empty `Channel`.
- Password extraction handles URL `pwd` parameters and nearby text.
- Detail-page concurrency is bounded.
- Error messages include the plugin name.
- Any added route is namespaced by plugin name.
- Tests or build commands were run, or the reason they could not run is reported.
