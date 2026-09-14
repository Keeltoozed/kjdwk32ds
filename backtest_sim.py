import numpy as np
import pandas as pd

def simulate_meme_coin(n_steps=60, dt=1):
    # Simulate a single meme coin trajectory for 60 minutes
    # It either: 1) rugs (10%), 2) slow bleeds (70%), 3) pumps (20%)
    outcome = np.random.rand()
    prices = [1.0]
    
    if outcome < 0.10: # Instant rug in first 5 mins
        rug_time = np.random.randint(1, 5)
        for i in range(1, n_steps):
            if i == rug_time: prices.append(0.01)
            else: prices.append(prices[-1] * 0.99 if prices[-1] > 0.05 else prices[-1])
    elif outcome < 0.80: # Slow bleed / chop
        for i in range(1, n_steps):
            drift = -0.005 # negative drift
            vol = 0.05
            change = np.random.normal(drift, vol)
            prices.append(max(0.01, prices[-1] * (1 + change)))
    else: # Rocket
        for i in range(1, n_steps):
            drift = 0.02 # positive drift
            vol = 0.08 # high volatility (will have 30% dips!)
            change = np.random.normal(drift, vol)
            prices.append(max(0.01, prices[-1] * (1 + change)))
            
    return prices

def test_strategy(prices, tp_activation, trailing_drop, hard_stop, time_limit, time_profit_req):
    max_pnl = 0
    for t, p in enumerate(prices):
        pnl = (p - 1.0)
        max_pnl = max(max_pnl, pnl)
        
        # 1. Hard Stop
        if pnl <= hard_stop: return hard_stop
        
        # 2. Time Exit
        if t >= time_limit and pnl < time_profit_req:
            return pnl
            
        # 3. Trailing Stop
        if max_pnl >= tp_activation:
            # Trailing stop allows a drop from the peak
            allowed_drop = trailing_drop
            current_drop = (max_pnl - pnl) / (1 + max_pnl)
            if current_drop >= allowed_drop:
                return pnl # Sold at trailing stop
                
    return (prices[-1] - 1.0)

results = []
np.random.seed(42)
trajectories = [simulate_meme_coin() for _ in range(5000)]

strategies = {
    "Diamond Hands (Current)": (0.80, 0.25, -0.15, 10, 0.05),
    "Quick Scalper": (0.20, 0.10, -0.10, 5, 0.02),
    "Dynamic Adaptive": (0.35, 0.15, -0.15, 10, 0.05)
}

for name, params in strategies.items():
    pnls = [test_strategy(traj, *params) for traj in trajectories]
    win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
    avg_pnl = np.mean(pnls)
    max_win = np.max(pnls)
    print(f"{name}: WinRate={win_rate*100:.1f}%, AvgPnL={avg_pnl*100:.1f}%, MaxWin={max_win*100:.1f}%")
