import pandas as pd
import xgboost as xgb
import numpy as np
import random

def run_realistic_backtest():
    print("🔬 ЗАПУСК ГИПЕР-РЕАЛИСТИЧНОГО БЭКТЕСТА (С учетом MEV, Slippage и Jito Tips)...")
    
    try:
        df = pd.read_csv("pump_dataset.csv").dropna()
    except Exception as e:
        print(f"Ошибка загрузки датасета: {e}")
        return

    features = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
    X = df[features]
    y = df["target"]

    model = xgb.XGBClassifier()
    model.load_model("pump_model.json")
    probs = model.predict_proba(X)[:, 1]
    
    # 1. Порог входа строже (оставляем 70%)
    preds = (probs >= 0.70).astype(int)
    
    df['pred'] = preds
    df['actual'] = y
    trades = df[df['pred'] == 1].copy()
    
    # Перемешиваем
    trades = trades.sample(frac=1, random_state=42).reset_index(drop=True)

    # === РЕАЛИСТИЧНЫЕ ПАРАМЕТРЫ РЫНКА (SOLANA MEME COINS) ===
    START_CAPITAL = 20.0
    REINVEST_PERCENT = 0.10
    MIN_TRADE_SIZE = 4.0
    MAX_TRADE_SIZE = 100.0
    
    SLIPPAGE_IN = 0.05    # 5% теряем на входе из-за проскальзывания
    SLIPPAGE_OUT = 0.10   # 10% теряем на выходе (дампы всегда жестче)
    JITO_TIP_USD = 0.75   # Чаевые валидаторам за быстрый вход/выход ($0.75 * 2)
    NETWORK_FEE = 0.05    # Стандартная комса Solana
    
    # Пенальти за скорость (MEV/Sandwich)
    # Примерно 15% успешных ракет в реальности крадутся MEV-ботами или мы входим слишком поздно
    MEV_SANDWICH_PROBABILITY = 0.15 

    capital = START_CAPITAL
    peak_capital = START_CAPITAL
    max_drawdown_pct = 0.0
    portfolio_history = []
    
    wins_count = 0
    losses_count = 0

    for idx, row in trades.iterrows():
        if capital < MIN_TRADE_SIZE:
            break
            
        trade_size = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, capital * REINVEST_PERCENT))
        
        # Стоимость входа (Чаевые + Комса)
        entry_cost = JITO_TIP_USD + NETWORK_FEE
        exit_cost = JITO_TIP_USD + NETWORK_FEE
        
        # Фактический размер сделки после потери на Slippage при входе
        effective_position = (trade_size - entry_cost) * (1 - SLIPPAGE_IN)
        
        if effective_position <= 0:
            capital -= trade_size # Потеряли деньги просто на комиссиях
            portfolio_history.append(capital)
            losses_count += 1
            continue

        if row['actual'] == 1:
            # Это реальная ракета по датасету. Но успеем ли мы?
            if random.random() < MEV_SANDWICH_PROBABILITY:
                # Нас засендвичили или мы вошли на самом хае. Фиксируем убыток по стопу.
                gross_exit = effective_position * (1 - 0.15) # Стоп лосс -15%
                losses_count += 1
            else:
                # Нормальная ракета
                pump_pct = np.random.uniform(0.20, 1.00) # Реалистичный тейк 20-100%
                gross_exit = effective_position * (1 + pump_pct)
                wins_count += 1
        else:
            # Скам или Рагпул
            if random.random() < 0.20:
                # Успели выйти по стопу (20% шансов)
                gross_exit = effective_position * (1 - 0.15)
            else:
                # Рагпул / Dev Dump (80% шансов). Ликвидность выдернули.
                gross_exit = effective_position * (1 - 0.95)
            losses_count += 1

        # Применяем проскальзывание на выходе и вычитаем комиссии сети
        net_exit = (gross_exit * (1 - SLIPPAGE_OUT)) - exit_cost
        
        # Подсчет изменения капитала (net_exit - то что мы вложили)
        trade_pnl_usd = net_exit - trade_size
        capital += trade_pnl_usd
        
        if capital > peak_capital:
            peak_capital = capital
            
        current_drawdown = (peak_capital - capital) / peak_capital
        if current_drawdown > max_drawdown_pct:
            max_drawdown_pct = current_drawdown
            
        portfolio_history.append(capital)

    net_profit_usd = capital - START_CAPITAL
    roi_pct = (net_profit_usd / START_CAPITAL) * 100
    
    total_trades_taken = len(portfolio_history)
    real_win_rate = wins_count / total_trades_taken if total_trades_taken > 0 else 0

    print("-" * 50)
    print(f"📉 РЕАЛИСТИЧНЫЙ БЭКТЕСТ (Slippage: IN 5% / OUT 10%, Jito: $1.5/trade)")
    print(f"💰 Стартовый капитал: ${START_CAPITAL:.2f}")
    print(f"💰 Итоговый капитал: ${capital:,.2f}")
    if capital < START_CAPITAL:
        print(f"🔴 Чистый УБЫТОК: ${abs(net_profit_usd):,.2f} ({roi_pct:,.1f}%)")
    else:
        print(f"🟢 Чистая прибыль: ${net_profit_usd:,.2f} (+{roi_pct:,.1f}%)")
    print(f"📉 Макс. просадка (Max Drawdown): {max_drawdown_pct*100:.1f}%")
    print(f"📈 Всего сделок: {total_trades_taken}")
    print(f"⚔️ Реальный Win Rate (с учетом MEV-сэндвичей): {real_win_rate*100:.1f}%")
    print("-" * 50)
    
if __name__ == "__main__":
    run_realistic_backtest()
