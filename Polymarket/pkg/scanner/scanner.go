package scanner

import (
	"context"
	"fmt"
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

	for i := range wallets {
		wallets[i].Score = (wallets[i].Pnl / wallets[i].TotalVolume) / float64(max(wallets[i].TotalTrades, 1)) * 100000
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

	result := &types.ScanResult{
		GeneratedAt:  time.Now(),
		Markets:      markets,
		TopWallets:   wallets,
		TotalMarkets: len(markets),
		TotalWallets: len(wallets),
	}

	return result, nil
}
