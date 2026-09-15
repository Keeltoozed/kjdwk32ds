"""Бэктест триггера снайпера "N уникальных покупателей за 5 минут".

Цена реальная (dune_data.csv, минутные свечи pump.fun).
Число покупателей восстанавливается из bonding-curve математики:
каждый уникальный покупатель своим нетто-объёмом двигает цену вверх,
поэтому buyers_m5 = sum(max(0, ret_m)) / IMPACT_PER_BUYER за окно 5 минут.
IMPACT_PER_BUYER = 2% — средний нетто-сдвиг цены от одного покупателя
на стартовой капитализации (~$5-15k) с учётом встречных продаж.
Калибровка: 15 покупателей => m5 ~ +30%, 50 покупателей => m5 ~ +100%.

Сценарии:
  A: триггер 15 покупателей, вход сразу
  B: триггер 50 покупателей, вход сразу
  C: триггер 15 + вход на откате (m1 <= +2%, ждать не больше 10 мин)
  D: триггер 50 + вход на откате (наша pullback-стратегия)
Выходы — боевая логика снайпера (simulate_trade из backtest_live_logic_v2):
тейк 60% на +35%, трейлинг, стоп -15%, stagnant, time-exit 60 мин,
проскальзывание 0.5-2% и глубина стакана.
"""
import bisect
import sys
from collections import Counter
from datetime import timezone

import numpy as np
import pandas as pd

import config
from backtest_live_logic_v2 import simulate_trade

IMPACT_PER_BUYER = 0.02
PULLBACK_M1_MAX = 0.02
PULLBACK_WAIT_MIN = 10


def buyers_series(rets, impact):
    out = []
    acc = 0.0
    for i, r in enumerate(rets):
        acc += max(0.0, r)
        if i >= 5:
            acc -= max(0.0, rets[i - 5])
        out.append(acc / impact)
    return out


def build_candidates(series, min_buyers, pullback):
    cands = []
    for mint, (times, prices) in series.items():
        if len(prices) < 8:
            continue
        rets = [0.0] + [(prices[i] - prices[i - 1]) / prices[i - 1]
                        for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(rets) != len(prices):
            continue
        buyers = buyers_series(rets, IMPACT_PER_BUYER)
        trigger_idx = None
        for i in range(5, len(prices) - 2):
            if buyers[i] >= min_buyers:
                trigger_idx = i
                break
        if trigger_idx is None:
            continue
        ei = trigger_idx
        if pullback:
            ei = None
            for j in range(trigger_idx, min(trigger_idx + PULLBACK_WAIT_MIN, len(prices) - 2)):
                m1 = (prices[j] - prices[j - 1]) / prices[j - 1] if prices[j - 1] > 0 else 0
                if m1 <= PULLBACK_M1_MAX:
                    ei = j
                    break
            if ei is None:
                continue
        cands.append({"mint": mint, "entry_time": times[ei],
                      "entry_idx": ei, "entry_price": prices[ei],
                      "trigger_idx": trigger_idx})
    cands.sort(key=lambda c: c["entry_time"])
    return cands


def run_portfolio(series, cands):
    capital = config.INITIAL_BALANCE_USD
    start = capital
    open_trades = []
    done = []
    day_pnl = {}
    skipped_concurrent = 0
    skipped_kill = 0
    for c in cands:
        et = c["entry_time"]
        still_open = [(x, p) for x, p in open_trades if x > et]
        day = et.astimezone(timezone.utc).date()
        if day_pnl.get(day, 0.0) <= -config.MAX_DAILY_LOSS_USD:
            skipped_kill += 1
            open_trades = still_open
            continue
        if len(still_open) >= config.MAX_CONCURRENT_POSITIONS:
            skipped_concurrent += 1
            open_trades = still_open
            continue
        if capital < 5.0:
            open_trades = still_open
            continue
        size = max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0)))
        times, prices = series[c["mint"]]
        r = simulate_trade(times, prices, c["entry_idx"], size, False)
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
                px = prices[k]
                unreal += o["size"] * ((px - o["entry_price"]) / o["entry_price"])
        eq = d["capital_after"] + unreal
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)
    return {"trades": done, "max_dd": max_dd, "start": start, "final": capital,
            "skipped_concurrent": skipped_concurrent, "skipped_kill": skipped_kill}


def report(name, res):
    tr = res["trades"]
    print("=" * 64)
    print(f"СЦЕНАРИЙ {name}: сделок {len(tr)} "
          f"(пропущено: concurrent={res['skipped_concurrent']}, kill={res['skipped_kill']})")
    if not tr:
        print("  нет сделок")
        return
    pnls = np.array([t["pnl"] for t in tr])
    wins = pnls[pnls > 0]
    moons = [t for t in tr if t["pnl"] >= t["size"] * 0.5]
    print(f"  Капитал: ${res['start']:.0f} -> ${res['final']:.2f} "
          f"({(res['final']/res['start']-1)*100:+.1f}%)")
    print(f"  Win Rate: {len(wins)/len(pnls)*100:.1f}% | "
          f"ср. PnL/сделку ${pnls.mean():+.2f} | суммарно ${pnls.sum():+.2f}")
    print(f"  Макс. просадка MtM: {res['max_dd']*100:.1f}% | "
          f"лучшая ${pnls.max():+.2f} / худшая ${pnls.min():+.2f}")
    print(f"  Сделок с PnL >= +50% сайза: {len(moons)}")
    for reason, n in Counter(t["reason"] for t in tr).most_common(5):
        rp = [t["pnl"] for t in tr if t["reason"] == reason]
        print(f"    {n:4d}x  {reason}  (ср. ${np.mean(rp):+.2f})")


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "dune_data.csv"
    df = pd.read_csv(csv_path)
    df["minute"] = pd.to_datetime(df["minute"], utc=True)
    df = df.sort_values(["mint", "minute"])
    series = {m: (g["minute"].tolist(), g["price_usd"].tolist())
              for m, g in df.groupby("mint")}
    print(f"Загружено: {len(series)} токенов, {len(df)} свечей (реальные данные).")
    print(f"Прокси покупателей: 1 покупатель = нетто +{IMPACT_PER_BUYER*100:.0f}% к цене "
          f"(bonding curve, старт-кап ~$5-15k). 15 пок. => m5 ~+30%, 50 пок. => m5 ~+100%.")

    report("A — 15 покупателей / 5 мин, вход сразу",
           run_portfolio(series, build_candidates(series, 15, False)))
    report("B — 50 покупателей / 5 мин, вход сразу",
           run_portfolio(series, build_candidates(series, 50, False)))
    report("C — 15 покупателей + вход на откате",
           run_portfolio(series, build_candidates(series, 15, True)))
    report("D — 50 покупателей + вход на откате (наша стратегия)",
           run_portfolio(series, build_candidates(series, 50, True)))


if __name__ == "__main__":
    main()
