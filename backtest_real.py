import pandas as pd
import xgboost as xgb
import numpy as np
import json
import random

def run_real_backtest():
    print("=" * 60)
    print("🔬 БЭКТЕСТ V5: НА РЕАЛЬНЫХ ДАННЫХ ИЗ SUPABASE")
    print("   (Без выдуманных процентов. Только факты.)")
    print("=" * 60)
    
    # ============================================================
    # ЧАСТЬ 1: Реальное распределение PnL из 160 закрытых сделок
    # ============================================================
    with open("real_trades.json", "r") as f:
        real_trades = json.load(f)
    
    real_pnls = [t["pnl"] for t in real_trades if t.get("pnl") is not None]
    
    # Фактические вероятности по бакетам из Supabase
    total = len(real_pnls)
    p_catastrophic = len([p for p in real_pnls if p <= -50]) / total       # 9.4%
    p_heavy_loss   = len([p for p in real_pnls if -50 < p <= -25]) / total # 20.6%
    p_medium_loss  = len([p for p in real_pnls if -25 < p <= -10]) / total # 22.5%
    p_flat         = len([p for p in real_pnls if -10 < p <= 10]) / total  # 27.5%
    p_small_win    = len([p for p in real_pnls if 10 < p <= 50]) / total   # 14.4%
    p_big_win      = len([p for p in real_pnls if 50 < p <= 100]) / total  # 3.1%
    p_moon         = len([p for p in real_pnls if p > 100]) / total        # 2.5%
    
    print(f"\n📊 Реальное распределение PnL (из {total} сделок):")
    print(f"  Катастрофа (< -50%):    {p_catastrophic*100:.1f}%")
    print(f"  Тяжелый убыток (-50/-25): {p_heavy_loss*100:.1f}%")
    print(f"  Средний убыток (-25/-10): {p_medium_loss*100:.1f}%")
    print(f"  Около нуля (-10/+10):     {p_flat*100:.1f}%")
    print(f"  Маленький профит (+10/50): {p_small_win*100:.1f}%")
    print(f"  Большой профит (+50/100):  {p_big_win*100:.1f}%")
    print(f"  ИКСЫ (> +100%):           {p_moon*100:.1f}%")
    
    # Средние PnL внутри каждого бакета (реальные!)
    avg_catastrophic = np.mean([p for p in real_pnls if p <= -50]) if any(p <= -50 for p in real_pnls) else -75
    avg_heavy_loss = np.mean([p for p in real_pnls if -50 < p <= -25]) if any(-50 < p <= -25 for p in real_pnls) else -35
    avg_medium_loss = np.mean([p for p in real_pnls if -25 < p <= -10]) if any(-25 < p <= -10 for p in real_pnls) else -15
    avg_flat = np.mean([p for p in real_pnls if -10 < p <= 10]) if any(-10 < p <= 10 for p in real_pnls) else -2
    avg_small_win = np.mean([p for p in real_pnls if 10 < p <= 50]) if any(10 < p <= 50 for p in real_pnls) else 25
    avg_big_win = np.mean([p for p in real_pnls if 50 < p <= 100]) if any(50 < p <= 100 for p in real_pnls) else 70
    avg_moon = np.mean([p for p in real_pnls if p > 100]) if any(p > 100 for p in real_pnls) else 200
    
    print(f"\n📈 Средний PnL в каждом бакете:")
    print(f"  Катастрофа:       {avg_catastrophic:.1f}%")
    print(f"  Тяжелый убыток:   {avg_heavy_loss:.1f}%")
    print(f"  Средний убыток:   {avg_medium_loss:.1f}%")
    print(f"  Около нуля:       {avg_flat:.1f}%")
    print(f"  Маленький профит: {avg_small_win:.1f}%")
    print(f"  Большой профит:   {avg_big_win:.1f}%")
    print(f"  ИКСЫ:             {avg_moon:.1f}%")
    
    # ============================================================
    # ЧАСТЬ 2: Правильный Train/Test Split на pump_dataset
    # ============================================================
    df = pd.read_csv("pump_dataset.csv").dropna()
    features = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
    X = df[features]
    y = df["target"]
    
    # 80% train, 20% test (строго хронологический — первые 80% = прошлое)
    split_idx = int(len(df) * 0.80)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    
    # Переобучаем модель ТОЛЬКО на train
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=len(y_train[y_train==0])/max(1, len(y_train[y_train==1]))
    )
    model.fit(X_train, y_train)
    
    # Тестируем ТОЛЬКО на невиденных данных
    test_probs = model.predict_proba(X_test)[:, 1]
    test_preds = (test_probs >= 0.70).astype(int)
    
    total_test = len(y_test)
    signals = sum(test_preds)
    tp = sum((test_preds == 1) & (y_test.values == 1))
    fp = sum((test_preds == 1) & (y_test.values == 0))
    out_of_sample_wr = tp / signals if signals > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"🧪 OUT-OF-SAMPLE ТЕСТ (Модель НЕ видела эти {total_test} монет)")
    print(f"  Сигналов на покупку: {signals}")
    print(f"  True Positives: {tp}")
    print(f"  False Positives: {fp}")
    print(f"  Out-of-Sample Win Rate: {out_of_sample_wr*100:.1f}%")
    print(f"{'='*60}")
    
    # ============================================================
    # ЧАСТЬ 3: Симуляция портфеля с РЕАЛЬНЫМИ PnL из Supabase
    # ============================================================
    
    START_CAPITAL = 20.0
    REINVEST_PERCENT = 0.10
    MIN_TRADE = 4.0
    MAX_TRADE = 100.0
    MAX_CONCURRENT = 15
    JITO_FEE_USD = 0.075
    
    # Сколько реально сделок в день? Из Supabase: 160 за ~5 дней = ~32/день
    TRADES_PER_DAY = 32
    DAYS = 30
    TOTAL_SIMULATED_TRADES = TRADES_PER_DAY * DAYS
    
    # Но мы не можем протестировать больше сигналов, чем модель дала
    TOTAL_SIMULATED_TRADES = min(TOTAL_SIMULATED_TRADES, signals)
    
    capital = START_CAPITAL
    peak_capital = START_CAPITAL
    max_drawdown_pct = 0.0
    history = []
    
    wins = 0
    losses = 0
    
    buckets = [
        (p_catastrophic, avg_catastrophic),
        (p_heavy_loss, avg_heavy_loss),
        (p_medium_loss, avg_medium_loss),
        (p_flat, avg_flat),
        (p_small_win, avg_small_win),
        (p_big_win, avg_big_win),
        (p_moon, avg_moon),
    ]
    
    for i in range(TOTAL_SIMULATED_TRADES):
        if capital < MIN_TRADE:
            break
            
        trade_size = max(MIN_TRADE, min(MAX_TRADE, capital * REINVEST_PERCENT))
        
        # Выбираем PnL из реального распределения Supabase
        roll = random.random()
        cumulative = 0
        pnl_pct = 0
        for prob, avg_pnl in buckets:
            cumulative += prob
            if roll <= cumulative:
                # Добавляем шум (+/- 30% от среднего в бакете)
                pnl_pct = avg_pnl * np.random.uniform(0.7, 1.3)
                break
        
        if pnl_pct > 0:
            wins += 1
        else:
            losses += 1
            
        trade_pnl_usd = trade_size * (pnl_pct / 100.0) - JITO_FEE_USD
        capital += trade_pnl_usd
        
        if capital > peak_capital:
            peak_capital = capital
            
        dd = (peak_capital - capital) / peak_capital
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd
            
        history.append(capital)
    
    net = capital - START_CAPITAL
    roi = (net / START_CAPITAL) * 100
    actual_trades = len(history)
    wr = wins / actual_trades if actual_trades > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"💎 ИТОГОВЫЙ РЕАЛИСТИЧНЫЙ БЭКТЕСТ (30 дней)")
    print(f"  Источник PnL: Supabase (160 реальных закрытых сделок)")
    print(f"  Модель: Out-of-Sample (обучена на 80%, тест на 20%)")
    print(f"{'='*60}")
    print(f"  💰 Старт:        ${START_CAPITAL:.2f}")
    print(f"  💰 Итого:        ${capital:,.2f}")
    if net >= 0:
        print(f"  🟢 Прибыль:      ${net:,.2f} (+{roi:,.1f}%)")
    else:
        print(f"  🔴 Убыток:       ${abs(net):,.2f} ({roi:,.1f}%)")
    print(f"  📉 Max Drawdown: {max_drawdown_pct*100:.1f}%")
    print(f"  📈 Сделок:       {actual_trades}")
    print(f"  ⚔️  Win Rate:     {wr*100:.1f}%")
    print(f"  🏆 Выигрышей:    {wins}")
    print(f"  💀 Убытков:       {losses}")
    print(f"{'='*60}")

if __name__ == "__main__":
    run_real_backtest()
