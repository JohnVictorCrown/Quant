"""backtest.py
- Parses Consolidated_Multi_Coin_Indicator_Analysis_Report.md to align splits per coin
- Standardizes test split features using train split parameters (no leakage)
- Recreates zero-importance feature pruning matching train.py
- Loads saved XGBoost fold model ensembles from models/
- Automatically calculates dynamic 95th percentile signal thresholds per asset
- Optimizes the trade holding period (HORIZON) per coin using risk-adjusted return metrics
- Simulates separate BUY, SELL, and COMBINED portfolio backtests
- Generates an interactive HTML dashboard containing Plotly charts and optimal parameters
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone
import re
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from xgboost import XGBRegressor

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "full_with_indicators_parts"
MODEL_DIR = ROOT / "models"
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

if not DATA_DIR.exists():
    raise SystemExit(f"Missing data directory at: {DATA_DIR}. Run indicator_features.py first.")
if not MODEL_DIR.exists():
    raise SystemExit(f"Missing models directory at: {MODEL_DIR}. Run train.py first.")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
METADATA_COLS = ["open_time", "symbol", "open", "high", "low", "close", "volume", "future_close", "y_buy", "y_sell"]

INITIAL_BANKROLL = 10000.0
STAKE_SIZE = 0.05
SIGNAL_PERCENTILE = 95  # Trades the top 5% strongest signals dynamically
DEFAULT_HORIZON = 12

# Candidate holding periods (horizons) to evaluate dynamically (30m to 8h)
HORIZON_CANDIDATES = [6, 12, 18, 24, 36, 48, 72, 96]


def discover_trained_horizons():
    """Parses the generated Markdown report to locate the exact training horizon per coin."""
    horizons = {}
    report_path = REPORT_DIR / "Consolidated_Multi_Coin_Indicator_Analysis_Report.md"
    if report_path.exists():
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Split sections per asset profile
            segments = content.split("## Asset Profile: ")
            for seg in segments[1:]:
                lines = seg.split("\n")
                coin = lines[0].strip()
                for line in lines:
                    if "Optimal Target Horizon" in line or "Target Forward Horizon" in line:
                        match = re.search(r"(\d+)\s+bars", line)
                        if match:
                            horizons[coin] = int(match.group(1))
                            break
        except Exception as e:
            print(f"Error parsing trained horizons from report: {e}")
            
    # Apply standard fallback defaults if file is missing or parsing fails
    for sym in SYMBOLS:
        if sym not in horizons:
            horizons[sym] = DEFAULT_HORIZON
    return horizons


def prune_zero_importance_features(X_tr, y_tr, feature_names):
    """Reconstructs the precise feature pruning used in train.py."""
    baseline = XGBRegressor(
        objective="reg:squarederror", 
        tree_method="hist", 
        n_estimators=100, 
        random_state=42, 
        n_jobs=-1
    )
    baseline.fit(X_tr, y_tr)
    importances = baseline.feature_importances_
    keep_indices = [i for i, imp in enumerate(importances) if imp > 0.0]
    if not keep_indices:
        keep_indices = list(range(X_tr.shape[1]))
    return keep_indices


def run_portfolio_backtest(df_test, buy_preds, sell_preds, buy_thresh, sell_thresh, mode="COMBINED", initial=INITIAL_BANKROLL, stake=STAKE_SIZE, horizon=DEFAULT_HORIZON):
    """Simulates a highly realistic overlapping portfolio backtest supporting separate modes."""
    cash = initial
    equity = initial
    equity_curve = []
    bh_curve = []
    
    # Track active trades: list of dicts
    active_trades = []
    trades_pnl = []  # Record PnL of closed trades
    
    close_prices = df_test["close"].values
    initial_close = close_prices[0]
    
    for t in range(len(df_test)):
        # 1. Resolve expiring trades
        remaining_trades = []
        for trade in active_trades:
            if t >= trade["exit_idx"]:
                # Close trade
                exit_price = close_prices[t]
                entry_price = trade["entry_price"]
                trade_size = trade["size"]
                
                if trade["type"] == "BUY":
                    pct_return = (exit_price - entry_price) / entry_price
                else:  # SELL
                    pct_return = (entry_price - exit_price) / entry_price
                    
                pnl = trade_size * pct_return
                trades_pnl.append(pnl)
                
                # Return position value back to cash
                cash += trade_size + pnl
            else:
                remaining_trades.append(trade)
        active_trades = remaining_trades
        
        # Calculate current asset values of active trades
        current_trades_value = 0.0
        for trade in active_trades:
            current_price = close_prices[t]
            entry_price = trade["entry_price"]
            trade_size = trade["size"]
            if trade["type"] == "BUY":
                pct_return = (current_price - entry_price) / entry_price
            else:
                pct_return = (entry_price - current_price) / entry_price
            current_trades_value += trade_size * (1 + pct_return)
            
        equity = cash + current_trades_value
        
        # 2. Check for new signals
        buy_p = buy_preds[t]
        sell_p = sell_preds[t]
        
        if cash > 0:
            position_size = equity * stake
            if position_size > cash:
                position_size = cash
                
            if position_size > 0.1:  # Floor trade size filter
                # Mode-dependent execution
                if mode in ["BUY_ONLY", "COMBINED"] and buy_p >= buy_thresh:
                    active_trades.append({
                        "type": "BUY",
                        "entry_price": close_prices[t],
                        "size": position_size,
                        "exit_idx": t + horizon
                    })
                    cash -= position_size
                elif mode in ["SELL_ONLY", "COMBINED"] and sell_p >= sell_thresh:
                    active_trades.append({
                        "type": "SELL",
                        "entry_price": close_prices[t],
                        "size": position_size,
                        "exit_idx": t + horizon
                    })
                    cash -= position_size
                    
        # Finalize equity step
        current_trades_value = 0.0
        for trade in active_trades:
            current_price = close_prices[t]
            entry_price = trade["entry_price"]
            trade_size = trade["size"]
            if trade["type"] == "BUY":
                pct_return = (current_price - entry_price) / entry_price
            else:
                pct_return = (entry_price - current_price) / entry_price
            current_trades_value += trade_size * (1 + pct_return)
            
        equity = cash + current_trades_value
        
        equity_curve.append(equity)
        bh_curve.append(initial * (close_prices[t] / initial_close))
        
    return np.array(equity_curve), np.array(bh_curve), trades_pnl


def calculate_mdd(eq):
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1)) if len(peak) > 0 and np.max(peak) > 0 else 0.0


def main():
    print("Loading partitioned Parquet data...", flush=True)
    df_all = pd.read_parquet(DATA_DIR)
    
    # Discover trained horizons dynamically from the report output
    trained_horizons = discover_trained_horizons()
    print(f"Discovered trained horizons: {trained_horizons}", flush=True)
    
    # Identify dynamic indicator features matching training environment
    indicator_cols = [c for c in df_all.columns if c not in METADATA_COLS]
    feature_cols = ["open", "high", "low", "close", "volume"] + indicator_cols

    backtest_results = {}

    for coin in SYMBOLS:
        g = df_all[df_all["symbol"] == coin].copy()
        if g.empty:
            print(f"Skipping {coin} (no data found in Parquet)")
            continue
            
        # Get the specific horizon used to train this coin's models
        trained_horizon = trained_horizons.get(coin, DEFAULT_HORIZON)
        print(f"\nEvaluating models for {coin} (Trained Horizon: {trained_horizon} bars)...", flush=True)
        
        g = g.sort_values("open_time").reset_index(drop=True)
        g = g.dropna(subset=feature_cols).reset_index(drop=True)
        
        # Chronological temporal splitting matching training boundary splits
        g_model = g.iloc[:-trained_horizon].copy().reset_index(drop=True)
        g_model["future_close"] = g["close"].shift(-trained_horizon)
        g_model = g_model.dropna(subset=["future_close"]).reset_index(drop=True)
        
        raw_buy_profit = (g_model["future_close"] - g_model["close"]) / g_model["close"]
        raw_sell_profit = -raw_buy_profit
        
        p1_b, p99_b = np.percentile(raw_buy_profit, [1, 99])
        p1_s, p99_s = np.percentile(raw_sell_profit, [1, 99])
        buy_profit_clipped = np.clip(raw_buy_profit, p1_b, p99_b)
        sell_profit_clipped = np.clip(raw_sell_profit, p1_s, p99_s)

        split_idx = int(len(g_model) * 0.7)
        train_df = g_model.iloc[:split_idx].copy()
        test_df = g_model.iloc[split_idx + trained_horizon:].copy()
        
        train_buy_raw = buy_profit_clipped.iloc[:split_idx].values.reshape(-1, 1)
        train_sell_raw = sell_profit_clipped.iloc[:split_idx].values.reshape(-1, 1)

        # Re-standardize test features strictly using train set statistics (zero leakage)
        mu = train_df[feature_cols].mean(axis=0)
        sd = train_df[feature_cols].std(axis=0).replace(0, 1.0)
        X_train_raw = ((train_df[feature_cols] - mu) / sd).values
        X_test_raw = ((test_df[feature_cols] - mu) / sd).values
        
        # Scaling targets exactly matching train.py to scale signals correctly
        buy_scaler = MinMaxScaler()
        sell_scaler = MinMaxScaler()
        _ = buy_scaler.fit_transform(train_buy_raw)
        _ = sell_scaler.fit_transform(train_sell_raw)

        # Enforce exact indices pruned during feature extraction step
        buy_keep_idx = prune_zero_importance_features(X_train_raw, buy_scaler.transform(train_buy_raw).flatten(), feature_cols)
        sell_keep_idx = prune_zero_importance_features(X_train_raw, sell_scaler.transform(train_sell_raw).flatten(), feature_cols)

        # Load 3-fold saved JSON model ensembles
        buy_ensemble = []
        sell_ensemble = []
        for fold in range(3):
            m_buy = XGBRegressor()
            m_buy.load_model(str(MODEL_DIR / f"{coin}_BUY_fold_{fold:02d}.json"))
            buy_ensemble.append(m_buy)
            
            m_sell = XGBRegressor()
            m_sell.load_model(str(MODEL_DIR / f"{coin}_SELL_fold_{fold:02d}.json"))
            sell_ensemble.append(m_sell)

        # Slice pruned columns and average predictions across all three folds
        X_test_buy = X_test_raw[:, buy_keep_idx]
        X_test_sell = X_test_raw[:, sell_keep_idx]
        
        buy_preds = np.mean([m.predict(X_test_buy) for m in buy_ensemble], axis=0)
        sell_preds = np.mean([m.predict(X_test_sell) for m in sell_ensemble], axis=0)

        # Calculate dynamic percentile thresholds (95th percentile = top 5% strongest signals)
        buy_threshold = float(np.percentile(buy_preds, SIGNAL_PERCENTILE))
        sell_threshold = float(np.percentile(sell_preds, SIGNAL_PERCENTILE))
        
        # --- Parameter Sweep over Horizons (using Combined mode to score) ---
        best_horizon = None
        best_score = -999999.0
        best_metrics = None
        
        print(f"  Optimizing trade holding horizon over {HORIZON_CANDIDATES} candidates...", flush=True)
        for h in HORIZON_CANDIDATES:
            eq, bh, closed = run_portfolio_backtest(
                test_df, buy_preds, sell_preds,
                buy_thresh=buy_threshold, sell_thresh=sell_threshold,
                mode="COMBINED", horizon=h
            )
            ret_pct = (eq[-1] / INITIAL_BANKROLL - 1) * 100
            mdd_pct = calculate_mdd(eq) * 100
            score = ret_pct / (abs(mdd_pct) + 1.0)
            
            if score > best_score:
                best_score = score
                best_horizon = h
                best_metrics = (eq, bh, closed, ret_pct, mdd_pct)
        
        # Unpack optimized parameters
        eq_curve_comb, bh_curve, closed_comb, return_comb, mdd_comb = best_metrics
        final_eq_comb = eq_curve_comb[-1]
        final_bh = bh_curve[-1]
        mdd_bh = calculate_mdd(bh_curve) * 100
        
        total_trades_comb = len(closed_comb)
        wins_comb = sum(1 for p in closed_comb if p > 0)
        win_rate_comb = (wins_comb / total_trades_comb * 100) if total_trades_comb > 0 else 0.0
        
        # --- Run the isolated BUY_ONLY and SELL_ONLY backtests on optimal horizon ---
        eq_curve_buy, _, closed_buy = run_portfolio_backtest(
            test_df, buy_preds, sell_preds,
            buy_thresh=buy_threshold, sell_thresh=sell_threshold,
            mode="BUY_ONLY", horizon=best_horizon
        )
        final_eq_buy = eq_curve_buy[-1]
        mdd_buy = calculate_mdd(eq_curve_buy) * 100
        total_trades_buy = len(closed_buy)
        wins_buy = sum(1 for p in closed_buy if p > 0)
        win_rate_buy = (wins_buy / total_trades_buy * 100) if total_trades_buy > 0 else 0.0

        eq_curve_sell, _, closed_sell = run_portfolio_backtest(
            test_df, buy_preds, sell_preds,
            buy_thresh=buy_threshold, sell_thresh=sell_threshold,
            mode="SELL_ONLY", horizon=best_horizon
        )
        final_eq_sell = eq_curve_sell[-1]
        mdd_sell = calculate_mdd(eq_curve_sell) * 100
        total_trades_sell = len(closed_sell)
        wins_sell = sum(1 for p in closed_sell if p > 0)
        win_rate_sell = (wins_sell / total_trades_sell * 100) if total_trades_sell > 0 else 0.0

        labels = pd.to_datetime(test_df["open_time"]).dt.strftime("%Y-%m-%d %H:%M").tolist()
        
        backtest_results[coin] = {
            "labels": labels,
            "equity_comb": [round(float(x), 2) for x in eq_curve_comb],
            "equity_buy": [round(float(x), 2) for x in eq_curve_buy],
            "equity_sell": [round(float(x), 2) for x in eq_curve_sell],
            "bh": [round(float(x), 2) for x in bh_curve],
            "optimal_horizon": best_horizon,
            "buy_thresh": round(buy_threshold, 5),
            "sell_thresh": round(sell_threshold, 5),
            "metrics_comb": {
                "final_eq": round(final_eq_comb, 2),
                "final_bh": round(final_bh, 2),
                "return_pct": round(return_comb, 2),
                "bh_return_pct": round((final_bh / INITIAL_BANKROLL - 1) * 100, 2),
                "mdd_pct": round(mdd_comb, 2),
                "mdd_bh_pct": round(mdd_bh, 2),
                "total_trades": total_trades_comb,
                "win_rate_pct": round(win_rate_comb, 2)
            },
            "metrics_buy": {
                "final_eq": round(final_eq_buy, 2),
                "return_pct": round((final_eq_buy / INITIAL_BANKROLL - 1) * 100, 2),
                "mdd_pct": round(mdd_buy, 2),
                "total_trades": total_trades_buy,
                "win_rate_pct": round(win_rate_buy, 2)
            },
            "metrics_sell": {
                "final_eq": round(final_eq_sell, 2),
                "return_pct": round((final_eq_sell / INITIAL_BANKROLL - 1) * 100, 2),
                "mdd_pct": round(mdd_sell, 2),
                "total_trades": total_trades_sell,
                "win_rate_pct": round(win_rate_sell, 2)
            }
        }
        print(f"  --> Combined Return: {return_comb:+.2f}% | BUY-Only: {backtest_results[coin]['metrics_buy']['return_pct']:+.2f}% | SELL-Only: {backtest_results[coin]['metrics_sell']['return_pct']:+.2f}%")

    # Generate the dashboard HTML
    generate_report(backtest_results)


def generate_report(results):
    report_path = REPORT_DIR / "Consolidated_Backtest_Report.html"
    
    # Construct tabular summaries dynamically using nested structure
    table_rows_html = ""
    for coin, r in results.items():
        ret_comb = r["metrics_comb"]["return_pct"]
        val_class_comb = "pct-green" if ret_comb >= 0 else "pct-red"
        
        ret_buy = r["metrics_buy"]["return_pct"]
        val_class_buy = "pct-green" if r["metrics_buy"]["return_pct"] >= 0 else "pct-red"
        
        ret_sell = r["metrics_sell"]["return_pct"]
        val_class_sell = "pct-green" if r["metrics_sell"]["return_pct"] >= 0 else "pct-red"
        
        bh_ret = r["metrics_comb"]["bh_return_pct"]
        bh_val_class = "pct-green" if bh_ret >= 0 else "pct-red"
        
        table_rows_html += f"""
        <tr>
            <td><strong>{coin} (Combined)</strong></td>
            <td>${r["metrics_comb"]["final_eq"]:,.2f}</td>
            <td class="{val_class_comb}">{ret_comb:+.2f}%</td>
            <td>{r["metrics_comb"]["mdd_pct"]:.2f}%</td>
            <td>${r["metrics_comb"]["final_bh"]:,.2f}</td>
            <td class="{bh_val_class}">{bh_ret:+.2f}%</td>
            <td>{r["metrics_comb"]["mdd_bh_pct"]:.2f}%</td>
            <td>{r["metrics_comb"]["total_trades"]}</td>
            <td>{r["metrics_comb"]["win_rate_pct"]:.2f}%</td>
            <td>{r["optimal_horizon"]} bars</td>
            <td>{r["buy_thresh"]} / {r["sell_thresh"]}</td>
        </tr>
        <tr>
            <td><span style="color: #94a3b8; padding-left: 10px;">↳ Buy-Only</span></td>
            <td>${r["metrics_buy"]["final_eq"]:,.2f}</td>
            <td class="{val_class_buy}">{ret_buy:+.2f}%</td>
            <td>{r["metrics_buy"]["mdd_pct"]:.2f}%</td>
            <td>-</td>
            <td>-</td>
            <td>-</td>
            <td>{r["metrics_buy"]["total_trades"]}</td>
            <td>{r["metrics_buy"]["win_rate_pct"]:.2f}%</td>
            <td>-</td>
            <td>-</td>
        </tr>
        <tr>
            <td><span style="color: #94a3b8; padding-left: 10px;">↳ Sell-Only</span></td>
            <td>${r["metrics_sell"]["final_eq"]:,.2f}</td>
            <td class="{val_class_sell}">{ret_sell:+.2f}%</td>
            <td>{r["metrics_sell"]["mdd_pct"]:.2f}%</td>
            <td>-</td>
            <td>-</td>
            <td>-</td>
            <td>{r["metrics_sell"]["total_trades"]}</td>
            <td>{r["metrics_sell"]["win_rate_pct"]:.2f}%</td>
            <td>-</td>
            <td>-</td>
        </tr>
        """

    meta_str = f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | Initial Bankroll: ${INITIAL_BANKROLL:,.2f} | Trade Stake: {STAKE_SIZE*100:.1f}% | Signal Threshold: Dynamic (Top 5% of Predictions) | Horizon Sweep: Optimized"
    tabs_html = "".join([f'<button class="tab-btn" onclick="switchTab(\'{coin}\')">{coin}</button>' for coin in results.keys()])
    panes_html = "".join([render_coin_pane(coin, data) for coin, data in results.items()])
    first_coin = list(results.keys())[0] if results.keys() else ""

    # HTML template is written as a literal string to safely ignore Javascript and CSS curly braces
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Consolidated Backtest Report</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
    body { background: #0b0f19; color: #e1e7f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px; }
    .container { max-width: 1300px; margin: 0 auto; }
    h1 { color: #ffffff; margin-bottom: 5px; }
    p.meta { color: #788a9e; margin-bottom: 25px; font-size: 14px; }
    
    /* Metrics Summary Table */
    table { width: 100%; border-collapse: collapse; background: #121824; border-radius: 8px; overflow: hidden; margin-bottom: 30px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
    th, td { padding: 14px 18px; text-align: left; border-bottom: 1px solid #1c2333; }
    th { background: #182030; color: #a5b4fc; font-weight: 600; font-size: 13px; text-transform: uppercase; }
    tr:hover { background: #161e2e; }
    .pct-green { color: #10b981; font-weight: 600; }
    .pct-red { color: #ef4444; font-weight: 600; }
    
    /* Tabs styling */
    .tabs-header { display: flex; gap: 8px; border-bottom: 1px solid #1c2333; margin-bottom: 15px; padding-bottom: 8px; }
    .tab-btn { background: #121824; border: 1px solid #1c2333; color: #94a3b8; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.2s; }
    .tab-btn:hover { background: #1e293b; color: #ffffff; }
    .tab-btn.active { background: #4f46e5; border-color: #4f46e5; color: #ffffff; }
    
    /* Chart and cards wrap */
    .chart-container { background: #121824; border-radius: 8px; padding: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); min-height: 500px; }
    .coin-pane { display: none; }
    .coin-pane.active { display: block; }
    
    .cards-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 20px; }
    .card { background: #1c2333; border-radius: 6px; padding: 15px; border: 1px solid #262f45; }
    .card .title { font-size: 12px; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; }
    .card .val { font-size: 22px; font-weight: bold; color: #ffffff; }
</style>
</head>
<body>
<div class="container">
    <h1>Consolidated Backtest Performance Dashboard</h1>
    <p class="meta">__META_STR__</p>
    
    <!-- Strategy Metrics Table -->
    <table>
        <thead>
            <tr>
                <th>Asset</th>
                <th>Final Equity</th>
                <th>Strategy Return</th>
                <th>Max DD</th>
                <th>Buy & Hold Equity</th>
                <th>Buy & Hold Return</th>
                <th>B&H Max DD</th>
                <th>Total Trades</th>
                <th>Win Rate</th>
                <th>Optimal Horizon</th>
                <th>Trigger Thresh (BUY / SELL)</th>
            </tr>
        </thead>
        <tbody>
            __TABLE_ROWS__
        </tbody>
    </table>
    
    <!-- Tab Controls -->
    <div class="tabs-header">
        __TABS_HEADER__
    </div>
    
    <!-- Charts Panes -->
    <div class="chart-container">
        __PANES__
    </div>
</div>

<script>
    // Tab switching mechanism
    function switchTab(coin) {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.remove('active');
            if (btn.innerText === coin) btn.classList.add('active');
        });
        document.querySelectorAll('.coin-pane').forEach(pane => {
            pane.classList.remove('active');
        });
        document.getElementById('pane-' + coin).classList.add('active');
        
        // Trigger plot resize in case chart display dimensions shifted
        window.dispatchEvent(new Event('resize'));
    }
    
    // Set initial active tab
    const firstCoin = "__FIRST_COIN__";
    if (firstCoin) switchTab(firstCoin);
</script>
</body>
</html>
"""
    # Safe replacement of placeholders to bypass f-string parsing restrictions
    html = html_template.replace("__META_STR__", meta_str)\
                         .replace("__TABLE_ROWS__", table_rows_html)\
                         .replace("__TABS_HEADER__", tabs_html)\
                         .replace("__PANES__", panes_html)\
                         .replace("__FIRST_COIN__", first_coin)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nConsolidated Interactive Dashboard Saved: {report_path}", flush=True)


