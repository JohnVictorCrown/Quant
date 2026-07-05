"""train.py
Multi-Coin Machine Learning & Statistical Profiling Pipeline:
- Loads multi-part Parquet dataset from data/full_with_indicators_parts/
- Dynamically screens and optimizes the best forecast horizon (HORIZON) per asset
- Prunes zero-importance noisy features per asset using baseline models
- Runs Bayesian Hyperparameter Optimization (Optuna TPE) per asset-target on the best horizon
- Trains and saves an ensemble of cross-validation fold models using early stopping
- Enforces strict target and feature scaling boundaries (no lookahead leakage)
- Inverse-transforms ensembled predictions to return space for realistic MAE reporting
- Generates a world-class statistical data report profiling indicators per coin

Dependencies Installation:
    pip install pandas numpy scikit-learn xgboost pyarrow optuna
    (Note: 'scikit-learn' is used for installation, while 'sklearn' is used for code imports)
"""

from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import optuna

# Scikit-learn modules
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV

from xgboost import XGBRegressor

# Silence Optuna verbose output to keep training logs clean
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Directories Setup
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "full_with_indicators_parts"
REPORT_DIR = ROOT / "reports"
MODEL_DIR = ROOT / "models"

for d in [REPORT_DIR, MODEL_DIR]:
    d.mkdir(exist_ok=True)

if not DATA_DIR.exists():
    raise SystemExit(f"Missing data directory at: {DATA_DIR}. Run indicator_features.py first.")

# Load full multi-part Parquet dataset
print("Loading multi-part Parquet dataset...", flush=True)
df_all = pd.read_parquet(DATA_DIR)

# Exclude metadata and identify all indicator feature columns dynamically
METADATA_COLS = ["open_time", "symbol", "open", "high", "low", "close", "volume", "future_close", "y_buy", "y_sell"]
INDICATOR_COLS = [c for c in df_all.columns if c not in METADATA_COLS]
FEATURE_COLS = ["open", "high", "low", "close", "volume"] + INDICATOR_COLS

# standard target horizons to optimize over (representing 30m to ~87h)
HORIZON_CANDIDATES = [6, 12, 24, 48, 96, 192, 384, 722, 1044]

# Regularized search space optimized for low Signal-to-Noise Ratio (SNR) environments
PARAM_GRID = {
    "max_depth": [3, 5],               # Shallower trees generalize better on noisy markets
    "learning_rate": [0.03, 0.05],
    "n_estimators": [150, 250],
    "reg_lambda": [1.0, 10.0],         # L2 Regularization
    "reg_alpha": [0.0, 1.0]            # L1 Regularization
}


def prune_zero_importance_features(X_tr, y_tr, feature_names):
    """Identifies and removes features with exactly zero predictive value."""
    baseline = XGBRegressor(
        objective="reg:squarederror", 
        tree_method="hist", 
        n_estimators=100, 
        random_state=42, 
        n_jobs=-1
    )
    baseline.fit(X_tr, y_tr)
    importances = baseline.feature_importances_
    
    # Keep indices where feature importance > 0
    keep_indices = [i for i, imp in enumerate(importances) if imp > 0.0]
    
    # Fallback to keep all features if zero-importance pruning somehow drops everything
    if not keep_indices:
        keep_indices = list(range(X_tr.shape[1]))
        
    pruned_features = [feature_names[i] for i in keep_indices]
    return keep_indices, pruned_features


