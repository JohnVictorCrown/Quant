package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"polymarket-wallet-scanner/pkg/config"
	"polymarket-wallet-scanner/pkg/report"
	"polymarket-wallet-scanner/pkg/scanner"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		cancel()
	}()

	s, err := scanner.New(cfg)
	if err != nil {
		log.Fatalf("scanner: %v", err)
	}

	result, err := s.Scan(ctx)
	if err != nil {
		log.Fatalf("scan: %v", err)
	}

	if err := report.Generate(result, cfg.OutputPath); err != nil {
		log.Fatalf("report: %v", err)
	}

	log.Printf("report written to %s", cfg.OutputPath)
}
