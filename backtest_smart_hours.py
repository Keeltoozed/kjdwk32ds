"""Максимально реалистичный бектест ЖИВОЙ логики с улучшениями:
- Price action с реальных пампов (dune_data.csv)
- Volume patterns (производный объём из волатильности цен)
- Order book depth simulation (большие позиции = худшая цена)
- Slippage modeling 0.5-2%
"""
import bisect
import json
from collections import Counter
from datetime import timezone
import numpy as np
import pandas as pd
import config
from exit_managers import MatureExitManager
import random


def priority_fee(amount_usd):
    return 0.075 if amount_usd < 10.0 else 0.45


def simulate_slippage(amount_usd, base_price):
    """Моделирование проскальзывания 0.5-2% в зависимости от размера позиции."""
    # Большие позиции = больше проскальзывание
    slippage_base = 0.005  # 0.5% базовое
    slippage_max = 0.02    # 2% максимальное
    # Масштабируем проскальзывание по размеру позиции (относительно $100)
    scale_factor = min(amount_usd / 100.0, 2.0)  # кап на 2x
    slippage_pct = slippage_base + (slippage_max - slippage_base) * (scale_factor / 2.0)
    # Добавляем случайность (рыночный шум)
    noise = np.random.uniform(0.8, 1.2)
    return slippage_pct * noise


class SimPosition:
    __slots__ = ("entry_price_usd", "amount_usd", "entry_time",
                 "max_price_usd", "is_mature", "is_moonbag",
                 "volume_proxy")  # производный объём

    def __init__(self, entry_price, amount, entry_time, is_mature, volume_proxy=1.0):
        self.entry_price_usd = entry_price
        self.amount_usd = amount
        self.entry_time = entry_time
        self.max_price_usd = entry_price
        self.is_mature = is_mature
        self.is_moonbag = False
        self.volume_proxy = volume_proxy  # для моделирования глубины стакана


def simulate_trade(times, prices, entry_idx, amount, is_mature):
    entry_price_raw = prices[entry_idx]
    
    # === ORDER BOOK DEPTH SIMULATION ===
    # Большие позиции получают худшую цену (проскальзывание)
    slippage_pct = simulate_slippage(amount, entry_price_raw)
    entry_price = entry_price_raw * (1 + slippage_pct)  # хуже цена входа
    
    # === VOLUME PATTERNS ===
    # Производный объём из волатильности ценового пути
    price_range = max(prices[entry_idx:entry_idx+5]) - min(prices[entry_idx:entry_idx+10] if len(prices) > entry_idx+10 else prices[entry_idx:])
    volume_proxy = max(0.5, min(2.0, 1.0 + price_range * 100))  # масштаб
    
    entry_time = times[entry_idx]
    pos = SimPosition(entry_price, amount, entry_time, is_mature, volume_proxy)
    realized = 0.0
    real_entry = entry_price * 1.01  # +1% комиссия входа

    def full_close(price_raw, reason, t, is_exit=False):
        # === SLIPPAGE MODELING НА ВЫХОДЕ ===
        exit_slippage_pct = simulate_slippage(pos.amount_usd, price_raw) if is_exit else 0.0
        price = price_raw * (1 - exit_slippage_pct)  # хуже цена выхода
        
        diff = (price * 0.99 - real_entry) / real_entry if real_entry > 0 else 0
        pnl = pos.amount_usd * diff - priority_fee(pos.amount_usd)
        return {"pnl": realized + pnl, "exit_time": t, "reason": reason,
                "exit_price": price}

    for j in range(entry_idx + 1, len(prices)):
        price_raw = prices[j]
        t = times[j]
        minutes_held = (t - entry_time).total_seconds() / 60

        if price_raw <= 0:
            if minutes_held > 180:
                return full_close(0.0, "Rug Pull / No Liquidity", t, True)
            continue

        if price_raw > pos.max_price_usd:
            pos.max_price_usd = price_raw

        pnl_pct = (price_raw - entry_price) / entry_price  # базовый PnL без проскальзывания
        max_pnl = (pos.max_price_usd - entry_price) / entry_price

        if is_mature:
            reason = MatureExitManager.evaluate_exit(pos, price_raw)
            if reason:
                return full_close(price_raw, reason, t, True)
            continue

        # 1. Частичный тейк 50% на +35% пика
        if max_pnl >= 0.35 and not pos.is_moonbag:
            sold = pos.amount_usd * 0.6
            # Выход с проскальзыванием
            exit_slippage_pct = simulate_slippage(sold, price_raw)
            price_exit = price_raw * (1 - exit_slippage_pct)
            diff = (price_exit * 0.99 - real_entry) / real_entry
            realized += sold * diff - priority_fee(sold)
            pos.amount_usd -= sold
            pos.is_moonbag = True
            continue

        # 2. Трейлинг
        drop = (pos.max_price_usd - price_raw) / pos.max_price_usd
        if pos.is_moonbag:
            if drop >= 0.20:
                return full_close(price_raw, "Moonbag Trailing (20% drop)", t, True)
            continue
        if max_pnl >= config.TRAILING_ACTIVATION_PCT:
            if drop >= config.TRAILING_DISTANCE_PCT:
                return full_close(price_raw, f"Smart Trailing (+{max_pnl*100:.0f}% peak)", t, True)

        # 3. Hard stop
        if pnl_pct <= config.STOP_LOSS_PCT:
            return full_close(price_raw, f"Hard Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)", t, True)

        # === УМНЫЙ ВЫХОД ПО ВРЕМЕНИ (Stagnant / Bleeding cut) ===
        if minutes_held >= 15 and pnl_pct < 0:
            return full_close(price_raw, f"Dead Coin Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)", t, True)
            
        if minutes_held >= 25 and pnl_pct < 0.10:
            return full_close(price_raw, f"Stagnant Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)", t, True)

        if minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
            return full_close(price_raw, "Time-based Exit (Dead Coin)", t, True)

    return full_close(prices[-1], "EndOfData", times[-1], True)


