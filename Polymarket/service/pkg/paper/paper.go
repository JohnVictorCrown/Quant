package paper

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
)

type Position struct {
	Size     float64 `json:"size"`
	Price    float64 `json:"price"`
	Market   string  `json:"market"`
	Outcome  string  `json:"outcome"`
}

type Data struct {
	Balance    float64              `json:"balance"`
	Positions  map[string]Position  `json:"positions"`
	RealizedPnl float64             `json:"realized_pnl"`
	TotalTrades int                 `json:"total_trades"`
}

type PaperTrader struct {
	mu   sync.Mutex
	path string
	data Data
}

func New(path string, stakeUSD float64) (*PaperTrader, error) {
	p := &PaperTrader{path: path, data: Data{
		Balance:   stakeUSD,
		Positions: make(map[string]Position),
	}}
	data, err := os.ReadFile(path)
	if err != nil {
		return p, nil
	}
	json.Unmarshal(data, &p.data)
	if p.data.Positions == nil {
		p.data.Positions = make(map[string]Position)
	}
	return p, nil
}

func (p *PaperTrader) Buy(assetID string, price, size float64, market, outcome string) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	cost := price * size
	if cost > p.data.Balance {
		return fmt.Errorf("insufficient balance: need $%.2f, have $%.2f", cost, p.data.Balance)
	}

	p.data.Balance -= cost
	p.data.Positions[assetID] = Position{Size: size, Price: price, Market: market, Outcome: outcome}
	p.data.TotalTrades++
	return nil
}

func (p *PaperTrader) Sell(assetID string, curPrice float64) (float64, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	pos, ok := p.data.Positions[assetID]
	if !ok {
		return 0, fmt.Errorf("position %s not found", assetID)
	}

	proceeds := curPrice * pos.Size
	pnl := proceeds - (pos.Price * pos.Size)
	p.data.Balance += proceeds
	p.data.RealizedPnl += pnl
	delete(p.data.Positions, assetID)
	p.data.TotalTrades++

	return pnl, nil
}

func (p *PaperTrader) GetPosition(assetID string) (Position, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	pos, ok := p.data.Positions[assetID]
	return pos, ok
}

func (p *PaperTrader) HasPosition(assetID string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	_, ok := p.data.Positions[assetID]
	return ok
}

func (p *PaperTrader) Stats() (balance float64, openPositions int, realizedPnl float64, totalTrades int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.data.Balance, len(p.data.Positions), p.data.RealizedPnl, p.data.TotalTrades
}

func (p *PaperTrader) AllPositions() map[string]Position {
	p.mu.Lock()
	defer p.mu.Unlock()
	cp := make(map[string]Position, len(p.data.Positions))
	for k, v := range p.data.Positions {
		cp[k] = v
	}
	return cp
}

func (p *PaperTrader) Balance() float64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.data.Balance
}

func (p *PaperTrader) RealizedPnl() float64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.data.RealizedPnl
}

func (p *PaperTrader) UnrealizedPnl(assetID string, curPrice float64) float64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	pos, ok := p.data.Positions[assetID]
	if !ok {
		return 0
	}
	return (curPrice * pos.Size) - (pos.Price * pos.Size)
}

func (p *PaperTrader) Save() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	data, err := json.Marshal(p.data)
	if err != nil {
		return err
	}
	return os.WriteFile(p.path, data, 0644)
}
