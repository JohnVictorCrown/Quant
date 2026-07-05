package tracker

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	"polymarket-copytrader/pkg/config"
	"polymarket-copytrader/pkg/polymarket"
	"polymarket-copytrader/pkg/store"
)

type Tracker struct {
	cfg    *config.Config
	client *polymarket.Client
	store  *store.Store
	mu     sync.RWMutex

	lastScan     time.Time
	scanCount    int
	tradesCopied int
}

func New(cfg *config.Config, client *polymarket.Client, st *store.Store) *Tracker {
	return &Tracker{
		cfg:    cfg,
		client: client,
		store:  st,
	}
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

	seen := make(map[string]bool)
	for _, pos := range openPositions {
		seen[pos.Asset] = true
	}
	for _, pos := range closedPositions {
		seen[pos.Asset] = true
	}

	newCount := 0
	for assetID := range seen {
		if t.store.IsNew(wallet, assetID) {
			newCount++
			t.handleNewTrade(wallet, assetID, openPositions, closedPositions)
			t.store.Mark(wallet, assetID)
		}
	}

	return newCount, nil
}

func (t *Tracker) handleNewTrade(wallet, assetID string, openPositions, closedPositions []polymarket.Position) {
	for _, pos := range openPositions {
		if pos.Asset == assetID {
			trade := t.client.BuildCopyTrade(pos, t.cfg.CopyAmountUSD)
			log.Printf("  >> NEW OPEN TRADE: %s → %s (%s) at %.4f, shares: %.2f, $%.2f",
				wallet[:10], trade.Market, trade.Outcome, trade.Price, trade.Size, t.cfg.CopyAmountUSD)

			if t.client.CanTrade() {
				if err := t.client.PlaceOrder(context.Background(), trade); err != nil {
					log.Printf("  !! ORDER FAILED: %v", err)
				} else {
					log.Printf("  >> ORDER PLACED: %s", trade.TokenID[:16])
					t.mu.Lock()
					t.tradesCopied++
					t.mu.Unlock()
				}
			}
			return
		}
	}

	for _, pos := range closedPositions {
		if pos.Asset == assetID {
			log.Printf("  >> NEW CLOSED TRADE: %s → %s (%s) pnl: $%.2f",
				wallet[:10], pos.Title, pos.Outcome, pos.RealizedPnl)
			return
		}
	}
}
