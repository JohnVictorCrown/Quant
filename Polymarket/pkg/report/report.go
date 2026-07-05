package report

import (
	"embed"
	"html/template"
	"os"
	"path/filepath"

	"polymarket-wallet-scanner/pkg/types"
)

//go:embed templates/*
var templateFS embed.FS

func Generate(result *types.ScanResult, outputPath string) error {
	tpl, err := template.New("report.html").Funcs(template.FuncMap{
		"add": func(a, b int) int { return a + b },
	}).ParseFS(templateFS, "templates/report.html")
	if err != nil {
		return err
	}

	if err := os.MkdirAll(filepath.Dir(outputPath), 0755); err != nil {
		return err
	}

	f, err := os.Create(outputPath)
	if err != nil {
		return err
	}
	defer f.Close()

	return tpl.Execute(f, result)
}
