"""Бектест стратегии на базе реальной истории сделок (real_trades.json).

Честное ограничение: в real_trades.json хранится только результат сделки
(mint, pnl, exit_reason, entry_time) — нет исторических снимков рынка
(m5/h1/h6/age/liq на момент входа). Поэтому мы не можем перепрогнать
analyzer_token на прошлое. Что делаем вместо этого:

1. Считаем реальную статистику по 160 сделкам (PNL, win-rate, avg win/loss).
2. Группируем убытки по exit_reason — понимаем, какие причины доминируют.
3. 시мулируем «что если»: отсекаем самые убыточные категории входов,
   которые могли бы быть отфильтрованы обновлёнными гейтами (Fix 4/5).
4. Выдаём таблицу «реальный P&L vs оптимистичный P&L» и список худших сделок.

Запуск: python3 backtest.py
"""
import json
from collections import Counter, defaultdict
from datetime import datetime

TRADES_FILE = "real_trades.json"


def load_trades():
    with open(TRADES_FILE, encoding="utf-8") as f:
        return json.load(f)


def stats(trades):
    if not trades:
        return {}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "count": len(trades),
        "total_pnl": round(sum(pnls), 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(trades), 1),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else None,
    }


def by_reason(trades):
    groups = defaultdict(list)
    for t in trades:
        groups[t.get("exit_reason", "unknown")].append(t)
    out = []
    for reason, items in groups.items():
        p = sum(i["pnl"] for i in items)
        out.append((reason, len(items), round(p, 2), round(p / len(items), 2)))
    out.sort(key=lambda x: x[2])
    return out


def simulate_filter(trades, drop_reasons):
    """Удалить сделки с указанными exit_reason (как если бы гейт их отрезал на входе)."""
    kept = [t for t in trades if t.get("exit_reason") not in drop_reasons]
    return kept


def main():
    trades = load_trades()
    print("=" * 62)
    print("БЕКТЕСТ real_trades.json  (записей:", len(trades), ")")
    print("=" * 62)

    s = stats(trades)
    print(f"\n[РЕАЛЬНЫЙ РЕЗУЛЬТАТ]")
    print(f"  Сделок:        {s['count']}")
    print(f"  Общий P&L:     ${s['total_pnl']:+.2f}")
    print(f"  Винрейт:       {s['win_rate']}%  ({s['wins']}勝 / {s['losses']}п)")
    print(f"  Средн. выигрыш: ${s['avg_win']:+.2f}")
    print(f"  Средн. убыток:  ${s['avg_loss']:+.2f}")
    print(f"  Лучшая:        ${s['best']:+.2f}")
    print(f"  Худшая:        ${s['worst']:+.2f}")
    if s["profit_factor"]:
        print(f"  Профит-фактор:  {s['profit_factor']}")

    print(f"\n[УБЫТКИ ПО ПРИЧИНЕ ВЫХОДА (exit_reason)]")
    print(f"  {'причина':<30} {'сделок':>7} {'сумма P&L':>12} {'ср/сделка':>11}")
    for reason, n, total, avg in by_reason(trades):
        mark = "  ← убыточная" if total < 0 else ""
        print(f"  {reason:<30} {n:>7} {total:>+12.2f} {avg:>+11.2f}{mark}")

    # Симуляция: если бы мы отрезали на входе самые убыточные сценарии
    # (аналог того, что новые гейты не пропустили бы вход).
    worst_reasons = [r for r, _, total, _ in by_reason(trades) if total < 0][:4]
    kept = simulate_filter(trades, worst_reasons)
    s2 = stats(kept)

    print(f"\n[ЧТО ЕСЛИ: отсечь на входе 4 самых убыточных exit_reason]")
    print(f"  Отсечённые причины: {worst_reasons}")
    print(f"  Оставшихся сделок:  {s2['count']}")
    print(f"  P&L был бы:         ${s2['total_pnl']:+.2f}  (было ${s['total_pnl']:+.2f})")
    print(f"  Винрейт:            {s2['win_rate']}%")
    print(f"  Профит-фактор:      {s2['profit_factor']}")
    delta = s2["total_pnl"] - s["total_pnl"]
    print(f"  Эффект:             ${delta:+.2f}")

    print(f"\n[10 ХУДШИХ СДЕЛОК]")
    worst = sorted(trades, key=lambda t: t["pnl"])[:10]
    for t in worst:
        print(f"  {t['entry_time'][:16]}  {t['mint'][:12]}...  ${t['pnl']:+.2f}  {t['exit_reason']}  src={t['source']}")

    print(f"\n[10 ЛУЧШИХ СДЕЛОК]")
    best = sorted(trades, key=lambda t: t["pnl"], reverse=True)[:10]
    for t in best:
        print(f"  {t['entry_time'][:16]}  {t['mint'][:12]}...  ${t['pnl']:+.2f}  {t['exit_reason']}  src={t['source']}")

    print("\nГотово. Это структурный бектест по истории — без повторного прогоняния analyzer.")


if __name__ == "__main__":
    main()
