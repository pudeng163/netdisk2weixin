package qqpd

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"pansou/util/json"
)

const (
	qqpdAPIBaseURL      = "https://pd.qq.com/qunng/guild/gotrpc/auth/"
	qqpdGuildEndpoint   = "trpc.group_pro.cmd0x907e.Cmd0x907e/HandleProcess?forGuildId=1"
	qqpdSearchEndpoint  = "trpc.group_pro.in_guild_search_svr.InGuildSearch/NewSearch"
	qqpdMaxResponseSize = 4 << 20
)

func validGuildID(guildID string) bool {
	if guildID == "" || guildID[0] < '1' || guildID[0] > '9' {
		return false
	}
	_, err := strconv.ParseUint(guildID, 10, 64)
	return err == nil
}

func (instance *QQPDPlugin) requestAPI(endpoint, cookieStr, oidb string, payload interface{}) ([]byte, error) {
	pSkey := parseCookieString(cookieStr)["p_skey"]
	if pSkey == "" {
		return nil, fmt.Errorf("[QQPD] 登录Cookie缺少p_skey，请重新登录")
	}

	requestURL, err := url.Parse(qqpdAPIBaseURL + endpoint)
	if err != nil {
		return nil, fmt.Errorf("[QQPD] 无效的接口地址: %w", err)
	}
	query := requestURL.Query()
	query.Set("bkn", strconv.FormatInt(bkn(pSkey), 10))
	requestURL.RawQuery = query.Encode()

	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("[QQPD] 编码请求失败: %w", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, requestURL.String(), bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, fmt.Errorf("[QQPD] 创建请求失败: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/plain, */*")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	req.Header.Set("Referer", "https://pd.qq.com/")
	req.Header.Set("Origin", "https://pd.qq.com")
	req.Header.Set("X-QQ-Client-AppId", "537246381")
	req.Header.Set("x-oidb", oidb)
	req.Header.Set("Cookie", cookieStr)

	client := instance.httpClient
	if client == nil {
		client = http.DefaultClient
	}
	resp, err := client.Do(req)
	if err != nil {
		var requestErr *url.Error
		if errors.As(err, &requestErr) {
			err = requestErr.Err
		}
		return nil, fmt.Errorf("[QQPD] 请求失败: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("[QQPD] 接口返回HTTP %d", resp.StatusCode)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, qqpdMaxResponseSize+1))
	if err != nil {
		return nil, fmt.Errorf("[QQPD] 读取响应失败: %w", err)
	}
	if len(body) > qqpdMaxResponseSize {
		return nil, fmt.Errorf("[QQPD] 接口响应超过大小限制")
	}
	if bytes.Contains(body, []byte("EO-Bot-Js-Token")) {
		return nil, fmt.Errorf("[QQPD] 接口返回EdgeOne安全校验页面，请稍后重试")
	}
	var response struct {
		Retcode *int   `json:"retcode"`
		Message string `json:"message"`
		Error   struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &response); err != nil {
		return nil, fmt.Errorf("[QQPD] 接口响应不是有效JSON: %w", err)
	}
	if response.Retcode == nil {
		return nil, fmt.Errorf("[QQPD] 接口响应缺少retcode")
	}
	if *response.Retcode != 0 || response.Error.Code != 0 {
		message := response.Message
		if message == "" {
			message = response.Error.Message
		}
		return nil, fmt.Errorf("[QQPD] 接口错误 retcode=%d, code=%d: %s", *response.Retcode, response.Error.Code, message)
	}
	return body, nil
}

func (instance *QQPDPlugin) extractGuildIDFromChannelNumber(channelNumber, cookieStr string) (string, error) {
	if validGuildID(channelNumber) {
		return channelNumber, nil
	}
	if strings.TrimSpace(channelNumber) == "" {
		return "", fmt.Errorf("[QQPD] 频道号不能为空")
	}
	payload := map[string]interface{}{
		"guild_num": channelNumber,
		"cmd0xf57_req": map[string]interface{}{
			"msg_field_filter": map[string]interface{}{
				"msg_guild_info_filter": map[string]int{"uint32_guild_number": 1},
			},
			"rpt_req_guild_info_list": []map[string]interface{}{{}},
		},
	}
	body, err := instance.requestAPI(qqpdGuildEndpoint, cookieStr, `{"uint32_service_type":1}`, payload)
	if err != nil {
		return "", err
	}
	var response struct {
		Data struct {
			GuildResponse struct {
				Guilds []struct {
					GuildID string `json:"uint64_guild_id"`
					Result  int    `json:"uint32_result"`
				} `json:"rpt_rsp_guild_info_list"`
			} `json:"cmd0xf57_rsp"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &response); err != nil {
		return "", fmt.Errorf("[QQPD] 解析频道信息失败: %w", err)
	}
	guilds := response.Data.GuildResponse.Guilds
	if len(guilds) == 0 {
		return "", fmt.Errorf("[QQPD] 未找到频道信息，请检查频道号或访问权限")
	}
	if guilds[0].Result != 0 {
		return "", fmt.Errorf("[QQPD] 获取频道信息失败，code=%d", guilds[0].Result)
	}
	if !validGuildID(guilds[0].GuildID) {
		return "", fmt.Errorf("[QQPD] 频道接口未返回有效的数字guild_id")
	}
	return guilds[0].GuildID, nil
}

func (instance *QQPDPlugin) cacheGuildID(userHash, channelNumber, guildID string) error {
	if !validGuildID(guildID) {
		return fmt.Errorf("[QQPD] 拒绝缓存无效的guild_id")
	}
	instance.mu.Lock()
	defer instance.mu.Unlock()
	user, exists := instance.getUserByHash(userHash)
	if !exists {
		return nil
	}
	configured := false
	for _, channel := range user.Channels {
		if channel == channelNumber {
			configured = true
			break
		}
	}
	if !configured {
		return nil
	}
	if user.ChannelGuildIDs == nil {
		user.ChannelGuildIDs = make(map[string]string)
	}
	user.ChannelGuildIDs[channelNumber] = guildID
	if err := instance.persistUserLocked(user); err != nil {
		return fmt.Errorf("[QQPD] 保存频道ID缓存失败: %w", err)
	}
	return nil
}

func (instance *QQPDPlugin) resolveChannelTasks(tasks []ChannelTask) ([]ChannelTask, error) {
	resolved := make([]ChannelTask, len(tasks))
	taskErrors := make([]error, len(tasks))
	semaphore := make(chan struct{}, MaxConcurrentChannels)
	var workers sync.WaitGroup
	for index, task := range tasks {
		workers.Add(1)
		semaphore <- struct{}{}
		go func(index int, task ChannelTask) {
			defer workers.Done()
			defer func() { <-semaphore }()
			if !validGuildID(task.GuildID) {
				guildID, err := instance.extractGuildIDFromChannelNumber(task.ChannelID, task.Cookie)
				if err != nil {
					taskErrors[index] = fmt.Errorf("频道 %s: %w", task.ChannelID, err)
					return
				}
				task.GuildID = guildID
				if err := instance.cacheGuildID(task.UserHash, task.ChannelID, guildID); err != nil {
					taskErrors[index] = fmt.Errorf("频道 %s: %w", task.ChannelID, err)
				}
			}
			resolved[index] = task
		}(index, task)
	}
	workers.Wait()
	ready := make([]ChannelTask, 0, len(tasks))
	for _, task := range resolved {
		if validGuildID(task.GuildID) {
			ready = append(ready, task)
		}
	}
	return ready, errors.Join(taskErrors...)
}
