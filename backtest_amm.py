import pandas as pd
import xgboost as xgb
import numpy as np
import random

def get_tokens_out_for_exact_sol_in(sol_in, virtual_sol, virtual_tokens):
    k = virtual_sol * virtual_tokens
    new_virtual_sol = virtual_sol + sol_in
    new_virtual_tokens = k / new_virtual_sol
    tokens_out = virtual_tokens - new_virtual_tokens
    return tokens_out, new_virtual_sol, new_virtual_tokens

def get_sol_out_for_exact_tokens_in(tokens_in, virtual_sol, virtual_tokens):
    k = virtual_sol * virtual_tokens
    new_virtual_tokens = virtual_tokens + tokens_in
    new_virtual_sol = k / new_virtual_tokens
    sol_out = virtual_sol - new_virtual_sol
    return sol_out, new_virtual_sol, new_virtual_tokens

def run_amm_backtest():
    print("🔬 ЗАПУСК БЭКТЕСТА С ЭМУЛЯЦИЕЙ PUMP.FUN BONDING CURVE...")
    
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

    SOL_PRICE_USD = 150.0
    START_CAPITAL_USD = 20.0
    REINVEST_PERCENT = 0.10
    MIN_TRADE_USD = 4.0
    MAX_TRADE_USD = 100.0
    
    JITO_FEE_SOL = 0.0005 # Jito tip
    NETWORK_FEE_SOL = 0.00005
    
    capital_sol = START_CAPITAL_USD / SOL_PRICE_USD
    peak_capital_sol = capital_sol
    max_drawdown_pct = 0.0
    
    wins_count = 0
    diamond_hands_wins = 0

    for idx, row in trades.iterrows():
        min_trade_sol = MIN_TRADE_USD / SOL_PRICE_USD
        max_trade_sol = MAX_TRADE_USD / SOL_PRICE_USD
        
        if capital_sol < min_trade_sol:
            break
            
        trade_size_sol = capital_sol * REINVEST_PERCENT
        trade_size_sol = max(min_trade_sol, min(max_trade_sol, trade_size_sol))
        
        # Pump.fun Initial State (примерно когда токен только вышел)
        # Реально бот входит не на старте, а когда там уже ~35 SOL
        # Допустим бот входит, когда кривая сдвинулась на +5 SOL
        virtual_sol = 35.0
        virtual_tokens = 919_714_285.71 # 32_190_000_000 / 35
        
        entry_cost = JITO_FEE_SOL + NETWORK_FEE_SOL
        available_sol_for_buy = trade_size_sol - entry_cost
        
        if available_sol_for_buy <= 0:
            capital_sol -= trade_size_sol
            continue
            
        # Бот покупает (Считаем точное проскальзывание AMM)
        tokens_bought, virtual_sol, virtual_tokens = get_tokens_out_for_exact_sol_in(
            available_sol_for_buy, virtual_sol, virtual_tokens
        )
        
        # Симуляция рынка (другие люди покупают или продают)
        if row['actual'] == 1:
            # Ракета! Толпа наливает от 10 до 50 SOL в пул
            if random.random() < 0.40:
                # Толпа вливает SOL (Иксы!)
                sol_inflow = np.random.uniform(10.0, 50.0) 
                _, virtual_sol, virtual_tokens = get_tokens_out_for_exact_sol_in(sol_inflow, virtual_sol, virtual_tokens)
                wins_count += 1
                diamond_hands_wins += 1
            else:
                # Ракета сорвалась (толпа слила часть)
                # Упало до уровня стоп-лосса
                # Мы выходим по стопу (цена упала на 25%)
                target_virtual_sol = virtual_sol * 0.866 # (0.866^2 = ~0.75 цены)
                sol_outflow = virtual_sol - target_virtual_sol
                _, virtual_sol, virtual_tokens = get_sol_out_for_exact_tokens_in(virtual_tokens * 0.1, virtual_sol, virtual_tokens) # Приблизительно
                # Мы просто говорим, что вирт SOL упал:
                virtual_sol = target_virtual_sol
                virtual_tokens = 32_190_000_000 / virtual_sol
        else:
            # Rug / Scam
            if random.random() < 0.40:
                # Спаслись по стоп-лоссу
                virtual_sol = virtual_sol * 0.866
                virtual_tokens = 32_190_000_000 / virtual_sol
            else:
                # Дев выдернул всё! (Виртуальный SOL падает до 30.1)
                virtual_sol = 30.1
                virtual_tokens = 32_190_000_000 / virtual_sol
                
        # Бот продает свои токены обратно в AMM
        sol_received, virtual_sol, virtual_tokens = get_sol_out_for_exact_tokens_in(
            tokens_bought, virtual_sol, virtual_tokens
        )
        
        net_exit_sol = sol_received - (JITO_FEE_SOL + NETWORK_FEE_SOL)
        
        trade_pnl_sol = net_exit_sol - trade_size_sol
        capital_sol += trade_pnl_sol
        
        if capital_sol > peak_capital_sol:
            peak_capital_sol = capital_sol
            
        current_drawdown = (peak_capital_sol - capital_sol) / peak_capital_sol
        if current_drawdown > max_drawdown_pct:
            max_drawdown_pct = current_drawdown

    capital_usd = capital_sol * SOL_PRICE_USD
    net_profit_usd = capital_usd - START_CAPITAL_USD
    roi_pct = (net_profit_usd / START_CAPITAL_USD) * 100
    
    print("-" * 50)
    print(f"📊 БЭКТЕСТ V4: ПОЛНАЯ ЭМУЛЯЦИЯ PUMP.FUN AMM (x * y = k)")
    print(f"💰 Стартовый капитал: ${START_CAPITAL_USD:.2f}")
    print(f"💰 Итоговый капитал: ${capital_usd:,.2f}")
    if capital_usd < START_CAPITAL_USD:
        print(f"🔴 Чистый УБЫТОК: ${abs(net_profit_usd):,.2f} ({roi_pct:,.1f}%)")
    else:
        print(f"🟢 Чистая прибыль: ${net_profit_usd:,.2f} (+{roi_pct:,.1f}%)")
    print(f"📉 Макс. просадка: {max_drawdown_pct*100:.1f}%")
    print(f"🚀 Поймано ИКСОВ (Diamond Hands): {diamond_hands_wins}")
    print("-" * 50)

if __name__ == "__main__":
    run_amm_backtest()
