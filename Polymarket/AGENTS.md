# Polymarket Wallet Scanner Bot

Go bot that scans top-performing Polymarket wallets and generates an HTML report.

## Build & Run

```bash
go mod init polymarket-wallet-scanner
go mod tidy
go run ./cmd/scanner
```

## Commands

- `go run ./cmd/scanner` — run the scanner and generate report
- `go build ./cmd/scanner` — build binary
- `go test ./...` — run all tests
- `go vet ./...` — static analysis
- `golangci-lint run ./...` — full lint

## Project Structure

```
├── cmd/scanner/       — entry point
├── pkg/
│   ├── config/        — config loading (env, flags)
│   ├── scanner/       — wallet scanning & filtering logic
│   ├── polymarket/    — SDK client wrappers
│   ├── report/        — HTML report generation
│   └── types/         — shared domain types
└── output/            — generated reports (gitignored)
```

## Architecture

1. **Config** — load API keys, target markets, scan parameters from env/config file
2. **Scanner** — fetch all traders for a market via SDK, rank by volume/profit, filter top N
3. **Report** — render top wallets into `output/report.html` with tables, charts, and metrics

## SDK Usage

Use `github.com/GoPolymarket/polymarket-go-sdk`. Key patterns:

```go
import "github.com/GoPolymarket/polymarket-go-sdk/pkg/clob/clobtypes"

// Fetch market data (read-only, no auth needed)
markets, err := client.CLOB.Markets(ctx, &clobtypes.MarketsRequest{...})

// Auto-paginate through all results
allMarkets, err := client.CLOB.MarketsAll(ctx, &clobtypes.MarketsRequest{Active: boolPtr(true)})
```

For read-only scanning (market data, prices, history), no auth/signer is required. Auth only needed for order placement.

## Conventions

- **No comments in code** unless absolutely necessary for a non-obvious workaround
- Use `internal/` packages for private code not meant for external import
- Errors should be wrapped with `fmt.Errorf("context: %w", err)`
- Use `context.Context` as first arg in all public functions
- HTML templates go in `pkg/report/templates/` (embedded via `embed.FS`)
- Keep the SDK abstraction in `pkg/polymarket/` — don't leak SDK types into scanner/report layers