def optimize_hyperparameters(X, y, tscv, target_name):
    """Runs Bayesian Optimization (TPE) to find regularized hyperparameters."""
    def objective(trial):
        params = {
            "objective": "reg:squarederror",
            "tree_method": "hist",
            "random_state": 42,
            "n_jobs": -1,
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
            "max_depth": trial.suggest_int("max_depth", 3, 6),
            "min_child_weight": trial.suggest_int("min_child_weight", 1, 20),
            "subsample": trial.suggest_float("subsample", 0.6, 0.9),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 0.9),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-2, 10.0, log=True),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-2, 10.0, log=True),
            "gamma": trial.suggest_float("gamma", 0.0, 5.0)
        }
        
        scores = []
        for train_idx, val_idx in tscv.split(X):
            X_train_f, y_train_f = X[train_idx], y[train_idx]
            X_val_f, y_val_f = X[val_idx], y[val_idx]
            
            # Enforce early stopping during CV to find the exact tree depth limit
            model = XGBRegressor(**params, n_estimators=1000, early_stopping_rounds=30)
            model.fit(
                X_train_f, y_train_f,
                eval_set=[(X_val_f, y_val_f)],
                verbose=False
            )
            
            preds = model.predict(X_val_f)
            scores.append(mean_absolute_error(y_val_f, preds))
            
        return np.mean(scores)

    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=15)
    return study.best_params


# Centralized results tracking for the master Markdown report
master_report_data = []

