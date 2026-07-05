package polymarket

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"time"

	"github.com/GoPolymarket/polymarket-go-sdk/v2"
	"github.com/GoPolymarket/polymarket-go-sdk/v2/pkg/clob/clobtypes"

	"polymarket-wallet-scanner/pkg/types"
)

type Client struct {
	sdk     *polymarket.Client
	httpCli *http.Client
}

func New() (*Client, error) {
	c, err := polymarket.NewClientE()
	if err != nil {
		return nil, fmt.Errorf("init sdk: %w", err)
	}
	return &Client{sdk: c, httpCli: http.DefaultClient}, nil
}

func (c *Client) FetchActiveMarkets(ctx context.Context, limit int) ([]types.MarketSummary, error) {
	active := true
	all, err := c.sdk.CLOB.MarketsAll(ctx, &clobtypes.MarketsRequest{Active: &active})
	if err != nil {
		return nil, fmt.Errorf("fetch markets: %w", err)
	}

	if limit > 0 && len(all) > limit {
		all = all[:limit]
	}

	summaries := make([]types.MarketSummary, 0, len(all))
	for _, m := range all {
		summaries = append(summaries, types.MarketSummary{
			ID:       m.ID,
			Question: m.Question,
			Volume:   volumeToFloat(m.Volume),
		})
	}
	return summaries, nil
}

type leaderboardEntry struct {
	Rank        string  `json:"rank"`
	ProxyWallet string  `json:"proxyWallet"`
	UserName    string  `json:"userName"`
	Pnl         float64 `json:"pnl"`
	Volume      float64 `json:"vol"`
}

func (c *Client) FetchLeaderboard(ctx context.Context, maxResults int) ([]types.Wallet, error) {
	var all []types.Wallet
	limit := 50
	if maxResults < limit {
		limit = maxResults
	}

	for offset := 0; offset < maxResults; offset += limit {
		remaining := maxResults - offset
		batch := limit
		if batch > remaining {
			batch = remaining
		}

		wallets, err := c.fetchLeaderboardPage(ctx, offset, batch)
		if err != nil {
			return nil, fmt.Errorf("page offset=%d: %w", offset, err)
		}

		all = append(all, wallets...)

		if len(wallets) < batch {
			break
		}
	}

	return all, nil
}

func (c *Client) fetchLeaderboardPage(ctx context.Context, offset, limit int) ([]types.Wallet, error) {
	url := fmt.Sprintf("https://data-api.polymarket.com/v1/leaderboard?offset=%d&limit=%d&orderBy=VOL&timePeriod=ALL&category=OVERALL", offset, limit)

	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpCli.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API %d: %s", resp.StatusCode, string(body))
	}

	var entries []leaderboardEntry
	if err := json.NewDecoder(resp.Body).Decode(&entries); err != nil {
		return nil, err
	}

	wallets := make([]types.Wallet, 0, len(entries))
	for _, e := range entries {
		roi := 0.0
		if e.Volume > 0 {
			roi = (e.Pnl / e.Volume) * 100
		}
		wallets = append(wallets, types.Wallet{
			Address:     e.ProxyWallet,
			Name:        e.UserName,
			TotalVolume: e.Volume,
			Pnl:         e.Pnl,
			ROI:         roi,
		})
	}

	return wallets, nil
}

type tradeStats struct {
	total int
	wins  int
}

func (c *Client) EnrichWithTradeCounts(ctx context.Context, wallets []types.Wallet) ([]types.Wallet, error) {
	type countResult struct {
		index  int
		stats  tradeStats
		err    error
	}

	ch := make(chan countResult, len(wallets))
	sem := make(chan struct{}, 5)

	for i, w := range wallets {
		go func(idx int, addr string) {
			sem <- struct{}{}
			defer func() { <-sem }()

			stats, err := c.fetchClosedStats(ctx, addr)
			ch <- countResult{idx, stats, err}
		}(i, w.Address)
	}

	for range wallets {
		r := <-ch
		if r.err != nil {
			return nil, r.err
		}
		wallets[r.index].TotalTrades = r.stats.total
		wallets[r.index].MarketsTraded = r.stats.total
		wr := 0.0
		if r.stats.total > 0 {
			wr = float64(r.stats.wins) / float64(r.stats.total) * 100
		}
		wallets[r.index].WinRate = wr
	}

	return wallets, nil
}

type closedPosition struct {
	RealizedPnl float64 `json:"realizedPnl"`
}

func (c *Client) fetchClosedStats(ctx context.Context, address string) (tradeStats, error) {
	url := fmt.Sprintf("https://data-api.polymarket.com/closed-positions?user=%s&limit=500", address)

	var lastErr error
	for attempt := 0; attempt < 5; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Duration(attempt*attempt)*time.Second + time.Duration(attempt*100)*time.Millisecond)
		}

		req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
		if err != nil {
			return tradeStats{}, err
		}

		resp, err := c.httpCli.Do(req)
		if err != nil {
			lastErr = err
			continue
		}

		if resp.StatusCode == http.StatusTooManyRequests {
			lastErr = fmt.Errorf("API 429")
			resp.Body.Close()
			continue
		}

		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			return tradeStats{}, fmt.Errorf("API %d", resp.StatusCode)
		}

		var positions []closedPosition
		if err := json.NewDecoder(resp.Body).Decode(&positions); err != nil {
			resp.Body.Close()
			return tradeStats{}, err
		}
		resp.Body.Close()

		wins := 0
		for _, p := range positions {
			if p.RealizedPnl > 0 {
				wins++
			}
		}
		return tradeStats{total: len(positions), wins: wins}, nil
	}
	return tradeStats{}, fmt.Errorf("rate limited after retries: %w", lastErr)
}

func volumeToFloat(v interface{}) float64 {
	switch val := v.(type) {
	case string:
		f, _, err := big.ParseFloat(val, 10, 64, big.ToNearestEven)
		if err != nil {
			return 0
		}
		fl, _ := f.Float64()
		return fl
	case float64:
		return val
	default:
		return 0
	}
}
