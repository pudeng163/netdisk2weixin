package qqpd

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	"pansou/plugin"
	"pansou/util/json"

	"github.com/gin-gonic/gin"
)

const testGuildResponse = `{"retcode":0,"data":{"cmd0xf57_rsp":{"rpt_rsp_guild_info_list":[{"uint64_guild_id":"592843764045681811","uint32_result":0}]}}}`
const testSearchResponse = `{"retcode":0,"data":{"union_result":{"guild_feeds":[{"title":"名称：遮天","content":"遮天 https://pan.quark.cn/s/abcdef123456","create_time":"1750000000"}]}}}`

type qqpdTestTransport func(*http.Request) (*http.Response, error)

func (transport qqpdTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return transport(request)
}

func newTestQQPD(test *testing.T, handler qqpdTestTransport) *QQPDPlugin {
	test.Helper()
	previousStorage := StorageDir
	StorageDir = test.TempDir()
	test.Cleanup(func() { StorageDir = previousStorage })
	return &QQPDPlugin{
		BaseAsyncPlugin: plugin.NewBaseAsyncPlugin("qqpd", 3),
		httpClient:      &http.Client{Transport: handler},
	}
}

func testHTTPResponse(body string) *http.Response {
	return &http.Response{
		StatusCode: http.StatusOK,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func storeTestUser(instance *QQPDPlugin) *User {
	user := &User{
		Hash:            "qqpd-test-user",
		Cookie:          "uin=o0123456; p_skey=test-key",
		Status:          "active",
		Channels:        []string{"pd97631607"},
		ChannelGuildIDs: make(map[string]string),
	}
	instance.users.Store(user.Hash, user)
	return user
}

func TestResolveGuildIDUsesOfficialAPI(test *testing.T) {
	var requests atomic.Int32
	instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
		requests.Add(1)
		if request.Method != http.MethodPost || request.URL.Path != "/qunng/guild/gotrpc/auth/trpc.group_pro.cmd0x907e.Cmd0x907e/HandleProcess" {
			test.Error("resolver must use the authenticated guild API, not the HTML page")
		}
		if request.URL.Query().Get("bkn") != strconv.FormatInt(bkn("test-key"), 10) {
			test.Error("resolver has an incorrect bkn")
		}
		if request.Header.Get("x-oidb") != `{"uint32_service_type":1}` || request.Header.Get("X-QQ-Client-AppId") != "537246381" {
			test.Error("resolver is missing official request headers")
		}
		if cookie, err := request.Cookie("p_skey"); err != nil || cookie.Value != "test-key" {
			test.Error("resolver must carry the existing login cookie")
		}
		if _, hasDeadline := request.Context().Deadline(); !hasDeadline {
			test.Error("request has no deadline")
		}
		body, _ := io.ReadAll(request.Body)
		var payload struct {
			GuildNumber string `json:"guild_num"`
			GuildQuery  struct {
				Guilds []map[string]interface{} `json:"rpt_req_guild_info_list"`
			} `json:"cmd0xf57_req"`
		}
		if err := json.Unmarshal(body, &payload); err != nil || payload.GuildNumber != "pd97631607" || len(payload.GuildQuery.Guilds) != 1 {
			test.Error("resolver sent an unexpected payload")
		}
		return testHTTPResponse(testGuildResponse), nil
	})
	guildID, err := instance.extractGuildIDFromChannelNumber("pd97631607", "p_skey=test-key")
	if err != nil || guildID != "592843764045681811" {
		test.Fatalf("resolve guild: id=%q error=%v", guildID, err)
	}
	for _, directID := range []string{"592843764045681811", "18446744073709551615"} {
		resolved, err := instance.extractGuildIDFromChannelNumber(directID, "")
		if err != nil || resolved != directID {
			test.Fatalf("numeric ID should not require a request: %v", err)
		}
	}
	if requests.Load() != 1 {
		test.Fatalf("expected one lookup, got %d", requests.Load())
	}
}

func TestValidGuildID(test *testing.T) {
	for _, invalid := range []string{"", "0", "01", "pd97631607", "+592843764045681811", "-1", "1.0", "18446744073709551616"} {
		if validGuildID(invalid) {
			test.Errorf("accepted invalid guild ID %q", invalid)
		}
	}
}

