"""Тест "теплоты" входа: сколько ракет ловим и какой ценой.

Ворота на минуте t (реальные свечи dune_data):
  PULLBACK: m5 >= +8%, m1 in [-10%, +5%], h1 <= +150%   (текущая live-логика)
  HEAT H:   m5 >= +15% и m5 <= H, m1 >= 0                (VIP-ракеты без отката)
Сценарии: P (только pullback), P+V40 (текущий VIP-потолок), P+V60, P+V80.
Выходы боевые mature (simulate_trade is_mature=True): стоп, трейлинг 12/4,
profit lock, stagnant, time-exit; проскальзывание 0.5-2%.
"""
import sys

import numpy as np
import pandas as pd

from backtest_buyers50 import run_portfolio
from backtest_live_logic_v2 import simulate_trade  # noqa: F401


def gate_at(prices, t, heat):
    m5 = prices[t] / prices[t - 5] - 1 if t >= 5 and prices[t - 5] > 0 else 0
    m1 = prices[t] / prices[t - 1] - 1 if t >= 1 and prices[t - 1] > 0 else 0
    h1 = prices[t] / prices[t - 60] - 1 if t >= 60 and prices[t - 60] > 0 else \
        (prices[t] / prices[0] - 1 if prices[0] > 0 else 0)
    pullback = m5 >= 0.08 and -0.10 <= m1 <= 0.05 and h1 <= 1.5
    vip = heat is not None and m5 >= 0.15 and m5 <= heat and m1 >= 0
    return pullback or vip


def build(series, heat):
    cands = []
    for mint, (times, prices) in series.items():
        if len(prices) < 8:
            continue
        ei = None
        for t in range(5, len(prices) - 2):
            if gate_at(prices, t, heat):
                ei = t
                break
        if ei is None:
            continue
        cands.append({"mint": mint, "entry_time": times[ei], "entry_idx": ei,
                      "entry_price": prices[ei]})
    cands.sort(key=lambda c: c["entry_time"])
    return cands


def run(series, cands):
    import bisect
    from collections import Counter
    from datetime import timezone
    import config
    capital = config.INITIAL_BALANCE_USD
    start = capital
    open_trades = []
    done = []
    day_pnl = {}
    for c in cands:
        et = c["entry_time"]
        still_open = [(x, p) for x, p in open_trades if x > et]
        day = et.astimezone(timezone.utc).date()
        if day_pnl.get(day, 0.0) <= -config.MAX_DAILY_LOSS_USD:
            open_trades = still_open
            continue
        if len(still_open) >= config.MAX_CONCURRENT_POSITIONS or capital < 5.0:
            open_trades = still_open
            continue
        size = max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0)))
        times, prices = series[c["mint"]]
        r = simulate_trade(times, prices, c["entry_idx"], size, True)
        capital += r["pnl"]
        day_pnl[day] = day_pnl.get(day, 0.0) + r["pnl"]
        open_trades = still_open + [(r["exit_time"], r["pnl"])]
        done.append({**c, **r, "size": size, "capital_after": capital})
    events = sorted(done, key=lambda d: d["exit_time"])
    peak = start
    max_dd = 0.0
    for d in events:
        unreal = 0.0
        for o in done:
            if o["entry_time"] <= d["exit_time"] < o["exit_time"]:
                times, prices = series[o["mint"]]
                k = bisect.bisect_right(times, d["exit_time"]) - 1
                k = max(k, o["entry_idx"])
                unreal += o["size"] * ((prices[k] - o["entry_price"]) / o["entry_price"])
        eq = d["capital_after"] + unreal
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)
    return {"trades": done, "max_dd": max_dd, "start": start, "final": capital}


def report(name, res):
    tr = res["trades"]
    print("=" * 64)
    print(f"{name}: сделок {len(tr)}")
    if not tr:
        print("  нет сделок")
        return
    pnls = np.array([t["pnl"] for t in tr])
    rockets = [t for t in tr if t["pnl"] >= t["size"] * 1.0]
    big = [t for t in tr if t["pnl"] >= t["size"] * 0.5]
    print(f"  $120 -> ${res['final']:.2f} ({(res['final']/120-1)*100:+.1f}%) | "
          f"WR {np.mean(pnls > 0)*100:.1f}% | просадка {res['max_dd']*100:.1f}%")
    print(f"  ракет (PnL >= +100% сайза): {len(rockets)} | >= +50% сайза: {len(big)} | "
          f"лучшая ${pnls.max():+.2f}")


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "dune_data.csv"
    df = pd.read_csv(csv_path)
    df["minute"] = pd.to_datetime(df["minute"], utc=True)
    df = df.sort_values(["mint", "minute"])
    series = {m: (g["minute"].tolist(), g["price_usd"].tolist())
              for m, g in df.groupby("mint")}
    print(f"Токенов: {len(series)}. P = pullback-гейт, Vxx = VIP-потолок m5.")
    report("P       (только pullback, без VIP)", run(series, build(series, None)))
    report("P+V40   (текущий live-потолок)", run(series, build(series, 0.40)))
    report("P+V60   (теплее)", run(series, build(series, 0.60)))
    report("P+V80   (почти вертикаль)", run(series, build(series, 0.80)))


if __name__ == "__main__":
    main()
