"""indicator_features.py
Reads price5m.parquet from the data folder, calculates 35 popular technical indicators 
exclusively on the 5-minute timeframe, and writes the output partitioned in 8 parts.
"""

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
OUT = ROOT / "data"
OUT.mkdir(exist_ok=True)


def calculate_wma(series, window):
    """Calculates Weighted Moving Average."""
    weights = np.arange(1, window + 1)
    return series.rolling(window).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def add_all_indicators(df):
    df = df.copy()

    # --- 1. Moving Averages (SMA, EMA, WMA) ---
    ma_windows = [5, 10, 20, 50, 100, 200]
    for w in ma_windows:
        df[f"sma_{w}"] = df["close"].rolling(w).mean()
        df[f"ema_{w}"] = df["close"].ewm(span=w, adjust=False).mean()
    
    for w in [10, 20, 50]:
        df[f"wma_{w}"] = calculate_wma(df["close"], w)

    # --- 2. Momentum & Oscillators ---
    # MACD (12, 26, 9)
    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema_12 - ema_26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Classic Wilder's RSI (14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # Stochastic Oscillator (%K 14, %D 3)
    low_14 = df["low"].rolling(14).min()
    high_14 = df["high"].rolling(14).max()
    df["stoch_k"] = 100 * (df["close"] - low_14) / (high_14 - low_14 + 1e-9)
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # Commodity Channel Index (CCI 20)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_sma = tp.rolling(20).mean()
    tp_md = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci_20"] = (tp - tp_sma) / (0.015 * tp_md + 1e-9)

    # Williams %R (14)
    df["williams_r"] = -100 * (high_14 - df["close"]) / (high_14 - low_14 + 1e-9)

    # Rate of Change (ROC 12)
    df["roc_12"] = 100 * df["close"].pct_change(12)

    # Ichimoku Cloud (Tenkan-sen 9, Kijun-sen 26)
    df["ichimoku_tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["ichimoku_kijun"] = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2

    # --- 3. Volatility & Trend ---
    # Bollinger Bands (20, 2)
    sma_20 = df["close"].rolling(20).mean()
    std_20 = df["close"].rolling(20).std()
    df["bb_upper"] = sma_20 + (2 * std_20)
    df["bb_lower"] = sma_20 - (2 * std_20)
    df["bb_b"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-9)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (sma_20 + 1e-9)

    # Average True Range (ATR 14)
    high_low = df["high"] - df["low"]
    high_close_prev = (df["high"] - df["close"].shift(1)).abs()
    low_close_prev = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    df["atr_14"] = tr.ewm(alpha=1/14, adjust=False).mean()

    # Average Directional Index (ADX 14)
    up_move = df["high"].diff()
    down_move = df["low"].shift(1) - df["low"]
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    tr_smooth = tr.ewm(alpha=1/14, adjust=False).mean()
    pos_di = 100 * (pd.Series(pos_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / (tr_smooth + 1e-9))
    neg_di = 100 * (pd.Series(neg_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / (tr_smooth + 1e-9))
    
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di + 1e-9)
    df["adx_14"] = dx.ewm(alpha=1/14, adjust=False).mean()

    # --- 4. Volume ---
    # On-Balance Volume (OBV)
    direction = np.sign(df["close"].diff().fillna(0))
    df["obv"] = (direction * df["volume"]).cumsum()

    # Chaikin Money Flow (CMF 20)
    mf_mult = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"] + 1e-9)
    mf_vol = mf_mult * df["volume"]
    df["cmf_20"] = mf_vol.rolling(20).sum() / (df["volume"].rolling(20).sum() + 1e-9)

    # Money Flow Index (MFI 14)
    raw_mf = tp * df["volume"]
    tp_diff = tp.diff()
    pos_mf = np.where(tp_diff > 0, raw_mf, 0.0)
    neg_mf = np.where(tp_diff < 0, raw_mf, 0.0)
    pos_mf_sum = pd.Series(pos_mf, index=df.index).rolling(14).sum()
    neg_mf_sum = pd.Series(neg_mf, index=df.index).rolling(14).sum()
    mfr = pos_mf_sum / (neg_mf_sum + 1e-9)
    df["mfi_14"] = 100 - (100 / (1 + mfr))

    # Rolling Volume Weighted Average Price (VWAP 24)
    vwap_num = (df["close"] * df["volume"]).rolling(24).sum()
    vwap_den = df["volume"].rolling(24).sum()
    df["vwap_24"] = vwap_num / (vwap_den + 1e-9)

    return df


def main():
    # MODIFIED: Points directly to OUT / "price5m.parquet" inside the data folder
    src = OUT / "price5m.parquet"
    if not src.exists():
        raise SystemExit(f"Missing price5m.parquet at: {src}\nRun download.py first.")

    df = pd.read_parquet(src)
    cols = ["open", "high", "low", "close", "volume"]
    df[cols] = df[cols].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"])

    processed_dfs = []
    
    # Process each coin group independently to maintain sequence isolation
    for sym, g in df.groupby("symbol"):
        print(f"Calculating indicators for {sym} ...", flush=True)
        g_sorted = g.set_index("open_time").sort_index()
        out = add_all_indicators(g_sorted).reset_index()
        processed_dfs.append(out)
        print(f"  -> Added {len(out):,} rows", flush=True)

    if not processed_dfs:
        print("No data available to process.")
        return

    print("\nConcatenating all processed coin dataframes...", flush=True)
    full_df = pd.concat(processed_dfs, ignore_index=True)
    
    # Sort for overall structural neatness
    full_df = full_df.sort_values(["symbol", "open_time"]).reset_index(drop=True)

    # --- Partitioning script begins here ---
    split_dir = OUT / "full_with_indicators_parts"
    split_dir.mkdir(exist_ok=True)

    print(f"\nPartitioning dataset into 8 equal parts inside {split_dir}...", flush=True)
    
    num_parts = 8
    chunk_size = int(np.ceil(len(full_df) / num_parts))
    total_written_bytes = 0

    for i in range(num_parts):
        start_idx = i * chunk_size
        end_idx = min((i + 1) * chunk_size, len(full_df))
        chunk = full_df.iloc[start_idx:end_idx]
        
        part_path = split_dir / f"part_{i+1:02d}.parquet"
        chunk.to_parquet(part_path, index=False)
        
        file_size_mb = part_path.stat().st_size / (1024 * 1024)
        total_written_bytes += part_path.stat().st_size
        print(f"  -> Saved {part_path.name} ({len(chunk):,} rows, {file_size_mb:.2f} MB)", flush=True)

    print(f"\nDone. Successfully split dataset into {num_parts} files.")
    print(f"Total Rows: {len(full_df):,}")
    print(f"Total Directory Size: {total_written_bytes / (1024 * 1024):.2f} MB")


if __name__ == "__main__":
    main()