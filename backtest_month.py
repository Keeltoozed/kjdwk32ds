"""Месячный бектест на РЕАЛЬНЫХ свечах GeckoTerminal (бесплатно, до 6 мес истории).
5m-свечи за ~30 дней -> упрощённый replay стратегии:
вход: свеча +7..60% после красной + объём >= 2x медианы(20) + h1<300% + h24<500%
выходы: стоп -20%, трейлинг +15%/8%, мунбэг 60% (половина), стагнант 7м/25м, кэп -30%.
Комиссии как в проде: 1%+1%+tip. Сайз $10.

Ограничения (честно): в свечах нет buys/sells и ликвидности пула в прошлом -
давление аппроксимировано направлением+объёмом, ликва-гейт пропущен.
Мёртвые пулы без истории скипаются.

Использование: python3 backtest_month.py [дней, по умолч. 30]
"""
import asyncio
import statistics
import sys
import time

sys.path.insert(0, "/Users/taya/Downloads/kjdwk32ds-main-")
import config  # noqa: F401  (пороги из прод-конфига)

STOP = float(getattr(config, "STOP_LOSS_PCT", -0.20))
TRAIL_ACT = float(getattr(config, "TRAILING_ACTIVATION_PCT", 0.15))
TRAIL_DIST = float(getattr(config, "TRAILING_DISTANCE_PCT", 0.08))
MOONBAG = float(getattr(config, "MOONBAG_TRIGGER_PCT", 0.60))
CAP = -0.30
SIZE = 10.0
M5_MIN, M5_MAX = 7.0, 60.0

