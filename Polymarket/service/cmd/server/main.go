package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"polymarket-copytrader/pkg/config"
	"polymarket-copytrader/pkg/polymarket"
	"polymarket-copytrader/pkg/server"
	"polymarket-copytrader/pkg/store"
	"polymarket-copytrader/pkg/tracker"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	st, err := store.New(cfg.StorePath)
	if err != nil {
		log.Fatalf("store: %v", err)
	}

	client, err := polymarket.New(cfg.PrivateKey, cfg.APIKey, cfg.APISecret, cfg.APIPassphrase)
	if err != nil {
		log.Fatalf("polymarket client: %v", err)
	}

	tr := tracker.New(cfg, client, st)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go tr.Start(ctx)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		cancel()
	}()

	srv := server.New(cfg, tr, st, client)
	if err := srv.Start(); err != nil {
		log.Fatalf("server: %v", err)
	}
}