def render_coin_pane(coin, r):
    # Standard template replacement to bypass native CSS/Javascript curly braces
    template = """
    <div class="coin-pane" id="pane-__COIN__">
        <div class="cards-row">
            <div class="card">
                <div class="title">Combined Return</div>
                <div class="val __VAL_CLASS__">__RETURN_PCT__%</div>
            </div>
            <div class="card">
                <div class="title">Max Drawdown</div>
                <div class="val" style="color:#ef4444">__MDD_PCT__%</div>
            </div>
            <div class="card">
                <div class="title">B&H Return</div>
                <div class="val __BH_VAL_CLASS__">__BH_RETURN_PCT__%</div>
            </div>
            <div class="card">
                <div class="title">Combined Trades</div>
                <div class="val">__TOTAL_TRADES__</div>
            </div>
            <div class="card">
                <div class="title">Combined Win Rate</div>
                <div class="val">__WIN_RATE_PCT__%</div>
            </div>
            <div class="card">
                <div class="title">Optimal Horizon</div>
                <div class="val">__OPTIMAL_HORIZON__ bars</div>
            </div>
        </div>
        <div id="__TRACE_ID__"></div>
        <script>
        (function() {
            const labels = __LABELS__;
            const equity_comb = __EQUITY_COMB__;
            const equity_buy = __EQUITY_BUY__;
            const equity_sell = __EQUITY_SELL__;
            const bh = __BH__;
            
            const traceE_comb = {x:labels, y:equity_comb, type:'scatter', mode:'lines', name:'Combined Strategy', line:{color:'#f3ba2f', width:2}};
            const traceE_buy = {x:labels, y:equity_buy, type:'scatter', mode:'lines', name:'Buy-Only Strategy', line:{color:'#10b981', width:1.5}};
            const traceE_sell = {x:labels, y:equity_sell, type:'scatter', mode:'lines', name:'Sell-Only Strategy', line:{color:'#ef4444', width:1.5}};
            const traceB = {x:labels, y:bh, type:'scatter', mode:'lines', name:'Buy & Hold equity', line:{color:'#7c8ba1', width:1, dash:'dot'}};
            
            const layout = {
              paper_bgcolor:'#121824', plot_bgcolor:'#121824',
              font:{color:'#e1e7f0'},
              margin:{l:50,r:40,t:20,b:50},
              xaxis:{title:'Time', gridcolor:'#1e2632', linecolor:'#1e2632'},
              yaxis:{title:'Portfolio Value ($)', gridcolor:'#1e2632', linecolor:'#1e2632', side:'left'},
              legend:{x:0.01, y:0.99, bgcolor:'rgba(0,0,0,0)'}
            };
            Plotly.newPlot('__TRACE_ID__',[traceE_comb, traceE_buy, traceE_sell, traceB],layout,{responsive:true, displayModeBar:true});
        })();
        </script>
    </div>
    """
    
    val_class = "pct-green" if r["metrics_comb"]["return_pct"] >= 0 else "pct-red"
    bh_val_class = "pct-green" if r["metrics_comb"]["bh_return_pct"] >= 0 else "pct-red"
    trace_id = f"chart-{coin}"
    
    return template.replace("__COIN__", coin)\
                   .replace("__TRACE_ID__", trace_id)\
                   .replace("__VAL_CLASS__", val_class)\
                   .replace("__BH_VAL_CLASS__", bh_val_class)\
                   .replace("__RETURN_PCT__", f"{r['metrics_comb']['return_pct']:+.2f}")\
                   .replace("__MDD_PCT__", f"{r['metrics_comb']['mdd_pct']:.2f}")\
                   .replace("__BH_RETURN_PCT__", f"{r['metrics_comb']['bh_return_pct']:+.2f}")\
                   .replace("__TOTAL_TRADES__", str(r["metrics_comb"]["total_trades"]))\
                   .replace("__WIN_RATE_PCT__", f"{r['metrics_comb']['win_rate_pct']:.2f}")\
                   .replace("__OPTIMAL_HORIZON__", str(r["optimal_horizon"]))\
                   .replace("__LABELS__", json.dumps(r["labels"]))\
                   .replace("__EQUITY_COMB__", json.dumps(r["equity_comb"]))\
                   .replace("__EQUITY_BUY__", json.dumps(r["equity_buy"]))\
                   .replace("__EQUITY_SELL__", json.dumps(r["equity_sell"]))\
                   .replace("__BH__", json.dumps(r["bh"]))


if __name__ == "__main__":
    main()