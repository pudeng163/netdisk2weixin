# 清影 (revohd.com) HTML结构分析

## 网站信息
- 网站名称: 清影
- 域名: www.revohd.com
- 类型: 影视资源搜索（仅123网盘）

## 1. 搜索页面

### URL格式
```
https://www.revohd.com/vodsearch/-------------.html?wd={keyword}
```

### HTML结构
- 容器: `div.module-search-item` (多个)
- 每个结果包含:
  - 封面: `.video-cover .module-item-cover .module-item-pic a`
    - href: `/voddetail/{id}.html`
  - 标题: `.video-info .video-info-header h3 a`
    - href: `/voddetail/{id}.html`
    - title: 影片标题
    - text: 影片标题
  - 分类标签: `.video-info-aux .video-tag-icon`
  - 年份/地区: `.video-info-aux .tag-link`
  - 导演: `.video-info-items .video-info-actor` (导演)
  - 主演: `.video-info-items .video-info-actor` (主演)
  - 剧情简介: `.video-info-items .video-info-item` (剧情)

### 提取信息
- 影片ID: 从详情页链接提取 `/voddetail/(\d+)\.html`
- 影片标题: 从标题链接获取

## 2. 详情页面

### URL格式
```
https://www.revohd.com/voddetail/{id}.html
```

### HTML结构

#### 基本信息
- 标题: `.video-info .video-info-header h1.page-title a`
- 更新时间: `.video-info-items` 中查找包含"更新："的元素
  - 格式: `更新：2025-12-09 07:22:37，最后更新于 4天前`
  - 提取: `2025-12-09 07:22:37`
- 剧情: `.video-info-items .video-info-item.video-info-content .vod_content span`

#### 123网盘下载链接区域
- 标题区: `div.module-heading h2.module-title` 包含 "123云盘链接"
- 链接容器: `div.module-list.module-player-list.module-downlist`
  - 链接项: `.module-row-one .module-row-info`
    - 链接文本: `a.module-row-text`
      - data-clipboard-text: 完整123网盘链接
      - 格式: `https://www.123684.com/s/H6Y7Vv-2oDFv?pwd=REVO`

### 123网盘链接格式
```
https://www.123684.com/s/{shareCode}?pwd={password}
```
- shareCode: 分享码（如 `H6Y7Vv-2oDFv`）
- password: 密码（4位大写字母，如 `REVO`）

## 3. 插件实现要点

### 搜索流程
1. 构造搜索URL: `baseURL + searchPath + "?wd=" + URLEncode(keyword)`
2. 发送GET请求，解析HTML
3. 提取所有 `.module-search-item` 元素
4. 对每个结果提取：
   - 详情页URL (`/voddetail/{id}.html`)
   - 影片标题
   - 影片ID

### 详情页处理
1. 并发请求详情页
2. 查找包含"123云盘链接"的下载区域
3. 提取123网盘链接：
   - 选择器: `.module-downlist .module-row-text`
   - 属性: `data-clipboard-text`
4. 解析链接提取密码（从 `?pwd=` 参数）

### 更新时间提取
1. 查找包含"更新："文本的元素
2. 使用正则提取时间: `更新[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})`
3. 解析为time.Time对象

### 特殊说明
- **仅支持123网盘**：此网站只提供123网盘链接
- **无需播放页**：网盘链接直接在详情页展示
- **密码格式**：固定4位（通常是大写字母）
- **链接唯一性**：每部影片通常只有一个123网盘链接

## 4. 网盘类型

固定为 `pan123`（123网盘）

## 5. 并发控制

### 建议配置
- 详情页并发数: 3-5个
- 请求超时: 30秒
- 使用信号量控制并发

### 错误处理
- 网络请求失败 → 重试3次
- HTML解析失败 → 跳过该项
- 未找到123网盘链接 → 跳过该影片
- 密码提取失败 → 记录但仍返回链接

## 6. 结果结构

### UniqueID格式
```
qingying-{影片ID}
```

### SearchResult
- **UniqueID**: `qingying-{id}`
- **Title**: 影片标题
- **Content**: 剧情简介
- **Links**: 123网盘链接数组（通常只有1个）
- **Channel**: 空字符串
- **Datetime**: 从"更新："字段提取的时间

### Link对象
- **Type**: `pan123`
- **URL**: 完整的123网盘链接
- **Password**: 从URL参数提取的4位密码

## 7. 优先级设置

建议设置为优先级3（标准网盘搜索插件）

## 8. 请求头设置

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8
Accept-Language: zh-CN,zh;q=0.9,en;q=0.8
Referer: https://www.revohd.com
```
