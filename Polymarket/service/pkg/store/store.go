package store

import (
	"encoding/json"
	"os"
	"sync"
)

type CopyInfo struct {
	Size     float64 `json:"size"`
	Price    float64 `json:"price"`
	Market   string  `json:"market"`
	Outcome  string  `json:"outcome"`
}

type Store struct {
	mu   sync.Mutex
	path string
	data Data
}

type Data struct {
	Seen   map[string]map[string]bool          `json:"seen"`
	Open   map[string]map[string]bool          `json:"open"`
	Copied map[string]map[string]CopyInfo `json:"copied"`
}

func New(path string) (*Store, error) {
	s := &Store{path: path, data: Data{
		Seen:   make(map[string]map[string]bool),
		Open:   make(map[string]map[string]bool),
		Copied: make(map[string]map[string]CopyInfo),
	}}
	data, err := os.ReadFile(path)
	if err != nil {
		return s, nil
	}
	json.Unmarshal(data, &s.data)
	if s.data.Seen == nil {
		s.data.Seen = make(map[string]map[string]bool)
	}
	if s.data.Open == nil {
		s.data.Open = make(map[string]map[string]bool)
	}
	if s.data.Copied == nil {
		s.data.Copied = make(map[string]map[string]CopyInfo)
	}
	return s, nil
}

func (s *Store) IsNew(walletAddr, assetID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Seen[walletAddr] == nil {
		return true
	}
	return !s.data.Seen[walletAddr][assetID]
}

func (s *Store) Mark(walletAddr, assetID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Seen[walletAddr] == nil {
		s.data.Seen[walletAddr] = make(map[string]bool)
	}
	s.data.Seen[walletAddr][assetID] = true
}

func (s *Store) SetOpen(walletAddr, assetID string, open bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Open[walletAddr] == nil {
		s.data.Open[walletAddr] = make(map[string]bool)
	}
	s.data.Open[walletAddr][assetID] = open
}

func (s *Store) IsOpen(walletAddr, assetID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Open[walletAddr] == nil {
		return false
	}
	return s.data.Open[walletAddr][assetID]
}

func (s *Store) GetOpenAssets(walletAddr string) []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var assets []string
	for id, open := range s.data.Open[walletAddr] {
		if open {
			assets = append(assets, id)
		}
	}
	return assets
}

func (s *Store) IsCopied(walletAddr, assetID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Copied[walletAddr] == nil {
		return false
	}
	_, ok := s.data.Copied[walletAddr][assetID]
	return ok
}

func (s *Store) SetCopied(walletAddr, assetID string, info CopyInfo) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Copied[walletAddr] == nil {
		s.data.Copied[walletAddr] = make(map[string]CopyInfo)
	}
	s.data.Copied[walletAddr][assetID] = info
}

func (s *Store) GetCopied(walletAddr, assetID string) (CopyInfo, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.data.Copied[walletAddr] == nil {
		return CopyInfo{}, false
	}
	info, ok := s.data.Copied[walletAddr][assetID]
	return info, ok
}

func (s *Store) Save() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	data, err := json.Marshal(s.data)
	if err != nil {
		return err
	}
	return os.WriteFile(s.path, data, 0644)
}

func (s *Store) Stats() (totalWallets int, totalTrades int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, assets := range s.data.Seen {
		totalWallets++
		totalTrades += len(assets)
	}
	return
}
