import pandas as pd
import numpy as np

df = pd.read_csv('dune_data.csv')
df['minute'] = pd.to_datetime(df['minute'])
df = df.sort_values(by=['mint', 'minute'])

def test_strategy(prices, strategy="smart"):
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
                
    return (prices[-1] - prices[0]) / prices[0]

# Extract final PNLs in chronological order of their FIRST trade
token_pnls = []
grouped = df.groupby('mint')
for mint, group in grouped:
    prices = group['price_usd'].tolist()
    if len(prices) < 2: continue
    
    first_time = group['minute'].iloc[0]
    pnl = test_strategy(prices, "smart")
    token_pnls.append((first_time, pnl))

token_pnls.sort(key=lambda x: x[0])

capital = 35.0
history = [capital]

for t, pnl_pct in token_pnls:
    if capital < 4.0:
        break # bankrupt
    
    # 15% of capital per trade, bounded by 4.0 and 100.0
    pos_size = min(max(4.0, capital * 0.15), 100.0)
    
    # Subtract 0.05% swap fee (Jupiter) and Jito tip equivalent (~$0.07)
    fees_usd = 0.07 + (pos_size * 0.005)
    
    trade_profit = (pos_size * pnl_pct) - fees_usd
    capital += trade_profit
    history.append(capital)

print(f"Start Balance: $35.00")
print(f"Final Balance: ${capital:.2f}")
print(f"Net Profit: ${capital - 35.0:.2f}")
