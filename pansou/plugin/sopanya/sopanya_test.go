package sopanya

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClassifyShareURL(t *testing.T) {
	cases := map[string]string{
		"https://pan.quark.cn/s/example":            "quark",
		"https://www.alipan.com/s/example":          "aliyun",
		"https://pan.baidu.com/s/example?pwd=abcd":  "baidu",
		"https://drive.uc.cn/s/example":             "uc",
		"https://pan.xunlei.com/s/example?pwd=abcd": "xunlei",
		"https://cloud.189.cn/t/example":            "tianyi",
		"https://example.com/not-a-share":           "",
	}
	for raw, want := range cases {
		if got := classifyShareURL(raw); got != want {
			t.Errorf("classifyShareURL(%q) = %q, want %q", raw, got, want)
		}
	}
}

func TestSopanyaSearchResolvesToken(t *testing.T) {
	oldBase := sopanyaBaseURL
	t.Cleanup(func() { sopanyaBaseURL = oldBase })

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/other/web_search":
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintln(w, `data: {"type":"progress","stage":"connecting"}`)
			fmt.Fprintln(w, `data: {"title":"侵略机器 (2026)","url":"opaque-token","description":"4K 电影","is_type":2}`)
			fmt.Fprintln(w, "data: [DONE]")
		case "/api/other/save_url":
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"code":200,"message":"资源获取成功","data":{"title":"侵略机器 (2026)","url":"https://pan.baidu.com/s/example?pwd=xeqy","is_type":2,"code":"","description":"4K 电影","update_time":"2026-09-10 13:11:25"}}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	sopanyaBaseURL = server.URL

	results, err := NewSopanyaPlugin().searchImpl(server.Client(), "侵略机器", nil)
	if err != nil {
		t.Fatalf("searchImpl returned error: %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("got %d results, want 1", len(results))
	}
	if got := results[0].Links[0].Type; got != "baidu" {
		t.Fatalf("link type = %q, want baidu", got)
	}
	if got := results[0].Links[0].Password; got != "xeqy" {
		t.Fatalf("password = %q, want xeqy", got)
	}
	if !strings.HasPrefix(results[0].UniqueID, pluginName+"-") {
		t.Fatalf("unexpected unique id: %q", results[0].UniqueID)
	}
}

func TestExtractShareURLsAndPassword(t *testing.T) {
	text := `资源：https://pan.baidu.com/s/example?pwd=abcd，备用 https://pan.quark.cn/s/xyz。`
	urls := extractShareURLs(text)
	if len(urls) != 2 {
		t.Fatalf("got %d URLs, want 2", len(urls))
	}
	if got := extractPassword(text); got != "abcd" {
		t.Fatalf("password = %q, want abcd", got)
	}
}