# Process each coin group independently
grouped = df_all.groupby("symbol")
for coin, g in grouped:
    print(f"\n=================== Profiling & Modeling {coin} ===================", flush=True)
    
    # Sort chronologically
    g = g.sort_values("open_time").reset_index(drop=True)
    
    # Filter rows that do not contain valid features
    g = g.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    
    # --- 1. Dynamic Horizon Screening (Optimizing the target holding period) ---
    best_horizon = None
    best_r2 = -999999.0
    
    print(f"  Screening optimal target horizon over candidates...", flush=True)
    for h in HORIZON_CANDIDATES:
        if len(g) <= (h * 3):
            continue
            
        # Safe target construction
        g_temp = g.iloc[:-h].copy().reset_index(drop=True)
        g_temp["future_close"] = g["close"].shift(-h)
        g_temp = g_temp.dropna(subset=["future_close"]).reset_index(drop=True)
        
        raw_profit = (g_temp["future_close"] - g_temp["close"]) / g_temp["close"]
        
        p1, p99 = np.percentile(raw_profit, [1, 99])
        profit_clipped = np.clip(raw_profit, p1, p99)
        
        # Split chronologically with purged gap
        split_i = int(len(g_temp) * 0.7)
        train_df_temp = g_temp.iloc[:split_i].copy()
        test_df_temp = g_temp.iloc[split_i + h:].copy()
        
        train_raw = profit_clipped.iloc[:split_i].values.reshape(-1, 1)
        test_raw = profit_clipped.iloc[split_i + h:].values.reshape(-1, 1)
        
        # Scaling target
        scaler_temp = MinMaxScaler()
        y_train_temp = scaler_temp.fit_transform(train_raw).flatten()
        y_test_temp = scaler_temp.transform(test_raw).flatten()
        
        # Feature standardization
        mu_temp = train_df_temp[FEATURE_COLS].mean(axis=0)
        sd_temp = train_df_temp[FEATURE_COLS].std(axis=0).replace(0, 1.0)
        X_train_temp = ((train_df_temp[FEATURE_COLS] - mu_temp) / sd_temp).values
        X_test_temp = ((test_df_temp[FEATURE_COLS] - mu_temp) / sd_temp).values
        
        # Train baseline model to score the predictability of this horizon
        fast_model = XGBRegressor(
            objective="reg:squarederror",
            tree_method="hist",
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1
        )
        fast_model.fit(X_train_temp, y_train_temp)
        preds_temp = fast_model.predict(X_test_temp)
        r2_temp = r2_score(y_test_temp, preds_temp)
        
        print(f"    Horizon {h:4d} bars: OOS R2 = {r2_temp:+.6f}", flush=True)
        
        if r2_temp > best_r2:
            best_r2 = r2_temp
            best_horizon = h
            
    if best_horizon is None:
        print(f"Skipping {coin} due to insufficient row count across all horizons.", flush=True)
        continue
        
    print(f"  --> Selected Optimal Horizon for {coin}: {best_horizon} bars (Validation R2: {best_r2:+.6f})", flush=True)
    HORIZON = best_horizon

    # Construct final targets with the optimized target horizon
    g_model = g.iloc[:-HORIZON].copy().reset_index(drop=True)
    g_model["future_close"] = g["close"].shift(-HORIZON)
    g_model = g_model.dropna(subset=["future_close"]).reset_index(drop=True)
    
    raw_buy_profit = (g_model["future_close"] - g_model["close"]) / g_model["close"]
    raw_sell_profit = -raw_buy_profit

    # --- 2. Statistical Profiling ---
    print(f"  Profiling indicator statistics...", flush=True)
    stats_records = []
    for col in FEATURE_COLS:
        series = g_model[col]
        stats_records.append({
            "feature": col,
            "mean": float(series.mean()),
            "std": float(series.std()),
            "skew": float(series.skew()),
            "kurtosis": float(series.kurt()),  # Excess Kurtosis
            "acf_1": float(series.autocorr(lag=1)),  # Persistence (Lag-1 ACF)
            "ic_proxy": float(series.corr(raw_buy_profit))  # Information Coefficient Proxy
        })
    df_stats = pd.DataFrame(stats_records)

    # Outlier reduction to limit target extreme variance noise (1st/99th percentiles)
    p1_b, p99_b = np.percentile(raw_buy_profit, [1, 99])
    p1_s, p99_s = np.percentile(raw_sell_profit, [1, 99])
    buy_profit_clipped = np.clip(raw_buy_profit, p1_b, p99_b)
    sell_profit_clipped = np.clip(raw_sell_profit, p1_s, p99_s)

    # Chronological temporal splitting with a purged gap to eliminate overlap leakage
    split_idx = int(len(g_model) * 0.7)
    train_df = g_model.iloc[:split_idx].copy()
    test_df = g_model.iloc[split_idx + HORIZON:].copy()  # Purged gap of size HORIZON

    train_buy_raw = buy_profit_clipped.iloc[:split_idx].values.reshape(-1, 1)
    test_buy_raw = buy_profit_clipped.iloc[split_idx + HORIZON:].values.reshape(-1, 1)
    
    train_sell_raw = sell_profit_clipped.iloc[:split_idx].values.reshape(-1, 1)
    test_sell_raw = sell_profit_clipped.iloc[split_idx + HORIZON:].values.reshape(-1, 1)

    # Scaling targets: fits scaler strictly on train set values to avoid target leakage
    buy_scaler = MinMaxScaler()
    sell_scaler = MinMaxScaler()
    
    train_df["y_buy"] = buy_scaler.fit_transform(train_buy_raw).flatten()
    test_df["y_buy"] = buy_scaler.transform(test_buy_raw).flatten()
    
    train_df["y_sell"] = sell_scaler.fit_transform(train_sell_raw).flatten()
    test_df["y_sell"] = sell_scaler.transform(test_sell_raw).flatten()

    # Standardize features strictly using train set statistics (zero mean, unit variance)
    mu = train_df[FEATURE_COLS].mean(axis=0)
    sd = train_df[FEATURE_COLS].std(axis=0).replace(0, 1.0)
    
    X_train_raw = ((train_df[FEATURE_COLS] - mu) / sd).values
    X_test_raw = ((test_df[FEATURE_COLS] - mu) / sd).values
    
    y_buy_train = train_df["y_buy"].values
    y_buy_test = test_df["y_buy"].values
    y_sell_train = train_df["y_sell"].values
    y_sell_test = test_df["y_sell"].values

    # Enforce purged boundaries during CV search to eliminate intra-fold overlap leakage
    tscv = TimeSeriesSplit(n_splits=3, gap=HORIZON)

    # --- 3. Zero-Importance Feature Pruning ---
    print(f"  Pruning zero-importance features...", flush=True)
    buy_keep_idx, buy_features = prune_zero_importance_features(X_train_raw, y_buy_train, FEATURE_COLS)
    sell_keep_idx, sell_features = prune_zero_importance_features(X_train_raw, y_sell_train, FEATURE_COLS)
    
    X_train_buy, X_test_buy = X_train_raw[:, buy_keep_idx], X_test_raw[:, buy_keep_idx]
    X_train_sell, X_test_sell = X_train_raw[:, sell_keep_idx], X_test_raw[:, sell_keep_idx]
    
    print(f"    BUY Features Kept:  {len(buy_features)}/{len(FEATURE_COLS)}")
    print(f"    SELL Features Kept: {len(sell_features)}/{len(FEATURE_COLS)}")

    # --- 4. Bayesian Optuna TPE Optimization ---
    print(f"  Optimizing BUY hyperparameters via Bayesian Search...", flush=True)
    best_buy_params = optimize_hyperparameters(X_train_buy, y_buy_train, tscv, "BUY")
    
    print(f"  Optimizing SELL hyperparameters via Bayesian Search...", flush=True)
    best_sell_params = optimize_hyperparameters(X_train_sell, y_sell_train, tscv, "SELL")

    # --- 5. Cross-Validation Ensembling (Fold Training) ---
    print(f"  Training Fold Ensembles with early stopping...", flush=True)
    
    # Configure base GBDT structures
    for params in [best_buy_params, best_sell_params]:
        params["objective"] = "reg:squarederror"
        params["tree_method"] = "hist"
        params["random_state"] = 42
        params["n_jobs"] = -1
        params["n_estimators"] = 1000
        params["early_stopping_rounds"] = 30

    buy_ensemble = []
    sell_ensemble = []

    # Train BUY Fold Models
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_train_buy)):
        X_tr_f, y_tr_f = X_train_buy[train_idx], y_buy_train[train_idx]
        X_val_f, y_val_f = X_train_buy[val_idx], y_buy_train[val_idx]
        
        model = XGBRegressor(**best_buy_params)
        model.fit(X_tr_f, y_tr_f, eval_set=[(X_val_f, y_val_f)], verbose=False)
        
        # Save fold model specifically to models/ directory
        model.save_model(str(MODEL_DIR / f"{coin}_BUY_fold_{fold:02d}.json"))
        buy_ensemble.append(model)

    # Train SELL Fold Models
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_train_sell)):
        X_tr_f, y_tr_f = X_train_sell[train_idx], y_sell_train[train_idx]
        X_val_f, y_val_f = X_train_sell[val_idx], y_sell_train[val_idx]
        
        model = XGBRegressor(**best_sell_params)
        model.fit(X_tr_f, y_tr_f, eval_set=[(X_val_f, y_val_f)], verbose=False)
        
        # Save fold model specifically to models/ directory
        model.save_model(str(MODEL_DIR / f"{coin}_SELL_fold_{fold:02d}.json"))
        sell_ensemble.append(model)

    # --- 6. Out-of-Sample Ensemble Averaging ---
    # Aggregate predictions across the model fold ensembles
    buy_preds_folds = np.array([m.predict(X_test_buy) for m in buy_ensemble])
    buy_pred_test = np.mean(buy_preds_folds, axis=0)
    buy_r2 = float(r2_score(y_buy_test, buy_pred_test))
    
    # Map predictions back to raw return space for realistic MAE reporting
    buy_pred_test_raw = buy_scaler.inverse_transform(buy_pred_test.reshape(-1, 1)).flatten()
    buy_mae_raw = float(mean_absolute_error(test_buy_raw.flatten(), buy_pred_test_raw))

    sell_preds_folds = np.array([m.predict(X_test_sell) for m in sell_ensemble])
    sell_pred_test = np.mean(sell_preds_folds, axis=0)
    sell_r2 = float(r2_score(y_sell_test, sell_pred_test))
    
    # Map predictions back to raw return space for realistic MAE reporting
    sell_pred_test_raw = sell_scaler.inverse_transform(sell_pred_test.reshape(-1, 1)).flatten()
    sell_mae_raw = float(mean_absolute_error(test_sell_raw.flatten(), sell_pred_test_raw))

    # Calculate global feature importances by averaging importances across folds
    buy_importances = np.mean([m.feature_importances_ for m in buy_ensemble], axis=0)
    sell_importances = np.mean([m.feature_importances_ for m in sell_ensemble], axis=0)

    buy_imp = pd.DataFrame({"feature": buy_features, "importance": buy_importances}).sort_values("importance", ascending=False).reset_index(drop=True)
    sell_imp = pd.DataFrame({"feature": sell_features, "importance": sell_importances}).sort_values("importance", ascending=False).reset_index(drop=True)

    # Collect statistics for the centralized report output
    master_report_data.append({
        "coin": coin,
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "optimal_horizon": HORIZON,
        "buy_r2": buy_r2, "buy_mae_pct": buy_mae_raw * 100,
        "sell_r2": sell_r2, "sell_mae_pct": sell_mae_raw * 100,
        "stats": df_stats,
        "top_buy_features": buy_imp.head(10),
        "top_sell_features": sell_imp.head(10)
    })

