"""download.py
- Downloads/updates 5m raw CSVs from Binance public archive
- Verifies data integrity
- Saves/merges into data/price5m.parquet
"""

import zipfile
import urllib.request
import urllib.error
import time
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
INTERVAL = "5m"
ROOT = Path(__file__).parent
RAW = ROOT / "data"
RAW.mkdir(exist_ok=True)

COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
USE = ["open_time", "open", "high", "low", "close", "volume"]


def detect_ts_unit(series):
    sample = pd.to_numeric(series, errors='coerce').dropna().head(5)
    if len(sample) == 0:
        return "ms"
    max_val = sample.max()
    return "us" if max_val > 1e14 else "ms"


def dl_month(symbol, year, month):
    url = (
        f"https://data.binance.vision/data/spot/monthly/klines/"
        f"{symbol}/{INTERVAL}/{symbol}-{INTERVAL}-{year}-{month:02d}.zip"
    )
    data = RAW / f"{symbol}-{INTERVAL}-{year}-{month:02d}.csv"
    tmp = data.with_suffix(".tmp_zip")
    
    if data.exists() and data.stat().st_size > 100:
        return data, False
    if tmp.exists():
        tmp.unlink(missing_ok=True)
        
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            with open(tmp, "wb") as out_file:
                out_file.write(response.read())
                
        print(f"  DL {symbol} {year}-{month:02d}", flush=True)
        with zipfile.ZipFile(tmp, "r") as z:
            csv_files = [m for m in z.namelist() if m.endswith(".csv")]
            if not csv_files:
                raise ValueError("No CSV file found in ZIP archive")
            # Extract content directly to the destination file
            with z.open(csv_files[0]) as source, open(data, "wb") as target:
                target.write(source.read())
                
        tmp.unlink(missing_ok=True)
        return data, True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # File does not exist yet (expected for recent/future months)
            return None, False
        print(f"  HTTP Error {e.code} for {symbol} {year}-{month:02d}", flush=True)
        return None, False
    except Exception as e:
        print(f"  FAIL {symbol} {year}-{month:02d}: {e}", flush=True)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return None, False


def fuse(symbol, files):
    if not files:
        return None
    dfs = []
    for p in files:
        try:
            # Detect whether the CSV has headers
            with open(p, "r") as f:
                first_line = f.readline()
            has_header = "open" in first_line or "time" in first_line
            
            # Read full CSV to prevent crashes if column count is less than 12
            if has_header:
                df = pd.read_csv(p, header=0)
            else:
                df = pd.read_csv(p, header=None)
                
            # Safely assign names matching the actual column count parsed
            df.columns = COLS[:df.shape[1]]
            
            unit = detect_ts_unit(df["open_time"])
            df["open_time"] = pd.to_datetime(pd.to_numeric(df["open_time"], errors='coerce'), unit=unit)
            
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
                
            dfs.append(df[USE])
        except Exception as e:
            print(f"  Skip {p.name}: {e}")
            
    if not dfs:
        return None
        
    merged = pd.concat(dfs).sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    merged.insert(0, "symbol", symbol)
    min_dt = pd.Timestamp("2020-01-01")
    max_dt = pd.Timestamp("2030-01-01")
    merged = merged.loc[merged["open_time"].between(min_dt, max_dt)].copy()
    return merged


def verify(df):
    issues = []
    if (df["high"] < df["low"]).any():
        issues.append("high < low detected")
    if (df[["open", "high", "low", "close", "volume"]] < 0).any().any():
        issues.append("negative values detected")
    if df.duplicated(subset=["symbol", "open_time"]).any():
        issues.append("duplicate timestamps detected")
    if df[["open_time", "open", "high", "low", "close", "volume"]].isnull().any().any():
        issues.append("null values detected")
    if issues:
        raise ValueError("Verification failed: " + "; ".join(issues))
    print("Verification passed.")


def main():
    # MODIFIED: Output path now points inside the "data" folder (RAW)
    combined_path = RAW / "price5m.parquet"
    existing = None
    existing_periods = set()
    
    if combined_path.exists():
        try:
            existing = pd.read_parquet(combined_path)
            print(f"Existing master: {len(existing):,} rows")
            
            # Fast vectorized extraction of already processed symbol-year-month periods
            dt_col = existing["open_time"]
            existing_periods = set(
                zip(existing["symbol"], dt_col.dt.year, dt_col.dt.month)
            )
        except Exception as e:
            print(f"Existing parquet unreadable: {e}")

    # Determine maximum valid historical month dynamically using timezone-aware UTC
    now = datetime.now(timezone.utc)
    current_year = now.year
    current_month = now.month
    
    new_parts = []
    for sym in SYMBOLS:
        print(f"\n=== {sym} ===", flush=True)
        files = []
        # Dynamic loop from 2022 to the previous complete month
        for y in range(2022, current_year + 1):
            for m in range(1, 13):
                if y == current_year and m >= current_month:
                    continue  # Historical monthly data is only ready after month-end
                    
                data, got = dl_month(sym, y, m)
                if data:
                    # Parse only if it's missing from the Parquet, or if we just freshly downloaded it
                    if (sym, y, m) not in existing_periods or got:
                        files.append(data)
                time.sleep(0.15)
                
        files.sort()
        bad = [p for p in files if p.stat().st_size < 100]
        for p in bad:
            p.unlink(missing_ok=True)
            
        files = [p for p in files if p.stat().st_size >= 100]
        print(f"  New/Updated Files to Parse: {len(files)}", flush=True)
        df = fuse(sym, files)
        if df is not None and len(df) > 0:
            new_parts.append(df)

    if not new_parts and existing is None:
        raise SystemExit("No data available")

    combined = pd.concat(new_parts, ignore_index=True) if new_parts else pd.DataFrame()
    if existing is not None:
        combined = pd.concat([existing, combined], ignore_index=True)
        
    if len(combined) > 0:
        combined = combined.sort_values(["symbol", "open_time"]).drop_duplicates(["symbol", "open_time"]).reset_index(drop=True)
        verify(combined)
        combined.to_parquet(combined_path, index=False)
        print(f"\nFinal: {combined_path}")
        print(f"Rows: {len(combined):,}")
        print(combined.groupby("symbol")["open_time"].agg(["min", "max", "count"]).sort_index())
    else:
        print("No updates found.")


if __name__ == "__main__":
    main()