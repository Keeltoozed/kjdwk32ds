import numpy as np

def simulate_capital(strategy_name, tp_rule, n_trades=500):
    capital = 35.0
    np.random.seed(42)
    
    for _ in range(n_trades):
        if capital < 4.0:
            break
            
        pos_size = min(max(4.0, capital * 0.15), 100.0)
        outcome = np.random.rand()
        
        if outcome < 0.10:
            pnl_pct = -np.random.uniform(0.15, 0.90) 
        elif outcome < 0.70:
            pnl_pct = -np.random.uniform(0.02, 0.15)
        else:
            max_pump = np.random.lognormal(mean=0, sigma=1.5) - 1.0 
            max_pump = max(0.05, max_pump)
            
            if tp_rule == "diamond":
                if max_pump >= 0.80:
                    pnl_pct = max_pump * 0.75 
                else:
                    pnl_pct = np.random.choice([-0.15, -0.05, 0.0])
                    
            elif tp_rule == "scalper":
                if max_pump >= 0.25:
                    pnl_pct = 0.25 
                else:
                    pnl_pct = np.random.choice([-0.15, -0.05])
                    
            elif tp_rule == "smart":
                if max_pump >= 0.80:
                    pnl_pct = max_pump * 0.75 
                elif max_pump >= 0.25:
                    pnl_pct = np.random.uniform(0.05, 0.20)
                else:
                    pnl_pct = np.random.choice([-0.15, -0.02])
                    
        trade_pnl = pos_size * pnl_pct
        capital += trade_pnl
        
    return capital

d = simulate_capital("Diamond", "diamond")
s = simulate_capital("Scalper", "scalper")
sm = simulate_capital("Smart", "smart")
print(f"Diamond: ${d:.2f}")
print(f"Scalper: ${s:.2f}")
print(f"Smart: ${sm:.2f}")
