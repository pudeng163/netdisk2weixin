package btbtlb

import (
	"context"
	"fmt"
	"hash/fnv"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/PuerkitoBio/goquery"
	"pansou/model"
	"pansou/plugin"
)

const (
	pluginName             = "btbtlb"
	pluginPriority         = 3
	baseURL                = "https://www.btbtlb.com"
	requestTimeout         = 20 * time.Second
	maxResponseBytes       = 8 << 20
	maxSearchItems         = 10
	maxResourcesPerMovie   = 12
	maxCloudResources      = 4
	maxResourceItems       = 40
	maxMovieConcurrency    = 4
	maxResourceConcurrency = 8
)

var (
	magnetRE    = regexp.MustCompile(`(?i)magnet:\?xt=urn:btih:[^\s"'<>]+`)
	urlRE       = regexp.MustCompile(`https?://[^\s"'<>]+`)
	passwordREs = []*regexp.Regexp{
		regexp.MustCompile(`(?i)[?&](?:pwd|password|passcode)=([0-9a-zA-Z]+)`),
		regexp.MustCompile(`(?i)(?:提取码|访问码|密码|取件码)\s*[:：]\s*([0-9a-zA-Z]+)`),
	}
	detailIDRE = regexp.MustCompile(`/detail/(\d+)\.html`)
	hashRE     = regexp.MustCompile(`(?i)^[0-9a-f]{40}$`)
	dateRE     = regexp.MustCompile(`\d{4}-\d{2}-\d{2}`)
)

// BtbtlbPlugin searches BT影视 movie pages and resolves their torrent/cloud links.
type BtbtlbPlugin struct {
	*plugin.BaseAsyncPlugin
}

type searchItem struct {
	id          string
	title       string
	detailURL   string
	description string
	image       string
	tags        []string
}

type resourceItem struct {
	id    string
	title string
	url   string
}

type movieDetail struct {
	title       string
	description string
	image       string
	tags        []string
	resources   []resourceItem
	datetime    time.Time
}

func init() {
	plugin.RegisterGlobalPlugin(NewBtbtlbPlugin())
}

func NewBtbtlbPlugin() *BtbtlbPlugin {
	return &BtbtlbPlugin{BaseAsyncPlugin: plugin.NewBaseAsyncPluginWithFilter(pluginName, pluginPriority, true)}
}

func (p *BtbtlbPlugin) Name() string        { return pluginName }
func (p *BtbtlbPlugin) DisplayName() string { return "BT影视" }
func (p *BtbtlbPlugin) Description() string {
	return "BT影视 - 电影、电视剧磁力和网盘资源"
}

func (p *BtbtlbPlugin) Search(keyword string, ext map[string]interface{}) ([]model.SearchResult, error) {
	result, err := p.SearchWithResult(keyword, ext)
	if err != nil {
		return nil, err
	}
	return result.Results, nil
}

func (p *BtbtlbPlugin) SearchWithResult(keyword string, ext map[string]interface{}) (model.PluginSearchResult, error) {
	return p.AsyncSearchWithResult(keyword, p.searchImpl, p.MainCacheKey, ext)
}

