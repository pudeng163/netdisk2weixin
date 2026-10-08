package panlian

import (
	"testing"
)

func TestNormalizeModernVideoItem(t *testing.T) {
	item := normalizeVideoItem(VideoItem{
		ID:          61205,
		Title:       "侵略机器",
		Cover:       "https://example.test/cover.webp",
		Intro:       "简介",
		Year:        "2026",
		Remarks:     "HD",
		Type:        "动作片",
		Actor:       "演员",
		DirectorNew: "导演",
	})
	if item.VodID != 61205 || item.VodName != "侵略机器" || item.VodPic == "" || item.VodContent != "简介" {
		t.Fatalf("modern video fields were not normalized: %+v", item)
	}
}

func TestSelectModernLinksKeepsPanTypes(t *testing.T) {
	links := []VideoLink{
		{ID: 1, PanType: "baidu"},
		{ID: 2, PanType: "baidu"},
		{ID: 3, PanType: "quark"},
	}
	selected := selectModernLinks(links, 2)
	if len(selected) != 2 || selected[0].ID != 1 || selected[1].ID != 3 {
		t.Fatalf("unexpected selected links: %+v", selected)
	}
}

func TestModernResponseTypes(t *testing.T) {
	if got := normalizeLinkType("uc", "https://pan.uc.cn/s/example"); got != "uc" {
		t.Fatalf("unexpected UC link type: %s", got)
	}
	if got := normalizeLinkType("123", "https://1856539457.share.123pan.cn/123pan/example"); got != "123" {
		t.Fatalf("unexpected 123 link type: %s", got)
	}
	if !isLoginMessage("请先登录") || !isLoginMessage("ADMIN_AUTH_REQUIRED") {
		t.Fatal("login error detection failed")
	}
}
