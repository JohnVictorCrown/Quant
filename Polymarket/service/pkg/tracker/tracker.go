package tracker

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	"polymarket-copytrader/pkg/config"
	"polymarket-copytrader/pkg/paper"
	"polymarket-copytrader/pkg/polymarket"
	"polymarket-copytrader/pkg/store"
)

type Tracker struct {
	cfg    *config.Config
	client *polymarket.Client
	store  *store.Store
	paper  *paper.PaperTrader
	mu     sync.RWMutex

	lastScan     time.Time
	scanCount    int
	tradesCopied int
}

func New(cfg *config.Config, client *polymarket.Client, st *store.Store, pt *paper.PaperTrader) *Tracker {
	return &Tracker{
		cfg:    cfg,
		client: client,
		store:  st,
		paper:  pt,
	}
}

func (t *Tracker) Paper() *paper.PaperTrader {
	return t.paper
}

func (t *Tracker) LastScan() time.Time {
	t.mu.RLock()
	defer t.mu.RUnlock()
	return t.lastScan
}

func (t *Tracker) ScanCount() int {
	t.mu.RLock()
	defer t.mu.RUnlock()
	return t.scanCount
}

func (t *Tracker) TradesCopied() int {
	t.mu.RLock()
	defer t.mu.RUnlock()
	return t.tradesCopied
}

func (t *Tracker) Start(ctx context.Context) {
	ticker := time.NewTicker(time.Duration(t.cfg.ScanIntervalMin) * time.Minute)
	defer ticker.Stop()

	log.Printf("tracker started — scanning %d wallets every %d min (dry-run: %v)",
		len(t.cfg.Wallets), t.cfg.ScanIntervalMin, !t.client.CanTrade())

	t.runOnce(ctx)

	for {
		select {
		case <-ticker.C:
			t.runOnce(ctx)
		case <-ctx.Done():
			log.Println("tracker stopped")
			return
		}
	}
}

func (t *Tracker) runOnce(ctx context.Context) {
	log.Println("scanning wallets for new trades...")
	start := time.Now()

	for _, wallet := range t.cfg.Wallets {
		count, err := t.scanWallet(ctx, wallet)
		if err != nil {
			log.Printf("  [%s] scan error: %v", wallet[:10], err)
			continue
		}
		if count > 0 {
			log.Printf("  [%s] %d new trade(s) detected", wallet[:10], count)
		}
	}

	if err := t.store.Save(); err != nil {
		log.Printf("save store: %v", err)
	}
	if !t.client.CanTrade() && t.paper != nil {
		if err := t.paper.Save(); err != nil {
			log.Printf("save paper: %v", err)
		}
	}

	t.mu.Lock()
	t.lastScan = time.Now()
	t.scanCount++
	t.mu.Unlock()

	log.Printf("scan complete — %v", time.Since(start).Round(time.Second))
}

func (t *Tracker) scanWallet(ctx context.Context, wallet string) (int, error) {
	openPositions, err := t.client.FetchOpenPositions(ctx, wallet)
	if err != nil {
		return 0, fmt.Errorf("open positions: %w", err)
	}

	closedPositions, err := t.client.FetchClosedPositions(ctx, wallet)
	if err != nil {
		return 0, fmt.Errorf("closed positions: %w", err)
	}

	currentOpen := make(map[string]bool)
	for _, pos := range openPositions {
		currentOpen[pos.Asset] = true
	}

	prevOpen := t.store.GetOpenAssets(wallet)
	prevOpenSet := make(map[string]bool)
	for _, id := range prevOpen {
		prevOpenSet[id] = true
	}

	newCount := 0

	for _, pos := range openPositions {
		if t.store.IsNew(wallet, pos.Asset) {
			newCount++
			t.handleNewOpenTrade(wallet, pos)
			t.store.Mark(wallet, pos.Asset)
		}
		if !t.store.IsOpen(wallet, pos.Asset) {
			t.store.SetOpen(wallet, pos.Asset, true)
		}
	}

	for assetID := range prevOpenSet {
		if !currentOpen[assetID] {
			if t.store.IsCopied(wallet, assetID) {
				newCount++
				t.handleClosedTrade(wallet, assetID)
			}
			t.store.SetOpen(wallet, assetID, false)
		}
	}

	for _, pos := range closedPositions {
		if t.store.IsNew(wallet, pos.Asset) {
			t.store.Mark(wallet, pos.Asset)
		}
	}

	return newCount, nil
}

func (t *Tracker) handleNewOpenTrade(wallet string, pos polymarket.Position) {
	trade := t.client.BuildCopyTrade(pos, t.cfg.CopyAmountUSD)
	log.Printf("  >> NEW OPEN TRADE: %s → %s (%s) at %.4f, shares: %.2f, $%.2f",
		wallet[:10], trade.Market, trade.Outcome, trade.Price, trade.Size, t.cfg.CopyAmountUSD)

	t.store.SetCopied(wallet, pos.Asset, store.CopyInfo{
		Size:    trade.Size,
		Price:   trade.Price,
		Market:  trade.Market,
		Outcome: trade.Outcome,
	})

	if t.client.CanTrade() {
		if err := t.client.PlaceOrder(context.Background(), trade); err != nil {
			log.Printf("  !! ORDER FAILED: %v", err)
		} else {
			log.Printf("  >> ORDER PLACED: %s", trade.TokenID[:16])
			t.mu.Lock()
			t.tradesCopied++
			t.mu.Unlock()
		}
	} else if t.paper != nil {
		if err := t.paper.Buy(trade.TokenID, trade.Price, trade.Size, trade.Market, trade.Outcome); err != nil {
			log.Printf("  !! PAPER BUY FAILED: %v", err)
		} else {
			log.Printf("  >> PAPER BUY: %s — balance: $%.2f", trade.TokenID[:16], t.paper.Balance())
			t.mu.Lock()
			t.tradesCopied++
			t.mu.Unlock()
		}
	}
}

func (t *Tracker) handleClosedTrade(wallet, assetID string) {
	info, ok := t.store.GetCopied(wallet, assetID)
	if !ok {
		return
	}

	log.Printf("  >> CLOSED TRADE: %s → %s (%s) — closing %.2f shares",
		wallet[:10], info.Market, info.Outcome, info.Size)

	if t.client.CanTrade() {
		trade := polymarket.CopyTrade{
			TokenID: assetID,
			Price:   info.Price,
			Size:    info.Size,
			Market:  info.Market,
			Outcome: info.Outcome,
		}
		if err := t.client.CloseOrder(context.Background(), trade); err != nil {
			log.Printf("  !! CLOSE ORDER FAILED: %v", err)
		} else {
			log.Printf("  >> CLOSE ORDER PLACED: %s", assetID[:16])
			t.mu.Lock()
			t.tradesCopied++
			t.mu.Unlock()
		}
	} else if t.paper != nil {
		pnl, err := t.paper.Sell(assetID, info.Price)
		if err != nil {
			log.Printf("  !! PAPER SELL FAILED: %v", err)
		} else {
			log.Printf("  >> PAPER SELL: %s — pnl: $%.2f, balance: $%.2f", assetID[:16], pnl, t.paper.Balance())
			t.mu.Lock()
			t.tradesCopied++
			t.mu.Unlock()
		}
	}
}