func (p *BtbtlbPlugin) searchImpl(client *http.Client, keyword string, _ map[string]interface{}) ([]model.SearchResult, error) {
	keyword = strings.TrimSpace(keyword)
	if keyword == "" {
		return []model.SearchResult{}, nil
	}
	if client == nil {
		client = &http.Client{Timeout: requestTimeout}
	}

	doc, err := p.fetchDocument(client, baseURL+"/search/"+url.PathEscape(keyword), baseURL+"/")
	if err != nil {
		return nil, fmt.Errorf("[%s] 搜索请求失败: %w", p.Name(), err)
	}
	items := parseSearchItems(doc)
	if len(items) > maxSearchItems {
		items = items[:maxSearchItems]
	}
	if len(items) == 0 {
		return []model.SearchResult{}, nil
	}

	// Movie pages are independent, so fetch them concurrently while keeping a bounded fan-out.
	movies := make([]movieDetail, len(items))
	validMovies := make([]bool, len(items))
	sem := make(chan struct{}, maxMovieConcurrency)
	var wg sync.WaitGroup
	for i, item := range items {
		wg.Add(1)
		go func(index int, item searchItem) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			detail, fetchErr := p.fetchMovie(client, item)
			if fetchErr == nil && len(detail.resources) > 0 {
				movies[index] = detail
				validMovies[index] = true
			}
		}(i, item)
	}
	wg.Wait()

	type resourceCandidate struct {
		movie movieDetail
		item  resourceItem
	}
	resources := make([]resourceCandidate, 0, maxResourceItems)
	seenResource := make(map[string]struct{})
	for i, movie := range movies {
		if !validMovies[i] {
			continue
		}
		for _, resource := range movie.resources {
			if _, exists := seenResource[resource.url]; exists {
				continue
			}
			seenResource[resource.url] = struct{}{}
			resources = append(resources, resourceCandidate{movie: movie, item: resource})
			if len(resources) >= maxResourceItems {
				break
			}
		}
		if len(resources) >= maxResourceItems {
			break
		}
	}
	if len(resources) == 0 {
		return []model.SearchResult{}, nil
	}

	resolved := make([]model.SearchResult, len(resources))
	valid := make([]bool, len(resources))
	sem = make(chan struct{}, maxResourceConcurrency)
	for i, candidate := range resources {
		wg.Add(1)
		go func(index int, candidate resourceCandidate) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			result, ok := p.fetchResource(client, candidate.movie, candidate.item)
			if ok {
				resolved[index] = result
				valid[index] = true
			}
		}(i, candidate)
	}
	wg.Wait()

	results := make([]model.SearchResult, 0, len(resolved))
	for i, result := range resolved {
		if valid[i] && len(result.Links) > 0 {
			results = append(results, result)
		}
	}
	// Torrent names commonly use dots, so this plugin filters internally and skips Service filtering.
	return plugin.FilterResultsByKeyword(results, keyword), nil
}

func (p *BtbtlbPlugin) fetchMovie(client *http.Client, item searchItem) (movieDetail, error) {
	doc, err := p.fetchDocument(client, item.detailURL, baseURL+"/")
	if err != nil {
		return movieDetail{}, err
	}
	detail := movieDetail{
		title:       cleanText(doc.Find(".video-info-header > h1.page-title").First().Text()),
		description: cleanText(doc.Find(".vod_content").First().Text()),
		image:       imageURL(doc.Find(".video-cover img").First()),
		tags:        append([]string{}, item.tags...),
		datetime:    time.Now(),
	}
	if detail.title == "" {
		detail.title = item.title
	}
	if detail.description == "" {
		detail.description = item.description
	}
	if detail.image == "" {
		detail.image = item.image
	}
	if len(detail.tags) == 0 {
		doc.Find(".video-info-aux a").Each(func(_ int, s *goquery.Selection) {
			if value := cleanText(s.Text()); value != "" && value != "Movie" {
				detail.tags = append(detail.tags, value)
			}
		})
	}

	seen := make(map[string]struct{})
	torrentResources := make([]resourceItem, 0, maxResourcesPerMovie)
	cloudResources := make([]resourceItem, 0, maxCloudResources)
	doc.Find(".module-row-info a.module-row-text[href]").Each(func(_ int, s *goquery.Selection) {
		href := absoluteURL(s.AttrOr("href", ""))
		if href == "" || (!strings.Contains(href, "/tdown/") && !strings.Contains(href, "/pdown/")) {
			return
		}
		if _, exists := seen[href]; exists {
			return
		}
		isCloud := strings.Contains(href, "/pdown/")
		if isCloud && len(cloudResources) >= maxCloudResources {
			return
		}
		if !isCloud && len(torrentResources) >= maxResourcesPerMovie {
			return
		}
		seen[href] = struct{}{}
		title := cleanResourceTitle(s.Find(".module-row-title h4").First().Text())
		if title == "" {
			title = cleanResourceTitle(s.AttrOr("title", ""))
		}
		if title == "" {
			title = detail.title
		}
		resource := resourceItem{id: resourceID(href), title: title, url: href}
		if isCloud {
			cloudResources = append(cloudResources, resource)
		} else {
			torrentResources = append(torrentResources, resource)
		}
	})
	detail.resources = append(torrentResources, cloudResources...)
	return detail, nil
}

