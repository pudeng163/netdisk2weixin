package util

import (
	"strings"
	"testing"
)

func TestParseSearchResultsExtractsInlineKeyboardLinks(t *testing.T) {
	const html = `<!DOCTYPE html>
<html><body>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="testchannel/100">
    <div class="tgme_widget_message_text">藏锋（2026）<br>网盘资源 <a href="https://pan.baidu.com/s/1dfyBU2ABaVepdzHhyXweRg?pwd=xeqy">正文百度链接</a></div>
    <div class="tgme_widget_message_footer">
      <a class="tgme_widget_message_date" href="https://t.me/testchannel/100">
        <time datetime="2026-09-10T00:00:00+00:00">00:00</time>
      </a>
    </div>
  </div>
  <div class="tgme_widget_message_inline_keyboard">
    <div class="tgme_widget_message_inline_row">
      <a class="tgme_widget_message_inline_button url_button" href="https://pan.baidu.com/s/1dfyBU2ABaVepdzHhyXweRg?pwd=xeqy"><span>百度</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://pan.baidu.com/s/passwordless123"><span>百度无密码</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://pan.quark.cn/s/92f07db5654b"><span>夸克 提取码：qwer</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://pan.xunlei.com/s/VP-EzzPFb8z7tideTMh-U-WTA1?pwd=vuiy#"><span>迅雷</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://cloud.189.cn/t/abc123"><span>天翼</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://drive.uc.cn/s/abc123"><span>UC</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://123pan.com/s/abc123"><span>123</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://115.com/s/abc123"><span>115</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://www.alipan.com/s/abc123"><span>阿里</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://guangyapan.com/s/abc123"><span>光鸭</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://caiyun.139.com/w/i/abc123"><span>移动</span></a>
      <a class="tgme_widget_message_inline_button callback_button" data-callback="ignored"><span>回调</span></a>
      <a class="tgme_widget_message_inline_button url_button" href="https://example.com/not-a-pan"><span>官网</span></a>
    </div>
  </div>
</div>
</body></html>`

	results, _, err := ParseSearchResults(html, "testchannel")
	if err != nil {
		t.Fatalf("ParseSearchResults returned error: %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("expected one result, got %d", len(results))
	}

	result := results[0]
	if len(result.Links) != 11 {
		t.Fatalf("expected 11 supported button links, got %d: %#v", len(result.Links), result.Links)
	}

	byURL := make(map[string]struct {
		linkType string
		password string
	})
	for _, link := range result.Links {
		if _, exists := byURL[link.URL]; exists {
			t.Errorf("duplicate link URL %q", link.URL)
		}
		byURL[link.URL] = struct {
			linkType string
			password string
		}{link.Type, link.Password}
	}

	checks := map[string]struct {
		linkType string
		password string
	}{
		"https://pan.baidu.com/s/1dfyBU2ABaVepdzHhyXweRg?pwd=xeqy":      {"baidu", "xeqy"},
		"https://pan.baidu.com/s/passwordless123":                       {"baidu", ""},
		"https://pan.quark.cn/s/92f07db5654b":                           {"quark", "qwer"},
		"https://pan.xunlei.com/s/VP-EzzPFb8z7tideTMh-U-WTA1?pwd=vuiy#": {"xunlei", "vuiy"},
		"https://cloud.189.cn/t/abc123":                                 {"tianyi", ""},
		"https://drive.uc.cn/s/abc123":                                  {"uc", ""},
		"https://123pan.com/s/abc123":                                   {"123", ""},
		"https://115.com/s/abc123":                                      {"115", ""},
		"https://www.alipan.com/s/abc123":                               {"aliyun", ""},
		"https://guangyapan.com/s/abc123":                               {"guangya", ""},
		"https://caiyun.139.com/w/i/abc123":                             {"mobile", ""},
	}
	for linkURL, want := range checks {
		got, ok := byURL[linkURL]
		if !ok {
			t.Errorf("missing button link %q; got URLs %#v", linkURL, byURL)
			continue
		}
		if got.linkType != want.linkType || got.password != want.password {
			t.Errorf("link %q = (%q, %q), want (%q, %q)", linkURL, got.linkType, got.password, want.linkType, want.password)
		}
	}
}

func TestParseSearchResultsSkipsTelegramDateHeaderForTitle(t *testing.T) {
	const html = `<!DOCTYPE html>
<html><body>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="peccxinpd/408032">
    <div class="tgme_widget_message_text"><b>📅 9月9日</b><br><br>🎬 ✅【<mark>藏锋</mark>（2026）】【全23集】【4K高码】<br><br>类型：国产剧<br>网盘：百度网盘<br>📝 简介：测试简介</div>
    <div class="tgme_widget_message_footer">
      <a class="tgme_widget_message_date" href="https://t.me/peccxinpd/408032">
        <time datetime="2026-09-09T12:02:55+00:00">12:02</time>
      </a>
    </div>
  </div>
  <div class="tgme_widget_message_inline_keyboard">
    <a class="tgme_widget_message_inline_button url_button" href="https://pan.baidu.com/s/1dfyBU2ABaVepdzHhyXweRg?pwd=xeqy"><span>查看资源</span></a>
  </div>
</div>
</body></html>`

	results, _, err := ParseSearchResults(html, "peccxinpd")
	if err != nil {
		t.Fatalf("ParseSearchResults returned error: %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("expected one result, got %d", len(results))
	}
	if got := results[0].Title; !strings.Contains(got, "藏锋") {
		t.Fatalf("expected real work title instead of date header, got %q", got)
	}
	if got := results[0].Links[0].WorkTitle; !strings.Contains(got, "藏锋") {
		t.Fatalf("expected link work title to contain keyword, got %q", got)
	}
}
