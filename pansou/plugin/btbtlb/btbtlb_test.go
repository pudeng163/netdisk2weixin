package btbtlb

import (
	"strings"
	"testing"
	"time"

	"github.com/PuerkitoBio/goquery"
)

func TestParseSearchItems(t *testing.T) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(`
		<div class="module-items"><div class="module-item">
			<div class="module-item-pic"><img data-src="/poster.jpg"></div>
			<div class="module-item-caption"><span>2024</span><span class="video-class">剧情,科幻</span></div>
			<div class="module-item-content"><div class="video-text">简介内容</div></div>
			<div class="module-item-titlebox"><a class="module-item-title" href="/detail/123.html" title="测试影片">测试影片</a></div>
		</div></div>`))
	if err != nil {
		t.Fatal(err)
	}
	items := parseSearchItems(doc)
	if len(items) != 1 {
		t.Fatalf("got %d items, want 1", len(items))
	}
	if items[0].id != "123" || items[0].title != "测试影片" || items[0].detailURL != baseURL+"/detail/123.html" {
		t.Fatalf("unexpected item: %+v", items[0])
	}
	if items[0].image != baseURL+"/poster.jpg" || items[0].description != "简介内容" {
		t.Fatalf("unexpected metadata: %+v", items[0])
	}
}

func TestExtractLinksAndPassword(t *testing.T) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(`
		<div>夸克提取码：QWER</div>
		<a href="https://pan.quark.cn/s/abc">夸克</a>
		<a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">磁力</a>`))
	if err != nil {
		t.Fatal(err)
	}
	links := extractLinks(doc)
	if len(links) != 2 {
		t.Fatalf("got %d links, want 2: %+v", len(links), links)
	}
	if links[0].Type != "quark" || links[0].Password != "QWER" {
		t.Fatalf("unexpected cloud link: %+v", links[0])
	}
	if links[1].Type != "magnet" || !strings.HasPrefix(links[1].URL, "magnet:") {
		t.Fatalf("unexpected magnet link: %+v", links[1])
	}
}

func TestExtractHashFallback(t *testing.T) {
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(`
		<div class="video-info-items"><span class="video-info-itemtitle">Hash:</span><div class="video-info-item">0123456789abcdef0123456789abcdef01234567</div></div>`))
	if err != nil {
		t.Fatal(err)
	}
	links := extractLinks(doc)
	if len(links) != 1 || links[0].Type != "magnet" || !strings.Contains(links[0].URL, "0123456789abcdef0123456789abcdef01234567") {
		t.Fatalf("unexpected hash fallback: %+v", links)
	}
}

func TestParseDateValue(t *testing.T) {
	got := parseDateValue("2026-08-31 21:36:31 +0800 CST")
	if got.IsZero() || got.Year() != 2026 || got.Month() != time.August || got.Day() != 31 {
		t.Fatalf("unexpected date: %v", got)
	}
	got = parseDateValue("2026-07-06T16:02:15+08:00")
	if got.IsZero() || got.Hour() != 16 {
		t.Fatalf("unexpected RFC3339 date: %v", got)
	}
}
