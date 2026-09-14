import pandas as pd
import numpy as np

# Load the file we just copied
df = pd.read_csv('dune_data.csv')

df['minute'] = pd.to_datetime(df['minute'])
df = df.sort_values(by=['mint', 'minute'])

def test_strategy(prices):
    max_pnl = 0
    hard_stop = -0.15
    for t, p in enumerate(prices):
        pnl = (p - prices[0]) / prices[0]
        max_pnl = max(max_pnl, pnl)
        
        current_stop = hard_stop
        # Smart Break-Even: If it hits +25%, lock stop at +5%
        if max_pnl >= 0.25:
            current_stop = 0.05 
            
        if pnl <= current_stop: return current_stop
        
        # Dead Coin Timeout (10 minutes)
        if t >= 10 and pnl < 0.05: return pnl
        
        # Stuck Profit Timeout (20 minutes)
        if t >= 20 and pnl < 0.25: return pnl
            
        # Trailing stop
        if max_pnl >= 0.80:
            if (max_pnl - pnl) / (1 + max_pnl) >= 0.25: return pnl
                
    return (prices[-1] - prices[0]) / prices[0]

results = []
grouped = df.groupby('mint')
for mint, group in grouped:
    prices = group['price_usd'].tolist()
    if len(prices) < 2:
        continue # Ignore single minute rug/glitch
    
    pnl = test_strategy(prices)
    results.append(pnl)

print(f"\n=== РЕАЛЬНЫЙ БЕКТЕСТ (DUNE ANALYTICS ЗА МЕСЯЦ) ===")
print(f"Обработано уникальных токенов: {len(results)}")
print(f"Win Rate (Сделки в плюс): {sum(1 for r in results if r > 0) / len(results) * 100:.1f}%")
print(f"Средняя чистая прибыль на сделку: {np.mean(results)*100:.2f}%")
