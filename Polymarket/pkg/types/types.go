package types

import "time"

type Wallet struct {
	Address       string
	Name          string
	TotalVolume   float64
	Pnl           float64
	ROI           float64
	Score         float64
	WinRate       float64
	TotalTrades   int
	MarketsTraded int
	Rank          int
}

type MarketSummary struct {
	ID       string
	Question string
	Volume   float64
	Traders  int
}

type ScanResult struct {
	GeneratedAt  time.Time
	Markets      []MarketSummary
	TopWallets   []Wallet
	TotalMarkets int
	TotalWallets int
}