# (сеть GT, минт) - микс: прошлые ракеты/раги из истории + живые тренды
UNIVERSE = [
    ("solana", "GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump"),   # JEANPHIL
    ("solana", "Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump"),   # CATE
    ("solana", "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk"),   # USELESS
    ("solana", "DrK9ci66pPJxi9ig327XcVRs8cDzs7frXXoHWwwKvsN5"),   # Chiiba
    ("solana", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),    # JUP кап
    ("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),   # BONK кап
    ("solana", "So11111111111111111111111111111111111111112"),    # SOL
]

FEE_TIP = 0.075


def net_pct(entry, exit_):
    return ((exit_ * 0.99) - (entry * 1.01)) / (entry * 1.01)


async def best_pool(session, network, mint):
    from http_client import fetch_json
    st, d = await fetch_json(
        f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{mint}/pools",
        timeout=12, retries=2)
    if st != 200 or not d:
        return None
    pools = [x for x in (d.get("included") or []) if x.get("type") == "pool"]
    if not pools:  # обычный /pools без ?include=top_pools кладёт пулы в data[]
        pools = [x for x in (d.get("data") or []) if isinstance(x, dict) and x.get("type") == "pool"]
    if not pools:
        return None
    # id здесь вида solana_XXX (не адрес!) - настоящий адрес пула в attributes.address
    def _pool_addr(p):
        return ((p.get("attributes") or {}).get("address")) or ""
    best = max(pools, key=lambda x: float((x.get("attributes") or {}).get("reserve_in_usd", 0) or 0))
    return _pool_addr(best) or None


async def fetch_candles(network, pool_id, days):
    """5m-свечи назад на days дней. Возвращает [(ts, o,h,l,c,vol), ...] по возрастанию."""
    from http_client import fetch_json
    out = []
    need = int(days * 24 * 12)
    before = None
    while len(out) < need:
        url = (f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/"
               f"{pool_id}/ohlcv/minute?aggregate=5&limit=1000")
        if before:
            url += f"&before_timestamp={before}"
        st, d = await fetch_json(url, timeout=15, retries=2)
        if st != 200 or not d:
            break
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not rows:
            break
        # формат [ts, o,h,l,c,vol]
        batch = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
        batch.sort()
        out = batch + out
        before = batch[0][0]
        if len(rows) < 1000:
            break
        if len(out) > need + 1000:
            break
    return out[-need:]


def simulate(candles):
    """Возвращает (реализованный pnl$, сделок,细节 список)."""
    trades = []
    vols = [c[5] for c in candles]
    pos = None  # dict(entry, amt, max, locked, t0)
    for i in range(21, len(candles)):
        ts, o, h, l, c, v = candles[i]
        prev = candles[i - 1]
        if pos is None:
            chg = (c - o) / o * 100 if o else 0
            prev_red = prev[4] < prev[1]
            med = statistics.median(vols[max(0, i - 20):i]) or 1
            # h1/h24 из свечей
            c60 = candles[max(0, i - 12)][1]
            c288 = candles[max(0, i - 288)][1]
            h1 = (c - c60) / c60 * 100 if c60 else 0
            h24 = (c - c288) / c288 * 100 if c288 else 0
            if M5_MIN <= chg <= M5_MAX and prev_red and v >= 2 * med and h1 < 300 and h24 < 500:
                pos = {"entry": c, "amt": SIZE, "max": c, "locked": 0.0, "t0": ts, "moon": False}
            continue
        # трекинг открытой
        if c > pos["max"]:
            pos["max"] = c
        maxp = (pos["max"] - pos["entry"]) / pos["entry"]
        pnl = (c - pos["entry"]) / pos["entry"]
        held = (ts - pos["t0"]) / 60
        if maxp >= MOONBAG and not pos["moon"]:
            sold = pos["amt"] * 0.5
            pos["locked"] += sold * net_pct(pos["entry"], c) - min(FEE_TIP, sold * 0.05)
            pos["amt"] -= sold
            pos["moon"] = True
            continue
        reason = None
        if maxp >= TRAIL_ACT and (pos["max"] - c) / pos["max"] >= TRAIL_DIST:
            reason = "trail"
        elif pnl <= CAP:
            reason = "cap"
        elif pnl <= STOP:
            reason = "stop"
        elif held >= 7 and pnl < 0:
            reason = "stagnant-"
        elif held >= 25 and pnl < 0.05:
            reason = "stagnant"
        if reason:
            fin = pos["amt"] * net_pct(pos["entry"], c) - min(0.75 if reason in ("cap", "stop") else FEE_TIP, pos["amt"] * 0.05)
            trades.append((pos["locked"] + fin, reason, round(maxp * 100), round(pnl * 100)))
            pos = None
    if pos:  # открыта на конец истории - закрываем по рынку
        fin = pos["amt"] * net_pct(pos["entry"], candles[-1][4]) - FEE_TIP
        trades.append((pos["locked"] + fin, "eod", 0, 0))
    return trades


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"Бектест {days}д: пулы -> 5m-свечи -> replay. Пороги из config.", flush=True)
    grand, gw, gl = 0.0, 0, 0
    for network, mint in UNIVERSE:
        pool = await best_pool(None, network, mint)
        await asyncio.sleep(3)  # не упираемся в 30/мин GT
        if not pool:
            print(f"{mint[:10]}: нет пула в GT - скип")
            continue
        candles = await fetch_candles(network, pool, days)
        if len(candles) < 100:
            print(f"{mint[:10]}: свечей {len(candles)} - скип")
            continue
        trades = simulate(candles)
        tot = sum(t[0] for t in trades)
        grand += tot
        gw += sum(1 for t in trades if t[0] > 0)
        gl += sum(1 for t in trades if t[0] <= 0)
        big = max(trades, key=lambda t: t[0]) if trades else (0, "-", 0, 0)
        print(f"{mint[:10]}: свечей {len(candles)}, сделок {len(trades)}, итог ${tot:+.2f}, лучшая ${big[0]:+.2f} ({big[1]} пик +{big[2]}%)")
    n = gw + gl
    print(f"\nИТОГ {days}д: сделок {n}, вин {gw} ({100*gw/max(n,1):.0f}%), P&L ${grand:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
