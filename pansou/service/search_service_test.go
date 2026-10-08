package service

import (
	"testing"
	"time"

	"pansou/model"
)

func TestMergeResultsByTypeKeepsTelegramContentKeywordMatch(t *testing.T) {
	results := []model.SearchResult{{
		UniqueID: "testchannel_1",
		Channel:  "testchannel",
		Datetime: time.Now(),
		Title:    "📅 9月10日",
		Content:  "🎬【目标作品】简介中包含目标关键词",
		Links: []model.Link{{
			Type: "baidu",
			URL:  "https://pan.baidu.com/s/test-link",
		}},
	}}

	merged := mergeResultsByType(results, "目标关键词", nil)
	if got := len(merged["baidu"]); got != 1 {
		t.Fatalf("expected Telegram content keyword match to retain one link, got %d: %#v", got, merged)
	}
}