func TestResolverRejectsUpstreamFailures(test *testing.T) {
	cases := []struct {
		name   string
		body   string
		status int
		want   string
	}{
		{"business error with data", `{"retcode":155,"message":"invalid guild","data":{}}`, 200, "retcode=155"},
		{"nested backend error", `{"retcode":0,"error":{"code":221,"message":"backend failed"},"data":{}}`, 200, "code=221"},
		{"missing retcode", `{"data":{}}`, 200, "缺少retcode"},
		{"missing guild", `{"retcode":0,"data":{}}`, 200, "未找到频道信息"},
		{"non numeric ID", strings.Replace(testGuildResponse, "592843764045681811", "pd97631607", 1), 200, "数字guild_id"},
		{"guild error", strings.Replace(testGuildResponse, `"uint32_result":0`, `"uint32_result":7`, 1), 200, "code=7"},
		{"challenge page", `<html><script>document.cookie="EO-Bot-Js-Token=test"</script></html>`, 200, "EdgeOne"},
		{"invalid JSON", `<html>unavailable</html>`, 200, "JSON"},
		{"HTTP error", "unavailable", 503, "HTTP 503"},
		{"oversized body", strings.Repeat(" ", qqpdMaxResponseSize+1), 200, "大小限制"},
	}
	for _, testCase := range cases {
		test.Run(testCase.name, func(test *testing.T) {
			instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
				response := testHTTPResponse(testCase.body)
				response.StatusCode = testCase.status
				return response, nil
			})
			guildID, err := instance.extractGuildIDFromChannelNumber("pd97631607", "p_skey=test-key")
			if err == nil || !strings.Contains(err.Error(), testCase.want) || guildID != "" {
				test.Fatalf("expected %q, got id=%q error=%v", testCase.want, guildID, err)
			}
		})
	}
}

func TestRequestErrorsDoNotExposeCredentials(test *testing.T) {
	instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
		return nil, errors.New("network unavailable")
	})
	_, err := instance.extractGuildIDFromChannelNumber("pd97631607", "p_skey=private-test-key")
	if err == nil || strings.Contains(err.Error(), "bkn=") || strings.Contains(err.Error(), "private-test-key") {
		test.Fatal("transport error must not expose the authenticated URL or cookie")
	}
	_, err = instance.extractGuildIDFromChannelNumber("pd97631607", "uin=o0123456")
	if err == nil || !strings.Contains(err.Error(), "p_skey") {
		test.Fatal("missing login credentials must be reported")
	}
}

func TestSearchRepairsAndPersistsLegacyCache(test *testing.T) {
	var lookups atomic.Int32
	var searches atomic.Int32
	instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
		if strings.HasSuffix(request.URL.Path, "/HandleProcess") {
			lookups.Add(1)
			return testHTTPResponse(testGuildResponse), nil
		}
		if !strings.HasSuffix(request.URL.Path, "/NewSearch") {
			return nil, fmt.Errorf("unexpected request path")
		}
		searches.Add(1)
		body, _ := io.ReadAll(request.Body)
		var payload struct {
			GuildID string `json:"guild_id"`
		}
		if err := json.Unmarshal(body, &payload); err != nil || !validGuildID(payload.GuildID) {
			test.Error("search received a non-numeric guild ID")
		}
		return testHTTPResponse(testSearchResponse), nil
	})
	user := storeTestUser(instance)
	user.Channels = append(user.Channels, "m250319e25")
	user.ChannelGuildIDs["pd97631607"] = "pd97631607"
	user.ChannelGuildIDs["m250319e25"] = "593406084000842514"
	originalCookie := user.Cookie
	if err := instance.persistUser(user); err != nil {
		test.Fatal(err)
	}
	instance.loadAllUsers()
	user, _ = instance.getUserByHash(user.Hash)
	if _, exists := user.ChannelGuildIDs["pd97631607"]; exists {
		test.Fatal("legacy non-numeric cache was not discarded during load")
	}
	for iteration := 0; iteration < 2; iteration++ {
		response, err := instance.SearchWithResult("遮天", nil)
		if err != nil || len(response.Results) != 2 {
			test.Fatalf("search returned %d results: %v", len(response.Results), err)
		}
		for _, result := range response.Results {
			if result.Channel != "" || !strings.HasPrefix(result.UniqueID, "qqpd-") || len(result.Links) == 0 {
				test.Fatal("search result violates the plugin contract")
			}
		}
	}
	if lookups.Load() != 1 || searches.Load() != 4 {
		test.Fatalf("cache was not reused: lookups=%d searches=%d", lookups.Load(), searches.Load())
	}
	data, err := os.ReadFile(filepath.Join(StorageDir, user.Hash+".json"))
	if err != nil {
		test.Fatal(err)
	}
	var persisted User
	if err := json.Unmarshal(data, &persisted); err != nil {
		test.Fatal(err)
	}
	if persisted.ChannelGuildIDs["pd97631607"] != "592843764045681811" || persisted.Cookie != originalCookie || len(persisted.Channels) != 2 {
		test.Fatal("repaired ID must be persisted without changing login or channels")
	}
}

func TestFailedLookupIsRetriedWithoutPoisoningCache(test *testing.T) {
	var lookups atomic.Int32
	instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
		if strings.HasSuffix(request.URL.Path, "/HandleProcess") {
			if lookups.Add(1) == 1 {
				return testHTTPResponse(`{"retcode":155,"message":"temporary error"}`), nil
			}
			return testHTTPResponse(testGuildResponse), nil
		}
		return testHTTPResponse(testSearchResponse), nil
	})
	user := storeTestUser(instance)
	response, err := instance.SearchWithResult("遮天", nil)
	if err == nil || len(response.Results) != 0 || len(user.ChannelGuildIDs) != 0 {
		test.Fatal("failed resolution must report an error without caching a short code")
	}
	response, err = instance.SearchWithResult("遮天", nil)
	if err != nil || len(response.Results) != 1 || lookups.Load() != 2 {
		test.Fatalf("failed lookup was not retried: %v", err)
	}
}

