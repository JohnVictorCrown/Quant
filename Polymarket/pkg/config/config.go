package config

import (
	"os"
	"strconv"
)

type Config struct {
	MaxWallets  int
	MinVolume   float64
	OutputPath  string
	MarketLimit int
}

func Load() (*Config, error) {
	return &Config{
		MaxWallets:  envInt("MAX_WALLETS", 1000),
		MinVolume:   envFloat("MIN_VOLUME", 1000),
		OutputPath:  envStr("OUTPUT_PATH", "output/report.html"),
		MarketLimit: envInt("MARKET_LIMIT", 20),
	}, nil
}

func envStr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		n, err := strconv.Atoi(v)
		if err == nil {
			return n
		}
	}
	return fallback
}

func envFloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		f, err := strconv.ParseFloat(v, 64)
		if err == nil {
			return f
		}
	}
	return fallback
}
