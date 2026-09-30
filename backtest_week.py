"""Недельный реплей «как вживую»: 5m-свечи GT за N дней -> стратегия прода.

От backtest_month.py отличается:
- все пороги/сайз тянутся из config (дрейфа нет);
- статистика expectancy: винрейт, средний вин/лосс, profit factor, max DD;
- юниверс из CLI: chain:mint,... (дефолт: капы вотчлиста + прошлые мемы);
- сделки пишутся в backtest_trades.jsonl (разбор без гаданий).

Честные ограничения (читай перед выводами):
- в свечах НЕТ buys/sells и прошлой ликвидности: давление = направление+объём,
  liq-гейт и velocity-фильтры пропущены → входов БОЛЬШЕ, качество НИЖЕ реала;
- гранулярность 5м: внутрисвечные крахи -20% невидимы → стопов МЕНЬШЕ реала;
- проскальзывание сверх 1%+1%+tip не моделируется.
Итог: нижняя граница качества входов, верхняя — удержания. Сравнивай живой
винрейт с этим бенчмарком (16% на прошлом прогоне), а не абсолютный P&L.

Использование:
  python3 backtest_week.py [дней=7] [chain:mint,...]
  python3 backtest_week.py 7 solana:DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263,base:0x...
"""
import asyncio
import statistics
import sys

sys.path.insert(0, "/Users/taya/Downloads/kjdwk32ds-main-")
import config  # noqa: F401
import backtest_month as bm
from backtest_month import best_pool, fetch_candles, net_pct, simulate

SIZE = 6.0  # прод-сайз; захардкожен, т.к. капитал в реплее не растёт
bm.SIZE = SIZE  # simulate() берёт глобал месячного модуля

DEFAULT_UNIVERSE = [
    ("solana", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),    # JUP
    ("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),   # BONK
    ("solana", "So11111111111111111111111111111111111111112"),    # SOL
    ("solana", "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"),    # RAY
    ("solana", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"),    # WIF
    ("solana", "GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump"),   # JEANPHIL
    ("solana", "Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump"),   # CATE
    ("solana", "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk"),   # USELESS
]


def parse_universe(arg: str):
    out = []
    for part in (arg or "").split(","):
        part = part.strip()
        if ":" in part:
            ch, mint = part.split(":", 1)
            if ch.strip() and mint.strip():
                out.append((ch.strip().lower(), mint.strip()))
    return out


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    universe = parse_universe(sys.argv[2]) if len(sys.argv) > 2 else []
    if not universe:
        universe = DEFAULT_UNIVERSE
    print(f"Бектест {days}д на {len(universe)} токенах. Пороги/сайз($ {SIZE}) из прод-конфига.",
          flush=True)
    all_trades, per_token = [], []
    for network, mint in universe:
        pool = await best_pool(None, network, mint)
        await asyncio.sleep(3)
        if not pool:
            print(f"{network}:{mint[:10]}: нет пула в GT - скип")
            continue
        candles = await fetch_candles(network, pool, days)
        if len(candles) < 100:
            print(f"{network}:{mint[:10]}: свечей {len(candles)} - скип")
            continue
        trades = simulate(candles)
        tot = sum(t[0] for t in trades)
        per_token.append((mint[:10], len(trades), tot))
        for t in trades:
            all_trades.append({"mint": mint[:10], "pnl": round(t[0], 2),
                               "reason": t[1], "peak": t[2], "exit": t[3]})
        print(f"{network}:{mint[:10]}: свечей {len(candles)}, сделок {len(trades)}, итог ${tot:+.2f}")
    import json as _j
    with open("backtest_trades.jsonl", "w") as f:
        for t in all_trades:
            f.write(_j.dumps(t) + "\n")
    n = len(all_trades)
    wins = [t for t in all_trades if t["pnl"] > 0]
    loss = [t for t in all_trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in loss)
    eq, peak, maxdd = 0.0, 0.0, 0.0
    for t in all_trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
    from collections import Counter as _C
    print(f"\nИТОГ {days}д: сделок {n}, вин {len(wins)} ({100*len(wins)/max(n,1):.0f}%)")
    print(f"P&L ${eq:+.2f} | сред.вин ${gp/max(len(wins),1):+.2f} | сред.лосс ${-gl/max(len(loss),1):+.2f}")
    print(f"Expectancy ${eq/max(n,1):+.2f}/сделку | PF {gp/max(gl,1e-9):.2f} | maxDD ${maxdd:.2f}")
    print("Выходы: " + ", ".join(f"{k}={v}" for k, v in sorted(_C(t['reason'] for t in all_trades).items())))
    print("→ backtest_trades.jsonl записан. Бенчмарк живого винрейта: не ниже этого.")
    try:
        from http_client import close_session as _cs
        await _cs()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