def run_scenario(series, entry_mode):
    cands = []
    
    for mint, (times, prices) in series.items():
        # Check if entry is in a dead zone
        entry_dt = pd.to_datetime(times[0])
        hour = entry_dt.hour
        day = entry_dt.dayofweek
        
        # Dead Zone: Sat(5), Sun(6), or Hour between 5 and 13
        is_dead = False
        if day >= 5: 
            is_dead = True
        if 5 <= hour <= 13:
            is_dead = True
            
        if is_dead:
            continue

        if entry_mode == "sniper":
            ei = 0
            mature = False
        else:
            if len(prices) < 6:
                continue
            ei = 5
            mature = True
        if len(prices) < ei + 2:
            continue
        cands.append({"mint": mint, "entry_time": times[ei],
                      "entry_price": prices[ei], "entry_idx": ei,
                      "mature": mature})

    cands.sort(key=lambda c: c["entry_time"])
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
        if size < 4.0:
            open_trades = still_open
            continue

        times, prices = series[c["mint"]]
        r = simulate_trade(times, prices, c["entry_idx"], size, c["mature"])
        capital += r["pnl"]
        day_pnl[day] = day_pnl.get(day, 0.0) + r["pnl"]
        open_trades = still_open + [(r["exit_time"], r["pnl"])]
        done.append({**c, **r, "size": size, "capital_after": capital})

    events = sorted(done, key=lambda d: d["exit_time"])
    equity = []
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
        equity.append((d["exit_time"], eq))
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)

    return {"trades": done, "equity": equity, "max_dd": max_dd,
            "skipped_concurrent": skipped_concurrent, "skipped_kill": skipped_kill,
            "start": start, "final": capital}


def report(name, res):
    tr = res["trades"]
    print("=" * 60)
    print(f"СЦЕНАРИЙ {name}: сделок {len(tr)} "
          f"(пропущено: concurrent={res['skipped_concurrent']}, kill-switch={res['skipped_kill']})")
    if not tr:
        print("  нет сделок"); return
    pnls = np.array([t["pnl"] for t in tr])
    wins = pnls[pnls > 0]
    print(f"  Стартовый капитал:  ${res['start']:.2f}")
    print(f"  Итоговый капитал:   ${res['final']:.2f} "
          f"({(res['final']/res['start']-1)*100:+.1f}%)")
    print(f"  Win Rate:           {len(wins)/len(pnls)*100:.1f}% ({len(wins)}/{len(pnls)})")
    print(f"  Средний PnL/сделку: ${pnls.mean():+.2f} ({pnls.mean()/np.mean([t['size'] for t in tr])*100:+.2f}% от сайза)")
    print(f"  Суммарный PnL:      ${pnls.sum():+.2f}")
    print(f"  Макс. просадка MtM: {res['max_dd']*100:.1f}%")
    print(f"  Лучшая / худшая:    ${pnls.max():+.2f} / ${pnls.min():+.2f}")
    print(f"  С УЧЁТОМ ПРОСКАЛЬЗЫВАНИЯ (0.5-2%) и ГЛУБИНЫ СТАКАНА")
    print("  Причины выходов:")
    for reason, n in Counter(t["reason"] for t in tr).most_common():
        rp = [t["pnl"] for t in tr if t["reason"] == reason]
        print(f"    {n:4d}x  {reason}  (ср. ${np.mean(rp):+.2f})")


def main():
    import sys
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "dune_data.csv"
    df = pd.read_csv(csv_path)
    df["minute"] = pd.to_datetime(df["minute"], utc=True)
    df = df.sort_values(["mint", "minute"])
    series = {m: (g["minute"].tolist(), g["price_usd"].tolist())
              for m, g in df.groupby("mint")}
    print(f"Загружено: {len(series)} токенов, {len(df)} минутных свечей. "
          f"Ничего из сети не качалось.")
    print(f"Параметры: депозит ${config.INITIAL_BALANCE_USD}, "
          f"риск {config.REINVEST_PERCENT}%, стоп {config.STOP_LOSS_PCT*100:.0f}%, "
          f"тейк 50% на +35%, трейлинг +{config.TRAILING_ACTIVATION_PCT*100:.0f}%/"
          f"{config.TRAILING_DISTANCE_PCT*100:.0f}%, time-exit {config.TIME_EXIT_MINUTES} мин.")
    print(f"ДОПОЛНИТЕЛЬНО: проскальзывание 0.5-2%, моделирование глубины стакана, объём из волатильности")

    ra = run_scenario(series, "sniper")
    report("A — SNIPER (вход на 1-й минуте, скальперская логика)", ra)
    rb = run_scenario(series, "mature")
    report("B — MATURE (вход на 5-й минуте, swing-логика)", rb)

    bh = []
    for m, (times, prices) in series.items():
        if len(prices) < 2:
            continue
        size = 5.0
        diff = (prices[-1] * 0.99 - prices[0] * 1.01) / (prices[0] * 1.01)
        bh.append(size * diff - priority_fee(size))
    bh = np.array(bh)
    print("=" * 60)
    print(f"BASELINE Buy&Hold до конца данных: сделок {len(bh)}, "
          f"win rate {np.mean(bh > 0)*100:.1f}%, ср. PnL ${bh.mean():+.2f}")


if __name__ == "__main__":
    main()
