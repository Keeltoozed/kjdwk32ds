import pandas as pd
import requests
import json
import time
import numpy as np

# Load a sample of 20 random tokens from the dataset
df = pd.read_csv("pump_dataset.csv")
mints = df['mint'].sample(20, random_state=42).tolist()

trajectories = []

print("Fetching historical OHLCV data from GeckoTerminal for 20 real Pump.fun tokens...")
headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
for mint in mints:
    url = f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/ohlcv/minute?limit=60"
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            # ohlcv_list is [timestamp, open, high, low, close, volume] in descending time order
            if data and len(data) > 10:
                # Reverse to chronological order
                data.reverse()
                # Normalize prices to starting price = 1.0
                start_price = data[0][1] # Open price of first minute
                if start_price > 0:
                    prices = [row[4] / start_price for row in data] # Use close prices
                    trajectories.append(prices)
    except Exception as e:
        pass
    time.sleep(1) # Rate limit

print(f"Successfully fetched {len(trajectories)} real trajectories.")

def test_strategy(prices, use_smart_rules=False):
    max_pnl = 0
    hard_stop = -0.15
    for t, p in enumerate(prices):
        pnl = (p - 1.0)
        max_pnl = max(max_pnl, pnl)
        
        current_stop = hard_stop
        
        if use_smart_rules:
            # Smart Break-Even: If it hits +25%, lock stop at +5%
            if max_pnl >= 0.25:
                current_stop = 0.05 
        
        if pnl <= current_stop: return current_stop
        
        if use_smart_rules:
            if t >= 20 and 0.05 <= pnl < 0.25: return pnl # Stuck Profit Exit
            
        # Diamond Hands Trailing
        if max_pnl >= 0.80:
            if (max_pnl - pnl) / (1 + max_pnl) >= 0.25: return pnl
            
        # Dead Coin Exit
        if t >= 10 and pnl < 0.05: return pnl
                
    return (prices[-1] - 1.0)

if len(trajectories) > 0:
    pnl_old = [test_strategy(traj, False) for traj in trajectories]
    pnl_new = [test_strategy(traj, True) for traj in trajectories]
    
    print("\n--- RESULTS ON REAL HISTORICAL DATA ---")
    print(f"OLD STRATEGY (Diamond Hands only):")
    print(f"WinRate: {sum(1 for p in pnl_old if p > 0) / len(pnl_old) * 100:.1f}%")
    print(f"Avg PnL: {np.mean(pnl_old) * 100:.1f}%")
    
    print(f"\nNEW STRATEGY (Smart Break-Even + Stuck Exit):")
    print(f"WinRate: {sum(1 for p in pnl_new if p > 0) / len(pnl_new) * 100:.1f}%")
    print(f"Avg PnL: {np.mean(pnl_new) * 100:.1f}%")
