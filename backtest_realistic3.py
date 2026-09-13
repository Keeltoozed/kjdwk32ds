import pandas as pd
import xgboost as xgb
import numpy as np
import random

def run_backtest_with_jito():
    df = pd.read_csv("pump_dataset.csv").dropna()
    features = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
    X = df[features]
    y = df["target"]

    model = xgb.XGBClassifier()
    model.load_model("pump_model.json")
    probs = model.predict_proba(X)[:, 1]
    
    preds = (probs >= 0.70).astype(int)
    
    df['pred'] = preds
    df['actual'] = y
    trades = df[df['pred'] == 1].copy()
    
    trades = trades.sample(frac=1, random_state=42).reset_index(drop=True)

    START_CAPITAL = 20.0
    REINVEST_PERCENT = 0.10
    MIN_TRADE_SIZE = 4.0
    MAX_TRADE_SIZE = 100.0
    
    # 💎 С JITO МЫ ЗАЩИЩЕНЫ ОТ СЭНДВИЧЕЙ, ПОЭТОМУ SLIPPAGE НИЖЕ
    SLIPPAGE_IN = 0.03    
    SLIPPAGE_OUT = 0.05   
    
    # С Jito мы платим 0.0005 SOL ($0.075) чаевых за 100% гарантию
    JITO_FEE = 0.075 
    NETWORK_FEE = 0.005 
    
    # Штраф за медленный RPC исчезает благодаря Jito! 
    # Мы всегда входим вовремя.
    SLOW_RPC_PENALTY = 0.0 

    capital = START_CAPITAL
    peak_capital = START_CAPITAL
    max_drawdown_pct = 0.0
    portfolio_history = []
    
    wins_count = 0
    diamond_hands_wins = 0

    for idx, row in trades.iterrows():
        if capital < MIN_TRADE_SIZE:
            break
            
        trade_size = max(MIN_TRADE_SIZE, min(MAX_TRADE_SIZE, capital * REINVEST_PERCENT))
        
        entry_cost = JITO_FEE + NETWORK_FEE
        exit_cost = JITO_FEE + NETWORK_FEE
        
        effective_position = (trade_size - entry_cost) * (1 - SLIPPAGE_IN)
        
        if row['actual'] == 1:
            # Алгоритм Diamond Hands: ждем иксов (от 80%)
            # Симулируем: 40% реальных ракет доходят до +100% и выше.
            # 60% ракет умирают на +30..50%. Т.к. мы убрали ранние тейки, мы ловим стоп по ним.
            if random.random() < 0.40:
                pump_pct = np.random.uniform(0.80, 5.00) # Иксы!
                gross_exit = effective_position * (1 + pump_pct)
                wins_count += 1
                diamond_hands_wins += 1
            else:
                # Ракета сдулась до того, как дала икс. Вышли по жесткому стопу -25%
                gross_exit = effective_position * (1 - 0.25)
        else:
            # Скам / Rug
            # С Jito у нас шанс спастись (продать быстрее дева) выше
            if random.random() < 0.40:
                gross_exit = effective_position * (1 - 0.25) # Спаслись по стопу
            else:
                gross_exit = effective_position * (1 - 0.90) # Не успели

        net_exit = (gross_exit * (1 - SLIPPAGE_OUT)) - exit_cost
        
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
    
    total_trades = len(portfolio_history)
    real_win_rate = wins_count / total_trades if total_trades > 0 else 0

    print("-" * 50)
    print(f"💎 БЭКТЕСТ V3: JITO ENABLED + DIAMOND HANDS EXITS")
    print(f"💰 Стартовый капитал: ${START_CAPITAL:.2f}")
    print(f"💰 Итоговый капитал: ${capital:,.2f}")
    if capital < START_CAPITAL:
        print(f"🔴 Чистый УБЫТОК: ${abs(net_profit_usd):,.2f} ({roi_pct:,.1f}%)")
    else:
        print(f"🟢 Чистая прибыль: ${net_profit_usd:,.2f} (+{roi_pct:,.1f}%)")
    print(f"📉 Макс. просадка: {max_drawdown_pct*100:.1f}%")
    print(f"📈 Всего сделок: {total_trades}")
    print(f"🚀 Поймано ИКСОВ (Diamond Hands): {diamond_hands_wins}")
    print(f"⚔️ Итоговый Win Rate (только иксы): {real_win_rate*100:.1f}%")
    print("-" * 50)

if __name__ == "__main__":
    run_backtest_with_jito()
