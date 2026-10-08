# 曦阳小站（yulinshufa）HTML 结构分析

## 搜索接口

- **入口 URL**：`http://www.yulinshufa.cn/plus/search.php?q=关键词`
- **请求方法**：GET（导航条中的 `<form method="get">`）
- **参数**：`q`（搜索关键字）
- **字符集**：页面声明 `charset=gbk`，抓取结果必须先转码为 UTF-8 再做解析。

## 搜索结果页

### 1. 列表容器

主内容位于 `div.main-list-con > ul.main-list`，每条结果对应一个 `<li>`。

```html
<div class="main-list-con">
  <ul class="main-list">
    <li>
      <!-- 单条结果 -->
    </li>
  </ul>
</div>
```

页面左栏、右栏是热点/推荐模块，可忽略。

### 2. 单条结果结构

| 元素 | 选择器 | 说明 |
| ---- | ------ | ---- |
| 封面图 | `li > div.list-pic > a > img` | `src` 为完整图片地址，`href` / `target` 指向详情页。 |
| 标题与详情链接 | `div.list-con > p.s-title > a` | 链接形如 `http://www.yulinshufa.cn/xz/45641/`。搜索关键词会被 `<font color='red'>` 包裹，需要去标签。 |
| 简介 | `div.list-con > p.s-desc` | 直接取文本即可；同样含有 `<font>` 高亮标签。 |
| 分类 | `div.list-con > div.s-ext > span.item:first-child > a` | 文本是分类名称（如“内地电视剧”），可作为标签。 |
| 日期 | `div.list-con > div.s-ext > span.item:last-child` | 形如 `2025-12-14`，只有日期无时间。 |

**ID 解析**：详情链接固定为 `/xz/{数字}/`，可直接提取数字作为 `UniqueID` 的一部分。

### 3. 其他模块

- `div.list-page`：搜索引擎兜底提示，暂无分页按钮。
- 右侧 `div.right-list`：最新 / 最热推荐，结构为 `<ul id="new-mov">` 和 `<ul id="pai-mov">`，若需要补充推荐可选择性解析。

## 详情页结构

### 1. URL 规律

```
PC:  http://www.yulinshufa.cn/xz/{id}/
WAP: http://wap.yulinshufa.cn/xz/{id}/
```

搜索结果全部落在 PC 端 URL，内容与 WAP 端一致。

### 2. 标题与基础信息

```html
<div class="content-tit">
  <h1>凡人修仙传：星海飞驰篇</h1>
</div>
<div class="content-info">
  <span>作者：夕阳小编</span>
  <span>日期：2025-05-20 22:07:00 </span>
</div>
```

- `h1` 为资源标题。
- `span` 中包含作者与完整时间，日期时间可直接解析。

### 3. 正文与下载信息

正文位于 `div.content`，顺序通常为简介段落、海报 `<img>`、下载信息。下载区采用 `<strong>下载地址</strong><br />` + 若干行文本：

```html
<strong>下载地址</strong><br />
凡人修仙传（动漫）<br />
链接：<a href="https://pan.quark.cn/s/31b8d504c3f5">https://pan.quark.cn/s/31b8d504c3f5</a><br />
提取码：JTvr
</br>
```

解析要点：

- 依次遍历 `div.content` 中的 `<a>`，筛出支持的网盘域名（夸克、百度、阿里等）。
- 提取码信息一般以“提取码：xxxx”紧跟在 `<a>` 后面的文本节点，也可能使用 `<br />`/`</br>` 换行。
- 如果正文中包含多段 `链接：` / `提取码：`，需将每个链接和最近的提取码配对。缺省的提取码可以置空。

### 4. 相关与导航区

- `div.content-next`：提供“链接地址”“移动端链接”“浏览前页/后页”，可用来构造抓取日志，但通常不需要。
- `div.content-related`：猜你喜欢列表 `<ul.soft-top-list>`，结构单一，仅含 `<a>`。

## 数据提取注意事项

1. **字符集**：整个站点返回 GBK，抓取后需 `iconv` 转 UTF-8，避免出现乱码。
2. **关键词高亮**：搜索结果页中的 `<font color='red'>` 和 `<strong>` 仅用于高亮，应在提取标题/简介时移除。
3. **日期格式**：搜索结果页只有日期（`YYYY-MM-DD`），详情页提供完整时间（`YYYY-MM-DD HH:MM:SS`）；若需要排序可优先使用详情页时间。
4. **链接类型**：常见网盘域名包括 `pan.quark.cn`、`pan.baidu.com`、`aliyundrive.com`、`pan.xunlei.com` 等，可直接复用项目内的 `determineCloudType()` 来识别。
5. **提取码匹配**：`提取码：` 文本紧贴 `<br />`，中英文冒号都有可能；解析时去掉空格即可获得 4 位密码。
6. **容错**：部分旧帖可能只有纯文本链接（无 `<a>`），需要在 `div.content` 中正则检索 `https?://` 形式的字符串作为补充。
