import numpy as np

def simulate_meme_coin(n_steps=60):
    outcome = np.random.rand()
    prices = [1.0]
    
    if outcome < 0.10: # Instant rug in first 5 mins
        rug_time = np.random.randint(1, 5)
        for i in range(1, n_steps):
            if i == rug_time: prices.append(0.01)
            else: prices.append(prices[-1] * 0.99 if prices[-1] > 0.05 else prices[-1])
    elif outcome < 0.80: # Slow bleed / chop
        for i in range(1, n_steps):
            change = np.random.normal(-0.005, 0.05)
            prices.append(max(0.01, prices[-1] * (1 + change)))
    else: # Rocket
        for i in range(1, n_steps):
            change = np.random.normal(0.02, 0.08)
            prices.append(max(0.01, prices[-1] * (1 + change)))
    return prices

def test_strategy(prices):
    max_pnl = 0
    hard_stop = -0.15
    for t, p in enumerate(prices):
        pnl = (p - 1.0)
        max_pnl = max(max_pnl, pnl)
        
        # 1. Smart Lock Profit (If it hits +40%, never let it go negative. Lock at +10%)
        current_stop = hard_stop
        if max_pnl >= 0.40:
            current_stop = 0.10 
            
        # 2. Hard / Locked Stop
        if pnl <= current_stop: return current_stop
        
        # 3. Time Exit
        if t >= 10 and pnl < 0.05: return pnl
            
        # 4. Trailing Stop
        if max_pnl >= 0.80:
            if (max_pnl - pnl) / (1 + max_pnl) >= 0.25: return pnl
                
    return (prices[-1] - 1.0)

np.random.seed(42)
trajectories = [simulate_meme_coin() for _ in range(5000)]

pnls = [test_strategy(traj) for traj in trajectories]
win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
avg_pnl = np.mean(pnls)
max_win = np.max(pnls)
print(f"Smart Lock (+40% -> +10%): WinRate={win_rate*100:.1f}%, AvgPnL={avg_pnl*100:.1f}%, MaxWin={max_win*100:.1f}%")