func (p *BtbtlbPlugin) fetchResource(client *http.Client, movie movieDetail, item resourceItem) (model.SearchResult, bool) {
	doc, err := p.fetchDocument(client, item.url, baseURL+"/")
	if err != nil {
		return model.SearchResult{}, false
	}
	resourceTitle := cleanResourceTitle(doc.Find(".tinfo .page-title").First().Text())
	if resourceTitle == "" {
		resourceTitle = item.title
	}
	if resourceTitle == "" {
		resourceTitle = movie.title
	}
	links := extractLinks(doc)
	if len(links) == 0 {
		return model.SearchResult{}, false
	}
	datetime := parseDateValue(labeledValue(doc, "更新时间"))
	if datetime.IsZero() {
		datetime = parseDateValue(labeledValue(doc, "种子时间"))
	}
	if datetime.IsZero() {
		datetime = movie.datetime
	}
	content := movie.description
	if content == "" {
		content = "来源：BT影视"
	}
	content += "\n资源：" + resourceTitle
	for i := range links {
		links[i].WorkTitle = movie.title
	}

	id := item.id
	if id == "" {
		id = shortHash(item.url)
	}
	result := model.SearchResult{
		UniqueID:  fmt.Sprintf("%s-%s", p.Name(), id),
		MessageID: fmt.Sprintf("%s-%s", p.Name(), id),
		Channel:   "",
		Datetime:  datetime,
		Title:     resourceTitle,
		Content:   content,
		Tags:      append([]string{"BT影视"}, movie.tags...),
		Links:     links,
	}
	if movie.image != "" {
		result.Images = []string{movie.image}
	}
	return result, true
}

func (p *BtbtlbPlugin) fetchDocument(client *http.Client, target, referer string) (*goquery.Document, error) {
	ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	setHeaders(req, referer)
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return goquery.NewDocumentFromReader(io.LimitReader(resp.Body, maxResponseBytes))
}

func parseSearchItems(doc *goquery.Document) []searchItem {
	items := make([]searchItem, 0, maxSearchItems)
	seen := make(map[string]struct{})
	doc.Find(".module-items .module-item").Each(func(_ int, s *goquery.Selection) {
		anchor := s.Find(".module-item-title[href]").First()
		if anchor.Length() == 0 {
			anchor = s.Find("a[href*='/detail/']").First()
		}
		href := absoluteURL(anchor.AttrOr("href", ""))
		if href == "" || !strings.Contains(href, "/detail/") {
			return
		}
		if _, exists := seen[href]; exists {
			return
		}
		title := cleanText(anchor.AttrOr("title", ""))
		if title == "" {
			title = cleanText(anchor.Text())
		}
		if title == "" {
			return
		}
		seen[href] = struct{}{}
		item := searchItem{
			id:          detailID(href),
			title:       title,
			detailURL:   href,
			description: cleanText(s.Find(".video-text").First().Text()),
			image:       imageURL(s.Find("img").First()),
		}
		s.Find(".module-item-caption span").Each(func(_ int, tag *goquery.Selection) {
			if value := cleanText(tag.Text()); value != "" {
				item.tags = append(item.tags, value)
			}
		})
		items = append(items, item)
	})
	return items
}

func extractLinks(doc *goquery.Document) []model.Link {
	links := make([]model.Link, 0, 4)
	seen := make(map[string]struct{})
	documentText := doc.Text()
	add := func(raw string) {
		raw = strings.TrimSpace(strings.Trim(raw, "'\"<>),.;"))
		if raw == "" {
			return
		}
		if strings.HasPrefix(strings.ToLower(raw), "magnet:?") {
			if _, exists := seen[raw]; exists {
				return
			}
			seen[raw] = struct{}{}
			links = append(links, model.Link{Type: "magnet", URL: raw})
			return
		}
		u, err := url.Parse(raw)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") {
			return
		}
		linkType := cloudType(u)
		if linkType == "" {
			return
		}
		password := extractPassword(raw)
		if password == "" {
			password = extractPassword(documentText)
		}
		key := raw + "\x00" + password
		if _, exists := seen[key]; exists {
			return
		}
		seen[key] = struct{}{}
		links = append(links, model.Link{Type: linkType, URL: raw, Password: password})
	}
	doc.Find("a[href]").Each(func(_ int, s *goquery.Selection) { add(s.AttrOr("href", "")) })
	for _, raw := range magnetRE.FindAllString(documentText, -1) {
		add(raw)
	}
	for _, raw := range urlRE.FindAllString(documentText, -1) {
		add(raw)
	}
	if len(links) == 0 {
		if hash := strings.TrimSpace(labeledValue(doc, "Hash")); hashRE.MatchString(hash) {
			add("magnet:?xt=urn:btih:" + strings.ToLower(hash))
		}
	}
	return links
}

