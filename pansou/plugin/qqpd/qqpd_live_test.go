package qqpd

import (
	"os"
	"path/filepath"
	"testing"

	"pansou/plugin"
)

func TestQQPDLiveSearch(test *testing.T) {
	if os.Getenv("QQPD_LIVE_TEST") != "1" {
		test.Skip("set QQPD_LIVE_TEST=1 to test with the local login in an isolated cache")
	}
	previousStorage := StorageDir
	test.Cleanup(func() { StorageDir = previousStorage })
	StorageDir = os.Getenv("QQPD_LIVE_USERS_DIR")
	if StorageDir == "" {
		StorageDir = filepath.Join("..", "..", "cache", "qqpd_users")
	}
	instance := &QQPDPlugin{BaseAsyncPlugin: plugin.NewBaseAsyncPlugin("qqpd", 3)}
	instance.loadAllUsers()
	StorageDir = test.TempDir()
	users := instance.getActiveUsers()
	if len(users) == 0 {
		test.Fatal("no active local QQPD account with configured channels")
	}
	for _, keyword := range []string{"遮天", "凡人修仙传"} {
		response, err := instance.SearchWithResult(keyword, nil)
		if err != nil {
			test.Fatalf("live search failed: %v", err)
		}
		if len(response.Results) == 0 {
			test.Fatalf("live search %q returned no results", keyword)
		}
		linkCount := 0
		for _, result := range response.Results {
			linkCount += len(result.Links)
		}
		cachedCount, channelCount := 0, 0
		for _, user := range users {
			cachedCount += len(user.ChannelGuildIDs)
			channelCount += len(user.Channels)
		}
		test.Logf("keyword=%q results=%d links=%d resolved_channels=%d/%d warning=%q", keyword, len(response.Results), linkCount, cachedCount, channelCount, response.Message)
	}
}