# Write consolidated master markdown report
master_report_path = REPORT_DIR / "Consolidated_Multi_Coin_Indicator_Analysis_Report.md"
with open(master_report_path, "w", encoding="utf-8") as rpt:
    rpt.write("# Consolidated Multi-Coin Indicator & Modeling Statistical Report\n\n")
    rpt.write(f"- Generated At: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    rpt.write("- Timeframe: 5min\n\n")

    for item in master_report_data:
        coin = item["coin"]
        rpt.write(f"## Asset Profile: {coin}\n\n")
        rpt.write(f"- **Optimal Target Horizon**: {item['optimal_horizon']} bars (~{item['optimal_horizon'] * 5} min)\n")
        rpt.write(f"- **Data Split Size**: Train: {item['train_rows']:,} rows, Test: {item['test_rows']:,} rows (with {item['optimal_horizon']} bar purged gap)\n")
        rpt.write(f"- **BUY Model Metrics (OOS)** - $R^2$: {item['buy_r2']:.4f}, MAE (in return space): {item['buy_mae_pct']:.4f}%\n")
        rpt.write(f"- **SELL Model Metrics (OOS)** - $R^2$: {item['sell_r2']:.4f}, MAE (in return space): {item['sell_mae_pct']:.4f}%\n\n")

        rpt.write("### Model Predictive Power (Top 5 Features)\n\n")
        rpt.write("| Rank | BUY Model Feature | Importance | SELL Model Feature | Importance |\n")
        rpt.write("|------|-------------------|------------|--------------------|------------|\n")
        for rank in range(5):
            b_feat = item["top_buy_features"].iloc[rank]["feature"]
            b_val = item["top_buy_features"].iloc[rank]["importance"]
            s_feat = item["top_sell_features"].iloc[rank]["feature"]
            s_val = item["top_sell_features"].iloc[rank]["importance"]
            rpt.write(f"| {rank + 1} | {b_feat} | {b_val:.5f} | {s_feat} | {s_val:.5f} |\n")
        rpt.write("\n")

        rpt.write("### Comprehensive Indicator Statistical Profile\n")
        rpt.write("Provides a detailed breakdown of distributions, auto-dependence, and predictive correlations.\n\n")
        rpt.write("| Indicator | Mean | Std Dev | Skewness | Kurtosis | Lag-1 ACF (Persistence) | Target Corr (IC Proxy) |\n")
        rpt.write("|-----------|------|---------|----------|----------|------------------------|------------------------|\n")
        
        # Sort by absolute correlation to display the most linearly predictive indicators first
        sorted_stats = item["stats"].reindex(item["stats"]["ic_proxy"].abs().sort_values(ascending=False).index)
        
        for idx, row in sorted_stats.iterrows():
            rpt.write(
                f"| {row['feature']} "
                f"| {row['mean']:.4f} "
                f"| {row['std']:.4f} "
                f"| {row['skew']:+.4f} "
                f"| {row['kurtosis']:+.4f} "
                f"| {row['acf_1']:.4f} "
                f"| {row['ic_proxy']:+.4f} |\n"
            )
        rpt.write("\n---\n\n")

print(f"\nTraining & statistical analysis complete.")
print(f"Master Statistical Report Saved: {master_report_path}")
print(f"Saved Models Directory:           {MODEL_DIR}")