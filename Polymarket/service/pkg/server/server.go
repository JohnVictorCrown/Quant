package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"polymarket-copytrader/pkg/config"
	"polymarket-copytrader/pkg/polymarket"
	"polymarket-copytrader/pkg/store"
	"polymarket-copytrader/pkg/tracker"
)

type Server struct {
	cfg     *config.Config
	tracker *tracker.Tracker
	store   *store.Store
	client  *polymarket.Client
	started time.Time
}

func New(cfg *config.Config, tr *tracker.Tracker, st *store.Store, cl *polymarket.Client) *Server {
	return &Server{cfg: cfg, tracker: tr, store: st, client: cl, started: time.Now()}
}

func (s *Server) Start() error {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/uptime", s.handleUptime)
	mux.HandleFunc("/status", s.handleStatus)
	mux.HandleFunc("/balance", s.handleBalance)

	addr := ":" + s.cfg.Port
	fmt.Printf("server listening on %s\n", addr)
	return http.ListenAndServe(addr, corsMiddleware(mux))
}

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == "OPTIONS" {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		next.ServeHTTP(w, r)
	})
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (s *Server) handleUptime(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"uptime":  time.Since(s.started).Round(time.Second).String(),
		"started": s.started.Format(time.RFC3339),
	})
}

func (s *Server) handleBalance(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	addr := s.cfg.UserAddress
	if addr == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "USER_ADDRESS not set in config"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	resp := map[string]interface{}{
		"address": addr,
	}

	openPositions, err := s.client.FetchOpenPositions(ctx, addr)
	if err != nil {
		resp["open_positions_error"] = err.Error()
	} else {
		positionValue := 0.0
		for i := range openPositions {
			positionValue += openPositions[i].Size * openPositions[i].CurPrice
		}
		resp["open_positions"] = openPositions
		resp["open_positions_count"] = len(openPositions)
		resp["position_value"] = positionValue
	}

	closedPositions, err := s.client.FetchClosedPositions(ctx, addr)
	if err != nil {
		resp["closed_positions_error"] = err.Error()
	} else {
		totalPnl := 0.0
		wins := 0
		for _, p := range closedPositions {
			totalPnl += p.RealizedPnl
			if p.RealizedPnl > 0 {
				wins++
			}
		}
		resp["closed_positions_count"] = len(closedPositions)
		resp["total_realized_pnl"] = totalPnl
		resp["win_rate"] = 0.0
		if len(closedPositions) > 0 {
			resp["win_rate"] = fmt.Sprintf("%.1f%%", float64(wins)/float64(len(closedPositions))*100)
		}
	}

	resp["total_trades"] = 0
	if v, ok := resp["open_positions_count"].(int); ok {
		resp["total_trades"] = resp["total_trades"].(int) + v
	}
	if v, ok := resp["closed_positions_count"].(int); ok {
		resp["total_trades"] = resp["total_trades"].(int) + v
	}

	json.NewEncoder(w).Encode(resp)
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	storeWallets, storeTrades := s.store.Stats()

	status := map[string]interface{}{
		"uptime":          time.Since(s.started).Round(time.Second).String(),
		"tracked_wallets": len(s.cfg.Wallets),
		"scan_count":      s.tracker.ScanCount(),
		"trades_copied":   s.tracker.TradesCopied(),
		"store_wallets":   storeWallets,
		"store_trades":    storeTrades,
		"live_trading":    s.cfg.CanPlaceOrders(),
		"copy_amount_usd": s.cfg.CopyAmountUSD,
		"scan_interval":   fmt.Sprintf("%dm", s.cfg.ScanIntervalMin),
	}

	if ls := s.tracker.LastScan(); !ls.IsZero() {
		status["last_scan"] = ls.Format(time.RFC3339)
	}

	json.NewEncoder(w).Encode(status)
}
