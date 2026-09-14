import json
import pandas as pd
import numpy as np

# 1. Extract the CSV data from the transcript
log_path = "/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/.system_generated/logs/transcript_full.jsonl"
csv_content = ""

try:
    with open(log_path, 'r') as f:
        lines = f.readlines()
        # Find the last USER_INPUT
        for line in reversed(lines):
            data = json.loads(line)
            if data.get("type") == "USER_INPUT":
                content = data.get("content", "")
                if "minute,mint,price_usd" in content:
                    # Extract everything from 'minute,mint,price_usd' onwards
                    start_idx = content.find("minute,mint,price_usd")
                    csv_content = content[start_idx:]
                    break
                    
    with open('dune_data.csv', 'w') as f:
        f.write(csv_content)
        
    print("CSV data successfully extracted from chat history.")
except Exception as e:
    print(f"Failed to extract CSV: {e}")
    exit(1)

# 2. Run the backtest
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

print(f"\n=== РЕАЛЬНЫЙ БЕКТЕСТ (DUNE ANALYTICS) ===")
print(f"Обработано уникальных токенов: {len(results)}")
print(f"Win Rate (Сделки в плюс): {sum(1 for r in results if r > 0) / len(results) * 100:.1f}%")
print(f"Средняя чистая прибыль на сделку: {np.mean(results)*100:.2f}%")
