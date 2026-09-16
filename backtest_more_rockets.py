"""Сетка гейтов: как ловить БОЛЬШЕ ракет и не слить депозит.

Параметры гейта (минутные свечи dune_data, вход на первой минуте t>=5):
  pullback: m5 >= pb_m5, m1 in [pb_m1_lo, pb_m1_hi], h1 <= h1_max   (full size)
  vip:      m5 in [vip_lo, vip_max], m1 >= 0                        (full size)
  lottery:  m5 >= lot_min, m1 >= 0 (чистая вертикаль)               (size * lot_mult)
Выходы боевые mature: стоп -10%, трейлинг 12/4, profit lock, stagnant, time 60.
"""
import bisect
import sys
from datetime import timezone

import numpy as np
import pandas as pd

import config
from backtest_live_logic_v2 import simulate_trade


def gates(prices, t, g):
    m5 = prices[t] / prices[t - 5] - 1 if t >= 5 and prices[t - 5] > 0 else 0
    m1 = prices[t] / prices[t - 1] - 1 if t >= 1 and prices[t - 1] > 0 else 0
    h1 = prices[t] / prices[t - 60] - 1 if t >= 60 and prices[t - 60] > 0 else \
        (prices[t] / prices[0] - 1 if prices[0] > 0 else 0)
    pb = m5 >= g["pb_m5"] and g["pb_m1_lo"] <= m1 <= g["pb_m1_hi"] and h1 <= g["h1_max"]
    vip = g["vip_lo"] <= m5 <= g["vip_max"] and m1 >= 0
    lot = g.get("lot_min") and m5 >= g["lot_min"] and m1 >= 0
    if pb or vip:
        return 1.0
    if lot:
        return g["lot_mult"]
    return 0.0


def build(series, g):
    cands = []
    for mint, (times, prices) in series.items():
        if len(prices) < 8:
            continue
        for t in range(5, len(prices) - 2):
            mult = gates(prices, t, g)
            if mult > 0:
                cands.append({"mint": mint, "entry_time": times[t], "entry_idx": t,
                              "entry_price": prices[t], "mult": mult})
                break
    cands.sort(key=lambda c: c["entry_time"])
    return cands


def run(series, cands):
    capital = config.INITIAL_BALANCE_USD
    start = capital
    open_trades = []
    done = []
    day_pnl = {}
    for c in cands:
        et = c["entry_time"]
        still_open = [(x, p) for x, p in open_trades if x > et]
        day = et.astimezone(timezone.utc).date()
        if day_pnl.get(day, 0.0) <= -config.MAX_DAILY_LOSS_USD \
                or len(still_open) >= config.MAX_CONCURRENT_POSITIONS or capital < 5.0:
            open_trades = still_open
            continue
        size = max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0))) * c["mult"]
        if size < 1.0:
            open_trades = still_open
            continue
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
                k = max(bisect.bisect_right(times, d["exit_time"]) - 1, o["entry_idx"])
                unreal += o["size"] * ((prices[k] - o["entry_price"]) / o["entry_price"])
        eq = d["capital_after"] + unreal
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)
    return done, capital, max_dd


def report(name, series, g):
    done, final, dd = run(series, build(series, g))
    print("=" * 70)
    if not done:
        print(f"{name}: нет сделок")
        return
    pnls = np.array([t["pnl"] for t in done])
    rockets = sum(1 for t in done if t["pnl"] >= t["size"] * 1.0)
    half = sum(1 for t in done if t["pnl"] >= t["size"] * 0.5)
    lots = [t for t in done if t["mult"] < 1.0]
    lot_pnl = sum(t["pnl"] for t in lots)
    print(f"{name}")
    print(f"  сделок {len(done)} | $120 -> ${final:.2f} ({(final/120-1)*100:+.1f}%) | "
          f"WR {np.mean(pnls > 0)*100:.1f}% | DD {dd*100:.1f}%")
    print(f"  РАКЕТ: >=+100% сайза {rockets}, >=+50% {half} | лучшая ${pnls.max():+.2f}"
          + (f" | лотерея: {len(lots)} сделок, PnL ${lot_pnl:+.2f}" if lots else ""))


CUR = dict(pb_m5=0.08, pb_m1_lo=-0.10, pb_m1_hi=0.05, h1_max=1.5, vip_lo=0.15, vip_max=0.80)

SCEN = [
    ("S0 ТЕКУЩИЙ (vip<=80%)", CUR),
    ("S1 vip<=100%", {**CUR, "vip_max": 1.00}),
    ("S2 vip<=100% + h1<=300%", {**CUR, "vip_max": 1.00, "h1_max": 3.0}),
    ("S3 шире pullback (m5>=6, m1 -15..+8) + vip>=10%",
     {**CUR, "vip_max": 1.00, "h1_max": 3.0, "pb_m5": 0.06, "pb_m1_lo": -0.15,
      "pb_m1_hi": 0.08, "vip_lo": 0.10}),
    ("S4 S3 + лотерея вертикалей m5>=100% сайзом 40%",
     {**CUR, "vip_max": 1.00, "h1_max": 3.0, "pb_m5": 0.06, "pb_m1_lo": -0.15,
      "pb_m1_hi": 0.08, "vip_lo": 0.10, "lot_min": 1.00, "lot_mult": 0.4}),
    ("S5 S4 лотерея сайзом 25%",
     {**CUR, "vip_max": 1.00, "h1_max": 3.0, "pb_m5": 0.06, "pb_m1_lo": -0.15,
      "pb_m1_hi": 0.08, "vip_lo": 0.10, "lot_min": 1.00, "lot_mult": 0.25}),
]


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "dune_data.csv"
    df = pd.read_csv(csv_path)
    df["minute"] = pd.to_datetime(df["minute"], utc=True)
    df = df.sort_values(["mint", "minute"])
    series = {m: (g["minute"].tolist(), g["price_usd"].tolist())
              for m, g in df.groupby("mint")}
    print(f"Токенов: {len(series)} (реальные свечи).")
    for name, g in SCEN:
        report(name, series, g)


if __name__ == "__main__":
    main()
