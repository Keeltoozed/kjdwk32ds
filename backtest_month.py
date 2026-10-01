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
TRAIL_ACT = float(getattr(config, "TRAILING_ACTIVATION_PCT", 0.25))
TRAIL_DIST = float(getattr(config, "EVM_RUNNER_TRAIL", 0.25))
MOONBAG = float(getattr(config, "MOONBAG_TRIGGER_PCT", 0.60))
CAP = -0.30
SIZE = 6.0  # прод-сайз EVM/sol (cap*5% при $120)
M5_MIN = float(getattr(config, "EVM_MIN_M5_PCT", 5.0))
M5_MAX = 60.0
STAG_LOSS_MIN = float(getattr(config, "STAGNANT_LOSS_MIN", 15))
STAG_HOLD_MIN = float(getattr(config, "STAGNANT_HOLD_MIN", 25))

# (сеть GT, минт) - микс: прошлые ракеты/раги из истории + живые тренды.
# GT отдаёт до ~6 мес 5m-свечей; мёртвые пулы скипаются автоматически.
UNIVERSE = [
    ("solana", "GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump"),   # JEANPHIL
    ("solana", "Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump"),   # CATE
    ("solana", "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk"),   # USELESS
    ("solana", "DrK9ci66pPJxi9ig327XcVRs8cDzs7frXXoHWwwKvsN5"),   # Chiiba
    ("solana", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),    # JUP кап
    ("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),   # BONK кап
    ("solana", "So11111111111111111111111111111111111111112"),    # SOL
    ("solana", "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"),    # RAY кап
    ("solana", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"),    # WIF кап
    ("solana", "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE"),     # ORCA кап
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
    addr = _pool_addr(best) or None
    reserve = float((best.get("attributes") or {}).get("reserve_in_usd", 0) or 0)
    return addr, reserve


async def fetch_candles(network, pool_id, days):
    """5m-свечи назад на days дней. Возвращает [(ts, o,h,l,c,vol), ...] по возрастанию.
    Кэш на диск (/tmp/bt_cache): окна GT плывут между прогонами, без кэша A/B
    сравнивает разные данные."""
    import hashlib as _h
    import json as _j
    import os as _o
    _cdir = "/tmp/bt_cache"
    try:
        _o.makedirs(_cdir, exist_ok=True)
    except Exception:
        pass
    _cf = _o.path.join(_cdir, _h.md5(f"{network}|{pool_id}|{days}".encode()).hexdigest() + ".json")
    try:
        if _o.path.exists(_cf):
            with open(_cf) as _f:
                return [tuple(r) for r in _j.load(_f)]
    except Exception:
        pass
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
    out = out[-need:]
    try:
        with open(_cf, "w") as _f:
            _j.dump(out, _f)
    except Exception:
        pass
    return out


def pressure_proxy(candles, i):
    """Прокси давления (в свечах нет buys/sells): объёмы зелёных свечей часа
    против красных — аналог прод-гейта h1 buys>=sells*1.5. Возвращает (ok, b, s)."""
    b = s = 0.0
    for j in range(max(0, i - 12), i):
        _o, _c, _v = candles[j][1], candles[j][4], candles[j][5]
        if _c >= _o:
            b += _v
        else:
            s += _v
    if s <= 0:
        return True, b, s
    return (b >= s * 1.5), b, s


def simulate(candles, liq_usd=999999.0):
    """Возвращает [(pnl$, причина, пик%, pnl%)]. liq_usd — статичный прокси
    текущего резерва (исторической ликвидности в свечах нет)."""
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
            if liq_usd < 20000:
                continue  # liq-гейт $20k (прокси: текущий резерв пула)
            if not (M5_MIN <= chg <= M5_MAX and prev_red and v >= 2 * med and h1 < 300 and h24 < 500):
                continue
            # Без прокси давления: для A/B exits важен один набор входов
            # с ракетами (давление в свечах невосстановимо; живые гейты
            # режут входы — это занижает винрейт replay, помним).
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
        elif held >= STAG_LOSS_MIN and pnl < 0:
            reason = "stagnant-"
        elif held >= STAG_HOLD_MIN and pnl < 0.05:
            reason = "stagnant"
        if reason:
            fin = pos["amt"] * net_pct(pos["entry"], c) - min(0.75 if reason in ("cap", "stop") else FEE_TIP, pos["amt"] * 0.05)
            trades.append((pos["locked"] + fin, reason, round(maxp * 100), round(pnl * 100)))
            pos = None
    if pos:  # открыта на конец истории - закрываем по рынку
        fin = pos["amt"] * net_pct(pos["entry"], candles[-1][4]) - FEE_TIP
        trades.append((pos["locked"] + fin, "eod", 0, 0))
    return trades


async def live_universe(per_chain=5):
    """Свежая вселенная недели: тренды GT по solana/base/bsc (+robinhood best-effort).
    Тестируем на ТЕКУЩЕМ рынке, а не на музейных минтах."""
    from http_client import fetch_json
    uni, seen = [], set()
    for net in ("solana", "base", "bsc", "robinhood"):
        try:
            st, d = await fetch_json(
                f"https://api.geckoterminal.com/api/v2/networks/{net}/trending_pools",
                timeout=12, retries=1)
            if st != 200 or not isinstance(d, dict):
                continue
            for item in (d.get("data") or [])[:per_chain]:
                try:
                    bid = (item.get("relationships") or {}).get("base_token", {}).get("data", {}).get("id", "")
                    mint = bid.split("_", 1)[1] if "_" in bid else ""
                    if mint and mint not in seen and len(mint) > 10:
                        seen.add(mint)
                        uni.append((net, mint))
                except Exception:
                    continue
        except Exception:
            continue
    return uni


async def pool_reserve(network, pool_id):
    """Заглушка: резерв уже известен из best_pool (лишний запрос убит —
    каждый GT-запрос ~3с+ретраи, экономим квоту)."""
    return 0.0


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    live = "--live" in sys.argv
    # Переопределения порогов для A/B:
    # python3 backtest_month.py 7 --set MOONBAG_TRIGGER_PCT=0.5
    # (форма --set K=V одним токеном тоже работает)
    global MOONBAG, STOP, TRAIL_ACT, TRAIL_DIST, M5_MIN, SIZE
    _args = list(sys.argv[2:])
    _kvs = []
    _i = 0
    while _i < len(_args):
        _a = _args[_i]
        if _a == "--set" and _i + 1 < len(_args) and "=" in _args[_i + 1]:
            _kvs.append(_args[_i + 1])
            _i += 2
            continue
        if _a.startswith("--set") and "=" in _a:
            _kvs.append(_a.split("--set", 1)[1].lstrip("="))
        _i += 1
    for _kv in _kvs:
        if "=" not in _kv:
            continue
        _k, _v = _kv.split("=", 1)
        _k = _k.strip()
        try:
            _v = float(_v.strip())
        except Exception:
            print(f"не число: {_kv}")
            continue
        if _k in globals():
            globals()[_k] = _v
            print(f"override {_k}={_v}")
        else:
            print(f"неизвестный ключ {_k} (MOONBAG/STOP/TRAIL_ACT/TRAIL_DIST/M5_MIN/SIZE)")
    print(f"Бектест {days}д: пулы -> 5m-свечи -> replay. Пороги из config.", flush=True)
    universe = await live_universe() if live else list(UNIVERSE)
    if live:
        print(f"LIVE-вселенная: {len(universe)} трендовых пулов (solana/base/bsc/robinhood).")
        universe += [u for u in UNIVERSE if u not in universe]
    grand, gw, gl = 0.0, 0, 0
    all_trades = []
    for network, mint in universe:
        # best_pool отдаёт и резерв (лишний запрос убит); троттлинг уже в fetch_json
        pool, liq = await best_pool(None, network, mint)
        if not pool:
            print(f"{mint[:10]}: нет пула в GT - скип")
            continue
        candles = await fetch_candles(network, pool, days)
        if len(candles) < 100:
            print(f"{mint[:10]}: свечей {len(candles)} - скип")
            continue
        trades = simulate(candles, liq_usd=liq)
        tot = sum(t[0] for t in trades)
        grand += tot
        gw += sum(1 for t in trades if t[0] > 0)
        gl += sum(1 for t in trades if t[0] <= 0)
        big = max(trades, key=lambda t: t[0]) if trades else (0, "-", 0, 0)
        from collections import Counter as _C
        reasons = dict(_C(t[1] for t in trades))
        print(f"{mint[:10]} [{network} liq~${liq:,.0f}]: свечей {len(candles)}, сделок {len(trades)}, итог ${tot:+.2f}, лучшая ${big[0]:+.2f} ({big[1]} пик +{big[2]}%)")
        if reasons:
            print(f"  причины: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
            for _t in trades:
                all_trades.append((mint[:10],) + _t)
    n = gw + gl
    print(f"\nИТОГ {days}д: сделок {n}, вин {gw} ({100*gw/max(n,1):.0f}%), P&L ${grand:+.2f}")
    from collections import Counter as _C2
    print("Причины всех выходов: " + ", ".join(f"{k}={v}" for k, v in sorted(_C2(t[2] for t in all_trades).items())))
    _wp = [t[1] for t in all_trades if t[1] > 0]
    _lp = [t[1] for t in all_trades if t[1] <= 0]
    _aw = sum(_wp) / len(_wp) if _wp else 0
    _al = sum(_lp) / len(_lp) if _lp else 0
    _pf = (sum(_wp) / abs(sum(_lp))) if _lp and sum(_lp) else 0.0
    _exp = (grand / n) if n else 0.0
    print(f"Средний вин ${ _aw:+.2f} / средний лосс ${_al:+.2f} | профит-фактор {_pf:.2f} | expectancy ${_exp:+.2f}/сделку")
    wins = sorted([t for t in all_trades if t[1] > 0], key=lambda t: -t[1])[:5]
    print("Топ-5 винов: " + "; ".join(f"{t[0]} ${t[1]:+.2f} ({t[2]} пик +{t[3]}%)" for t in wins))
    loss = sorted([t for t in all_trades if t[1] <= 0])[:5]
    print("Топ-5 лузеров: " + "; ".join(f"{t[0]} ${t[1]:+.2f} ({t[2]})" for t in loss))
    try:
        from http_client import close_session as _cs
        await _cs()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
