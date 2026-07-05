package scanner

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"polymarket-wallet-scanner/pkg/config"
	"polymarket-wallet-scanner/pkg/polymarket"
	"polymarket-wallet-scanner/pkg/types"
)

type Scanner struct {
	cfg    *config.Config
	client *polymarket.Client
}

func New(cfg *config.Config) (*Scanner, error) {
	c, err := polymarket.New()
	if err != nil {
		return nil, fmt.Errorf("create polymarket client: %w", err)
	}
	return &Scanner{cfg: cfg, client: c}, nil
}

func (s *Scanner) Scan(ctx context.Context) (*types.ScanResult, error) {
	if result, ok := s.loadCache(); ok {
		return result, nil
	}

	markets, err := s.client.FetchActiveMarkets(ctx, s.cfg.MarketLimit)
	if err != nil {
		return nil, fmt.Errorf("fetch markets: %w", err)
	}

	sort.Slice(markets, func(i, j int) bool {
		return markets[i].Volume > markets[j].Volume
	})

	wallets, err := s.client.FetchLeaderboard(ctx, s.cfg.MaxWallets)
	if err != nil {
		return nil, fmt.Errorf("fetch leaderboard: %w", err)
	}

	wallets, err = s.client.EnrichWithTradeCounts(ctx, wallets)
	if err != nil {
		return nil, fmt.Errorf("fetch trade counts: %w", err)
	}

	result := s.scoreAndRank(wallets, markets)
	s.saveCache(result)

	return result, nil
}

func (s *Scanner) scoreAndRank(wallets []types.Wallet, markets []types.MarketSummary) *types.ScanResult {
	for i := range wallets {
		wallets[i].Score = (wallets[i].Pnl / wallets[i].TotalVolume) * 100000
	}

	sort.Slice(wallets, func(i, j int) bool {
		return wallets[i].Score > wallets[j].Score
	})

	if len(wallets) > s.cfg.MaxWallets {
		wallets = wallets[:s.cfg.MaxWallets]
	}

	for i := range wallets {
		wallets[i].Rank = i + 1
	}

	return &types.ScanResult{
		GeneratedAt:  time.Now(),
		Markets:      markets,
		TopWallets:   wallets,
		TotalMarkets: len(markets),
		TotalWallets: len(wallets),
	}
}

func (s *Scanner) loadCache() (*types.ScanResult, bool) {
	info, err := os.Stat(s.cfg.CachePath)
	if err != nil {
		return nil, false
	}

	if time.Since(info.ModTime()) > time.Duration(s.cfg.CacheTTL)*time.Minute {
		return nil, false
	}

	data, err := os.ReadFile(s.cfg.CachePath)
	if err != nil {
		return nil, false
	}

	var result types.ScanResult
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, false
	}

	return &result, true
}

func (s *Scanner) saveCache(result *types.ScanResult) {
	if err := os.MkdirAll(filepath.Dir(s.cfg.CachePath), 0755); err != nil {
		return
	}

	data, err := json.Marshal(result)
	if err != nil {
		return
	}

	os.WriteFile(s.cfg.CachePath, data, 0644)
}