func TestManagementSearchReportsErrorsAndPartialResults(test *testing.T) {
	for _, includeWorkingChannel := range []bool{false, true} {
		test.Run(strconv.FormatBool(includeWorkingChannel), func(test *testing.T) {
			instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
				body, _ := io.ReadAll(request.Body)
				if strings.Contains(string(body), "593406084000842514") {
					return testHTTPResponse(testSearchResponse), nil
				}
				return testHTTPResponse(`{"retcode":155,"message":"search unavailable","data":{}}`), nil
			})
			user := storeTestUser(instance)
			user.ChannelGuildIDs[user.Channels[0]] = "592843764045681811"
			if includeWorkingChannel {
				user.Channels = append(user.Channels, "m250319e25")
				user.ChannelGuildIDs["m250319e25"] = "593406084000842514"
			}
			recorder := httptest.NewRecorder()
			ctx, _ := gin.CreateTestContext(recorder)
			instance.handleTestSearchWithData(ctx, user.Hash, map[string]interface{}{"keyword": "遮天"})
			var response struct {
				Success bool   `json:"success"`
				Message string `json:"message"`
				Data    struct {
					Total   int    `json:"total_results"`
					Warning string `json:"warning"`
				} `json:"data"`
			}
			if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
				test.Fatal(err)
			}
			if includeWorkingChannel {
				if !response.Success || response.Data.Total != 1 || !strings.Contains(response.Data.Warning, "retcode=155") {
					test.Fatal("partial results and their warning must both be returned")
				}
			} else if response.Success || !strings.Contains(response.Message, "retcode=155") {
				test.Fatal("upstream failure was incorrectly reported as zero successful results")
			}
		})
	}
}

func TestSearchAllowsLegitimateEmptyResults(test *testing.T) {
	for _, body := range []string{`{"retcode":0,"data":{"union_result":{"guild_feeds":[]}}}`, `{"retcode":0,"data":{"union_result":null}}`} {
		instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
			return testHTTPResponse(body), nil
		})
		results, err := instance.searchSingleChannel("不存在的关键词", "p_skey=test-key", "pd97631607", "592843764045681811")
		if err != nil || len(results) != 0 {
			test.Fatalf("legitimate empty search failed: %v", err)
		}
	}
}

func TestConcurrentChannelCacheUpdates(test *testing.T) {
	instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
		return testHTTPResponse(testGuildResponse), nil
	})
	user := storeTestUser(instance)
	user.Channels = nil
	for index := 0; index < 12; index++ {
		user.Channels = append(user.Channels, fmt.Sprintf("pd%d", index+1))
	}
	tasks, err := instance.resolveChannelTasks(instance.buildChannelTasks([]*User{user}))
	if err != nil || len(tasks) != 12 || len(user.ChannelGuildIDs) != 12 {
		test.Fatalf("concurrent cache repair failed: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(StorageDir, user.Hash+".json"))
	if err != nil {
		test.Fatal(err)
	}
	var persisted User
	if err := json.Unmarshal(data, &persisted); err != nil || len(persisted.ChannelGuildIDs) != 12 {
		test.Fatal("concurrent cache updates corrupted or lost persisted IDs")
	}
}

func TestSaveChannelsRebuildsOnlyValidMappings(test *testing.T) {
	instance := newTestQQPD(test, func(request *http.Request) (*http.Response, error) {
		body, _ := io.ReadAll(request.Body)
		if strings.Contains(string(body), "unavailable") {
			return testHTTPResponse(`{"retcode":155,"message":"lookup failed"}`), nil
		}
		return testHTTPResponse(testGuildResponse), nil
	})
	user := storeTestUser(instance)
	user.ChannelGuildIDs["pd97631607"] = "pd97631607"
	user.ChannelGuildIDs["removed"] = "593406084000842514"
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	instance.handleSetChannelsWithData(ctx, user.Hash, map[string]interface{}{
		"channels": []interface{}{"pd97631607", "pd97631607", "unavailable"},
	})
	var response struct {
		Success bool `json:"success"`
		Data    struct {
			Cached  int    `json:"guild_ids_cached"`
			Warning string `json:"warning"`
		} `json:"data"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		test.Fatal(err)
	}
	if !response.Success || response.Data.Cached != 1 || !strings.Contains(response.Data.Warning, "retcode=155") {
		test.Fatal("saved configuration must report unresolved channels")
	}
	if len(user.Channels) != 2 || user.ChannelGuildIDs["pd97631607"] != "592843764045681811" || len(user.ChannelGuildIDs) != 1 {
		test.Fatal("saved configuration retained an invalid or deleted mapping")
	}
	if err := instance.cacheGuildID(user.Hash, "removed", "593406084000842514"); err != nil {
		test.Fatal(err)
	}
	if len(user.ChannelGuildIDs) != 1 {
		test.Fatal("an in-flight lookup restored a removed channel's cache")
	}
}
