import pandas as pd
import numpy as np

df = pd.read_csv('dune_data.csv')
df['minute'] = pd.to_datetime(df['minute'])
df = df.sort_values(by=['mint', 'minute'])

def test_strategy(prices, strategy):
    max_pnl = 0
    hard_stop = -0.15
    for t, p in enumerate(prices):
        pnl = (p - prices[0]) / prices[0]
        max_pnl = max(max_pnl, pnl)
        
        current_stop = hard_stop
        
        if strategy == "smart":
            if max_pnl >= 0.25: current_stop = 0.05 
            if pnl <= current_stop: return current_stop
            if t >= 10 and pnl < 0.05: return pnl
            if t >= 20 and pnl < 0.25: return pnl
            if max_pnl >= 0.80 and (max_pnl - pnl) / (1 + max_pnl) >= 0.25: return pnl
                
        elif strategy == "diamond":
            if pnl <= current_stop: return current_stop
            if t >= 10 and pnl < 0.05: return pnl
            if max_pnl >= 0.80 and (max_pnl - pnl) / (1 + max_pnl) >= 0.25: return pnl
                
    return (prices[-1] - prices[0]) / prices[0]

results_smart = []
results_diamond = []

grouped = df.groupby('mint')
for mint, group in grouped:
    prices = group['price_usd'].tolist()
    if len(prices) < 2: continue
    
    results_smart.append(test_strategy(prices, "smart"))
    results_diamond.append(test_strategy(prices, "diamond"))

print(f"\n=== БЕКТЕСТ DUNE (УНИКАЛЬНЫХ ТОКЕНОВ: {len(results_smart)}) ===")
print("СТАРАЯ ЛОГИКА (Diamond Hands):")
print(f"  Винрейт: {sum(1 for r in results_diamond if r > 0) / len(results_diamond) * 100:.1f}%")
print(f"  Средний PnL: {np.mean(results_diamond)*100:.2f}%\n")

print("НОВАЯ ЛОГИКА (Smart Break-Even + Time Exit):")
print(f"  Винрейт: {sum(1 for r in results_smart if r > 0) / len(results_smart) * 100:.1f}%")
print(f"  Средний PnL: {np.mean(results_smart)*100:.2f}%")