func cloudType(u *url.URL) string {
	host := strings.ToLower(u.Hostname())
	switch {
	case host == "pan.quark.cn":
		return "quark"
	case host == "pan.baidu.com" || host == "yun.baidu.com":
		return "baidu"
	case host == "alipan.com" || host == "www.alipan.com":
		return "aliyun"
	case host == "drive.uc.cn":
		return "uc"
	case host == "cloud.189.cn":
		return "tianyi"
	case host == "caiyun.139.com":
		return "mobile"
	case host == "115.com" || host == "115cdn.com":
		return "115"
	case host == "123pan.com" || host == "www.123pan.com":
		return "123"
	case host == "pan.xunlei.com":
		return "xunlei"
	case host == "mypikpak.com" || host == "www.mypikpak.com":
		return "pikpak"
	default:
		return ""
	}
}

func extractPassword(raw string) string {
	for _, re := range passwordREs {
		if match := re.FindStringSubmatch(raw); len(match) > 1 {
			return strings.TrimSpace(match[1])
		}
	}
	return ""
}

func labeledValue(doc *goquery.Document, label string) string {
	value := ""
	doc.Find(".video-info-items").EachWithBreak(func(_ int, s *goquery.Selection) bool {
		if !strings.Contains(cleanText(s.Find(".video-info-itemtitle").First().Text()), label) {
			return true
		}
		value = cleanText(s.Find(".video-info-item").First().Text())
		return false
	})
	return value
}

func parseDateValue(value string) time.Time {
	value = cleanText(value)
	if value == "" {
		return time.Time{}
	}
	layouts := []string{
		time.RFC3339,
		"2006-01-02 15:04:05 -0700 MST",
		"2006-01-02 15:04:05 -0700",
		"2006-01-02 15:04:05",
		"2006-01-02",
	}
	for _, layout := range layouts {
		if parsed, err := time.Parse(layout, value); err == nil {
			return parsed
		}
	}
	if match := dateRE.FindString(value); match != "" {
		if parsed, err := time.ParseInLocation("2006-01-02", match, time.Local); err == nil {
			return parsed
		}
	}
	return time.Time{}
}

func imageURL(s *goquery.Selection) string {
	if s == nil || s.Length() == 0 {
		return ""
	}
	value := strings.TrimSpace(s.AttrOr("data-src", ""))
	if value == "" {
		value = strings.TrimSpace(s.AttrOr("src", ""))
	}
	return absoluteURL(value)
}

func absoluteURL(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" || strings.HasPrefix(raw, "http://") || strings.HasPrefix(raw, "https://") || strings.HasPrefix(strings.ToLower(raw), "magnet:") {
		return raw
	}
	if strings.HasPrefix(raw, "//") {
		return "https:" + raw
	}
	return baseURL + "/" + strings.TrimPrefix(raw, "/")
}

func detailID(raw string) string {
	if match := detailIDRE.FindStringSubmatch(raw); len(match) > 1 {
		return match[1]
	}
	return resourceID(raw)
}

func resourceID(raw string) string {
	value := strings.TrimSuffix(strings.TrimSpace(raw), "/")
	if idx := strings.LastIndex(value, "/"); idx >= 0 {
		value = value[idx+1:]
	}
	return strings.TrimSuffix(value, ".html")
}

func cleanResourceTitle(value string) string {
	value = cleanText(value)
	value = strings.TrimSuffix(value, ".torrent")
	value = strings.TrimSuffix(value, ".torren")
	return strings.TrimSpace(value)
}

func cleanText(value string) string {
	value = strings.ReplaceAll(value, "\u00a0", " ")
	return strings.Join(strings.Fields(value), " ")
}

func shortHash(value string) string {
	h := fnv.New32a()
	_, _ = h.Write([]byte(value))
	return fmt.Sprintf("%x", h.Sum32())
}

func setHeaders(req *http.Request, referer string) {
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8")
	req.Header.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
	if referer != "" {
		req.Header.Set("Referer", referer)
	}
}
