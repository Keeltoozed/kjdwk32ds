"""Живая статистика: винрейт/средние/PF/expectancy из портфеля (файл/Supabase).

Отвечает на вопрос «какой профит в реальности»: берёт закрытые сделки,
считает общий + разбивку по источникам (TG-SIGNAL:канал, ROBINHOOD, SCANNER...),
точку безубыточности и прогноз при текущем темпе.

Запуск: python3 stats.py [--days N]
"""
import json
import sys
import time


def _load():
    try:
        import config
    except Exception:
        config = None
    cloud, local = {}, {}
    try:
        url = getattr(config, 'SUPABASE_URL', None)
        key = getattr(config, 'SUPABASE_KEY', None)
        if url and key:
            from supabase import create_client
            sb = create_client(url, key)
            res = sb.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE_V3").execute()
            if res.data and res.data[0].get("features"):
                cloud = json.loads(res.data[0]["features"]) or {}
    except Exception as e:
        print(f"Supabase: {e}")
    fn = getattr(config, 'PAPER_PORTFOLIO_FILE', 'portfolio.json') if config else 'portfolio.json'
    try:
        with open(fn) as f:
            local = json.load(f) or {}
    except Exception:
        pass
    try:
        from tracker import PaperTracker
        return PaperTracker.merge_states(cloud, local)
    except Exception:
        return cloud or local


def _src_group(source: str) -> str:
    s = (source or '').strip()
    if s.startswith('TG-SIGNAL:'):
        parts = s.split(':')
        return 'TG:' + (parts[1] if len(parts) > 2 else '?')
    for tag in ('ROBINHOOD', 'BASE', 'BSC', 'ETHEREUM', 'SCANNER', 'GROWTH',
                'COPY', 'FOMO', 'JUP', 'BIRDEYE', 'SNIPER'):
        if tag in s.upper():
            return tag
    return s[:24] if s else 'unknown'


def main():
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 0
    cutoff = time.time() - days * 86400 if days > 0 else 0
    data = _load()
    closed = [v for v in data.values()
              if isinstance(v, dict) and v.get('status') == 'closed'
              and float(v.get('exit_time') or 0) >= cutoff
              and v.get('mint') != 'PORTFOLIO_STATE_V3']
    # карантин/фантомы не считаем
    try:
        from tracker import PaperTracker as _T

        class _P:
            pass

        clean = []
        for v in closed:
            p = _P()
            for k in ('entry_price_usd', 'exit_price_usd', 'pnl_usd',
                      'amount_usd', 'original_amount_usd'):
                setattr(p, k, v.get(k, 0))
            if not _T._is_suspicious_pnl(p):
                clean.append(v)
        n_bad = len(closed) - len(clean)
        closed = clean
    except Exception:
        n_bad = 0
    if not closed:
        print('Закрытых сделок нет.')
        return
    pnls = [float(v.get('pnl_usd') or 0) for v in closed]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    n = len(pnls)
    tot = sum(pnls)
    wr = len(wins) / n
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(loss) / len(loss) if loss else 0
    be = abs(al) / (aw + abs(al)) if (aw + abs(al)) else 1
    pf = (sum(wins) / abs(sum(loss))) if loss and sum(loss) else 0
    span_d = max(1, (max(float(v.get('exit_time') or 0) for v in closed)
                     - min(float(v.get('exit_time') or 0) for v in closed)) / 86400)
    print(f"Сделок: {n} за ~{span_d:.1f}д ({n / span_d:.1f}/день)  |  фантомов отсеяно: {n_bad}")
    print(f"Винрейт: {wr:.0%}  |  средний вин ${aw:+.2f} / лосс ${al:+.2f}")
    print(f"P&L: ${tot:+.2f}  |  expectancy ${tot / n:+.2f}/сделку  |  профит-фактор {pf:.2f}")
    print(f"Безубыточность при таких средних: {be:.0%} винрейта "
          f"({'ОК above' if wr >= be else 'НЕ ХВАТАЕТ ' + str(int((be - wr) * 100)) + 'пп'})")
    print(f"Прогноз: ${tot / n * (n / span_d) * 30:+.2f}/мес при текущем темпе")
    groups = {}
    for v in closed:
        groups.setdefault(_src_group(v.get('source', '')), []).append(float(v.get('pnl_usd') or 0))
    print('По источникам:')
    for g, ps in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
        w = sum(1 for p in ps if p > 0)
        print(f"  {g}: n={len(ps)} win={w / len(ps):.0%} P&L=${sum(ps):+.2f}")


def estimate(wr: float, aw: float, al: float, tpd: float, capital: float = 120.0):
    """Быстрая оценка: вердикт + прогноз. al отрицательный.
    Значимость эвристическая: n-экв 30 сделок, край должен превышать шум."""
    al = -abs(al)
    ev = wr * aw + (1 - wr) * al
    be = abs(al) / (aw + abs(al)) if (aw + abs(al)) else 1.0
    noise = abs(al) / (30 ** 0.5) * 2
    print(f"Вход: винрейт {wr:.0%}, вин ${aw:+.2f} / лосс ${al:+.2f}, {tpd:.1f} сделок/день, депо ${capital:.0f}")
    print(f"EV: ${ev:+.2f}/сделку  |  безубыточность: {be:.0%} винрейта")
    direction = "🟢 ПЛЮС" if ev > 0 else ("🔴 МИНУС" if ev < 0 else "⚪ НОЛЬ")
    conf = "уверенно" if abs(ev) >= noise else "пока шум (мало сделок)"
    print(f"Вердикт: {direction} ({conf}; порог значимости ~${noise:.2f})")
    per_day = ev * tpd
    print(f"Темп: ${per_day:+.2f}/день → ${per_day * 30:+.2f}/мес")
    cap = capital
    line = []
    for m in range(1, 4):
        cap = max(0, cap + per_day * 30)
        line.append(f"м{m}: ${cap:.0f}")
    print("Депозит: " + " → ".join(line) + (" (линейно, без реинвеста сайза)" if ev >= 0 else " (слив)"))


if __name__ == '__main__':
    if '--estimate' in sys.argv:
        g = lambda n, d: float(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d
        estimate(g('--wr', 0.35), g('--aw', 1.49), g('--al', 1.17),
                 g('--tpd', 3.0), g('--capital', 120.0))
    else:
        main()
