import numpy as np
import matplotlib.pyplot as plt
import os

def simulate_capital(strategy_name, tp_rule, n_trades=500):
    capital = 35.0
    history = [capital]
    
    np.random.seed(42) # For reproducible comparison
    
    for _ in range(n_trades):
        if capital < 4.0:
            break
            
        pos_size = min(max(4.0, capital * 0.15), 100.0)
        
        # Simulate trade outcome based on Pump.fun realities
        outcome = np.random.rand()
        
        # 1. 10% Instant Rug (-15% slippage up to -99%)
        if outcome < 0.10:
            pnl_pct = -np.random.uniform(0.15, 0.90) 
            
        # 2. 60% Dead Coin / Slow Bleed (Time exit or stop loss)
        elif outcome < 0.70:
            pnl_pct = -np.random.uniform(0.02, 0.15)
            
        # 3. 30% Runners (Pumps)
        else:
            max_pump = np.random.lognormal(mean=0, sigma=1.5) - 1.0 # Heavy tail
            max_pump = max(0.05, max_pump)
            
            if tp_rule == "diamond":
                if max_pump >= 0.80:
                    pnl_pct = max_pump * 0.75 # Trailing stop gives back 25%
                else:
                    # Didn't hit 80%, so it fell back down and stopped out or timed out
                    pnl_pct = np.random.choice([-0.15, -0.05, 0.0])
                    
            elif tp_rule == "scalper":
                if max_pump >= 0.25:
                    pnl_pct = 0.25 # Hard TP
                else:
                    pnl_pct = np.random.choice([-0.15, -0.05])
                    
            elif tp_rule == "smart":
                if max_pump >= 0.80:
                    pnl_pct = max_pump * 0.75 # Ride the rocket
                elif max_pump >= 0.25:
                    # Smart Break-even hit! Didn't reach 80%, but locked at least 5%
                    pnl_pct = np.random.uniform(0.05, 0.20)
                else:
                    # Time exit or stop loss
                    pnl_pct = np.random.choice([-0.15, -0.02])
                    
        trade_pnl = pos_size * pnl_pct
        capital += trade_pnl
        history.append(capital)
        
    return history

diamond = simulate_capital("Diamond Hands", "diamond")
scalper = simulate_capital("Scalper", "scalper")
smart = simulate_capital("Smart Rules", "smart")

plt.figure(figsize=(10, 6))
plt.plot(diamond, label=f"Diamond Hands (Final: ${diamond[-1]:.2f})", color='red')
plt.plot(scalper, label=f"Scalper 25% TP (Final: ${scalper[-1]:.2f})", color='blue')
plt.plot(smart, label=f"Smart Break-Even (Final: ${smart[-1]:.2f})", color='green', linewidth=2)
plt.axhline(y=35, color='gray', linestyle='--', alpha=0.5)
plt.title("Realistic Backtest: $35 Starting Capital, 500 Trades")
plt.xlabel("Number of Trades")
plt.ylabel("Account Balance ($)")
plt.legend()
plt.grid(True, alpha=0.3)
plt.yscale('log')
plt.savefig('/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/scratch/backtest_chart.png')
