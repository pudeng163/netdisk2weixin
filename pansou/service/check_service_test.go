package service

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"pansou/model"
)

type checkRoundTripperFunc func(*http.Request) (*http.Response, error)

func (roundTrip checkRoundTripperFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return roundTrip(request)
}

func TestCheck115ShareState(test *testing.T) {
	testCases := []struct {
		name        string
		response    string
		wantState   string
		wantSummary string
	}{
		{
			name:        "expired share with files and metadata",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"shareinfo":{"forbid_reason":"链接已过期","snap_id":"snapshot","share_title":"shared files"},"count":1,"list":[{"fid":"file"}]}}`,
			wantState:   checkStateBad,
			wantSummary: "链接已过期",
		},
		{
			name:        "expired share with list and no reason",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"list":[{"fid":"file"}]}}`,
			wantState:   checkStateBad,
			wantSummary: "链接状态异常(share_state=7)",
		},
		{
			name:        "expired share with count and no reason",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"count":1}}`,
			wantState:   checkStateBad,
			wantSummary: "链接状态异常(share_state=7)",
		},
		{
			name:        "expired share with snapshot and no reason",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"shareinfo":{"snap_id":"snapshot"}}}`,
			wantState:   checkStateBad,
			wantSummary: "链接状态异常(share_state=7)",
		},
		{
			name:        "expired share with title and no reason",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"shareinfo":{"share_title":"shared files"}}}`,
			wantState:   checkStateBad,
			wantSummary: "链接状态异常(share_state=7)",
		},
		{
			name:        "nested expired share state with files",
			response:    `{"state":true,"errno":0,"data":{"share_state":0,"shareinfo":{"share_state":7,"forbid_reason":"链接已过期"},"count":1}}`,
			wantState:   checkStateBad,
			wantSummary: "链接已过期",
		},
		{
			name:        "top level expired state overrides nested active state",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"shareinfo":{"share_state":1},"count":1}}`,
			wantState:   checkStateBad,
			wantSummary: "链接状态异常(share_state=7)",
		},
		{
			name:        "forbidden share with active state and files",
			response:    `{"state":true,"errno":0,"data":{"share_state":1,"shareinfo":{"forbid_reason":"  分享已取消  "},"count":1}}`,
			wantState:   checkStateBad,
			wantSummary: "分享已取消",
		},
		{
			name:        "forbidden share without state and with files",
			response:    `{"state":true,"errno":0,"data":{"shareinfo":{"forbid_reason":"链接已过期"},"count":1}}`,
			wantState:   checkStateBad,
			wantSummary: "链接已过期",
		},
		{
			name:        "password error with files",
			response:    `{"state":true,"errno":0,"data":{"share_state":7,"shareinfo":{"forbid_reason":"提取码错误"},"count":1}}`,
			wantState:   checkStateLocked,
			wantSummary: "提取码错误",
		},
		{
			name:        "active share without files",
			response:    `{"state":true,"errno":0,"data":{"share_state":1}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "nested active share without files",
			response:    `{"state":true,"errno":0,"data":{"shareinfo":{"share_state":1}}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "list without share state",
			response:    `{"state":true,"errno":0,"data":{"list":[{"fid":"file"}]}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "count with zero share state",
			response:    `{"state":true,"errno":0,"data":{"share_state":0,"count":1}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "snapshot without share state",
			response:    `{"state":true,"errno":0,"data":{"shareinfo":{"snap_id":"snapshot"}}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "title without share state",
			response:    `{"state":true,"errno":0,"data":{"shareinfo":{"share_title":"shared files"}}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "whitespace reason with files",
			response:    `{"state":true,"errno":0,"data":{"shareinfo":{"forbid_reason":"  "},"count":1}}`,
			wantState:   checkStateOK,
			wantSummary: "链接有效",
		},
		{
			name:        "empty share response",
			response:    `{"state":true,"errno":0,"data":{}}`,
			wantState:   checkStateBad,
			wantSummary: "链接状态异常(share_state=0)",
		},
		{
			name:        "API password error",
			response:    `{"state":false,"errno":1,"error":"提取码错误"}`,
			wantState:   checkStateLocked,
			wantSummary: "提取码错误",
		},
		{
			name:        "API expired error",
			response:    `{"state":false,"errno":1,"error":"链接已过期"}`,
			wantState:   checkStateBad,
			wantSummary: "链接已过期",
		},
	}

	for _, testCase := range testCases {
		test.Run(testCase.name, func(test *testing.T) {
			client := &http.Client{Transport: checkRoundTripperFunc(func(request *http.Request) (*http.Response, error) {
				if request.Method != http.MethodGet || request.URL.Host != "115cdn.com" || request.URL.Path != "/webapi/share/snap" {
					test.Fatalf("unexpected request: %s %s", request.Method, request.URL)
				}
				if request.URL.Query().Get("share_code") != "swff16b3ngy" || request.URL.Query().Get("receive_code") != "CNYY" {
					test.Fatalf("unexpected share parameters: %s", request.URL.RawQuery)
				}
				return &http.Response{
					StatusCode: http.StatusOK,
					Header:     http.Header{"Content-Type": []string{"application/json"}},
					Body:       io.NopCloser(strings.NewReader(testCase.response)),
				}, nil
			})}
			service := &CheckService{}
			item := model.CheckItem{
				DiskType: "115",
				URL:      "https://115.com/s/swff16b3ngy?password=CNYY",
			}

			result, err := service.check115(item, item.URL, client)
			if err != nil {
				test.Fatalf("check115 returned an error: %v", err)
			}
			if result.State != testCase.wantState || result.Summary != testCase.wantSummary {
				test.Fatalf("expected state %q and summary %q, got %#v", testCase.wantState, testCase.wantSummary, result)
			}
		})
	}
}
