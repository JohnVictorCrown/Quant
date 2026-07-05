package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Port            string
	Wallets         []string
	StorePath       string
	CopyAmountUSD   float64
	ScanIntervalMin int
	PrivateKey      string
	APIKey          string
	APISecret       string
	APIPassphrase   string
	UserAddress     string
}

func Load() (*Config, error) {
	cfg := &Config{
		Port:            envStr("PORT", "8080"),
		StorePath:       envStr("STORE_PATH", "copied_trades.json"),
		CopyAmountUSD:   envFloat("COPY_AMOUNT_USD", 10),
		ScanIntervalMin: envInt("SCAN_INTERVAL_MIN", 5),
		PrivateKey:      os.Getenv("POLY_PRIVATE_KEY"),
		APIKey:          os.Getenv("POLY_API_KEY"),
		APISecret:       os.Getenv("POLY_API_SECRET"),
		APIPassphrase:   os.Getenv("POLY_API_PASSPHRASE"),
		UserAddress:     os.Getenv("USER_ADDRESS"),
	}

	walletsPath := envStr("WALLETS_CONFIG", "wallets.config")
	data, err := os.ReadFile(walletsPath)
	if err != nil {
		return nil, fmt.Errorf("read wallets config: %w", err)
	}

	for _, addr := range strings.Split(strings.TrimSpace(string(data)), ",") {
		addr = strings.TrimSpace(addr)
		if addr != "" {
			cfg.Wallets = append(cfg.Wallets, addr)
		}
	}

	if len(cfg.Wallets) == 0 {
		return nil, fmt.Errorf("no wallets found in %s", walletsPath)
	}

	return cfg, nil
}

func (c *Config) CanPlaceOrders() bool {
	return c.PrivateKey != "" && c.APIKey != "" && c.APISecret != "" && c.APIPassphrase != ""
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
