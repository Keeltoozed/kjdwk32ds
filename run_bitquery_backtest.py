import requests
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

API_KEY = "ory_at_bmM3CAy6bJ3Ibv8Xbr2N-7a8XjqgsvGS2ADzNNRMY2o.jOXeymoz9jcOQrnodjLois-f-Jwr6awN3yWObxg6ijU"
URL = "https://streaming.bitquery.io/graphql"
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}

def run_query(query):
    resp = requests.post(URL, headers=HEADERS, json={"query": query})
    if resp.status_code == 200: 
        data = resp.json()
        if "errors" in data:
            print("GraphQL Error:", json.dumps(data["errors"], indent=2))
            return None
        return data
    print("HTTP Error:", resp.text)
    return None

def test_strategy(prices):
    max_pnl = 0
    hard_stop = -0.15
    for t, p in enumerate(prices):
        pnl = (p - prices[0]) / prices[0]
        max_pnl = max(max_pnl, pnl)
        
        current_stop = hard_stop
        # Smart Break-Even
        if max_pnl >= 0.25: current_stop = 0.05 
        if pnl <= current_stop: return current_stop
        # Dead Coin Timeout
        if t >= 10 and pnl < 0.05: return pnl
        # Stuck Profit Timeout
        if t >= 20 and pnl < 0.25: return pnl
        # Trailing stop
        if max_pnl >= 0.80:
            if (max_pnl - pnl) / (1 + max_pnl) >= 0.25: return pnl
                
    return (prices[-1] - prices[0]) / prices[0]

print("1. Fetching recent Pump.fun tokens...")

query_mints = """
{
  Solana {
    DEXTrades(
      where: {Trade: {Dex: {ProtocolName: {is: "pump"}}}}
      orderBy: {descendingByField: "Block_Time"}
      limit: {count: 200}
    ) {
      Block { Time }
      Trade {
        Buy { Currency { MintAddress Symbol } }
        Sell { Currency { MintAddress Symbol } }
      }
    }
  }
}
"""

data = run_query(query_mints)
if not data: exit(1)

mints = set()
for t in data['data']['Solana']['DEXTrades']:
    b = t['Trade']['Buy']['Currency']['MintAddress']
    s = t['Trade']['Sell']['Currency']['MintAddress']
    if b != "11111111111111111111111111111111": mints.add((b, t['Trade']['Buy']['Currency']['Symbol']))
    if s != "11111111111111111111111111111111": mints.add((s, t['Trade']['Sell']['Currency']['Symbol']))

mints = list(mints)[:15] # Test on 15 random tokens
print(f"Selected {len(mints)} tokens.")

results = []
for mint, symbol in mints:
    q = """
    {
      Solana {
        DEXTrades(
          where: {Trade: {Dex: {ProtocolName: {is: "pump"}}}, any: [{Trade: {Buy: {Currency: {MintAddress: {is: "%s"}}}}}, {Trade: {Sell: {Currency: {MintAddress: {is: "%s"}}}}}]}
          limit: {count: 500}
          orderBy: {ascendingByField: "Block_Time"}
        ) {
          Block { Time }
          Trade {
            Buy { Currency { MintAddress } PriceInUSD }
            Sell { Currency { MintAddress } PriceInUSD }
          }
        }
      }
    }
    """ % (mint, mint)
    
    td = run_query(q)
    if not td or not td.get('data', {}).get('Solana', {}).get('DEXTrades'): continue
    trades = td['data']['Solana']['DEXTrades']
    
    prices_by_minute = {}
    for t in trades:
        time_str = t['Block']['Time'][:16] 
        trade = t['Trade']
        price = trade['Buy'].get('PriceInUSD', 0) if trade['Buy']['Currency']['MintAddress'] == mint else trade['Sell'].get('PriceInUSD', 0)
        if price and price > 0:
            if time_str not in prices_by_minute: prices_by_minute[time_str] = []
            prices_by_minute[time_str].append(price)
            
    sorted_mins = sorted(prices_by_minute.keys())
    if not sorted_mins: continue
    minute_closes = [prices_by_minute[m][-1] for m in sorted_mins]
    
    if len(minute_closes) < 3:
        results.append(-0.15) 
        continue
        
    pnl = test_strategy(minute_closes)
    results.append(pnl)
    print(f"{symbol} ({mint[:4]}...): {pnl*100:+.2f}% (Tracked {len(minute_closes)} mins)")

print(f"\n=== LIVE BITQUERY BACKTEST ===")
print(f"Total tokens: {len(results)}")
print(f"Win Rate: {sum(1 for r in results if r > 0) / len(results) * 100:.1f}%")
print(f"Average PnL: {np.mean(results)*100:.2f}%")
