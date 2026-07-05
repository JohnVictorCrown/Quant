package store

import (
	"encoding/json"
	"os"
	"sync"
)

type Store struct {
	mu   sync.Mutex
	path string
	data Data
}

type Data struct {
	Seen map[string]map[string]bool `json:"seen"`
}

func New(path string) (*Store, error) {
	s := &Store{path: path, data: Data{Seen: make(map[string]map[string]bool)}}
	data, err := os.ReadFile(path)
	if err != nil {
		return s, nil
	}
	json.Unmarshal(data, &s.data)
	if s.data.Seen == nil {
		s.data.Seen = make(map[string]map[string]bool)
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
