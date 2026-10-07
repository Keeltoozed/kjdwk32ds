"""EARLY SCOUT: вход по собственному потоку сделок PumpPortal (секунды),
а не по DexScreener (лаг минуты). Малый билет, быстрый кат, широкий трейлинг.

РЕЖИМЫ:
  EARLY_SHADOW = True  (по умолчанию) — деньги НЕ тратятся: каждый виртуальный вход
      ведётся по тем же правилам выхода и пишется в early_shadow.jsonl.
      Отчёт: python early_scout.py report
  EARLY_SHADOW = False — реальные (paper) позиции через tracker, source "EARLY:...".
      position_manager должен пропускать source, начинающийся с "EARLY".
"""
import asyncio
import json
import time
from types import SimpleNamespace

try:
    import config
except Exception:  # отчёт можно запускать и без проекта
    config = None


def _w(name, default):
    return getattr(config, name, default) if config else default


class _State:
    def __init__(self, creator, sym, px):
        self.t0 = time.time()
        self.creator = creator
        self.sym = sym or "?"
        self.first_px = px
        self.px = px
        self.hist = []  # (ts, px)
        self.buyers = set()
        self.by_buyer = {}
        self.buy_sol = 0.0
        self.sell_sol = 0.0
        self.dev_sold = False


class _SimTracker:
    """Виртуальный трекер без денег: итог каждого входа пишет в JSONL."""

    def __init__(self, path):
        self.path = path
        self.positions = {}
        self.meta = {}

    def get_open_positions(self):
        return {m: p for m, p in self.positions.items() if p.status == "open"}

    def add_position(self, sym, mint, entry, size, **kw):
        self.positions[mint] = SimpleNamespace(status="open", source="EARLY", entry=entry,
                                               remaining=1.0, realized=0.0)

    def partial_close_position(self, mint, price, frac, reason):
        p = self.positions.get(mint)
        if not p or p.status != "open":
            return
        part = p.remaining * frac
        p.realized += part * (price / p.entry - 1)
        p.remaining -= part

    def close_position(self, mint, price, reason):
        p = self.positions.get(mint)
        if not p or p.status != "open":
            return
        p.realized += p.remaining * (price / p.entry - 1)
        p.remaining = 0.0
        p.status = "closed"
        m = self.meta.get(mint, {})
        row = {"ts": int(time.time()), "sym": m.get("sym", "?"), "mint": mint,
               "pnl_pct": round(p.realized, 4),
               "peak_pct": round(m.get("peak", p.entry) / p.entry - 1, 4),
               "held_s": int(time.time() - m.get("t0", time.time())),
               "reason": reason, "buyers": m.get("buyers", 0),
               "buy_sol": round(m.get("buy_sol", 0.0), 1)}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
        print(f"👻 EARLY shadow {row['sym']}: {row['pnl_pct'] * 100:+.0f}% "
              f"(пик {row['peak_pct'] * 100:+.0f}%) — {reason}")


def _px_usd(m, sol_usd):
    vs = float(m.get("vSolInBondingCurve") or 0)
    vt = float(m.get("vTokensInBondingCurve") or 0)
    return (vs / vt) * sol_usd if vs > 0 and vt > 0 else 0.0


def _entry_ok(st, now):
    """Органический ранний импульс: много РАЗНЫХ покупателей, перевес покупок,
    дев не продал, нет одного кита, цена растёт, но ещё не вертикаль."""
    if now - st.t0 < _w("EARLY_MIN_AGE_SEC", 20) or st.dev_sold or st.first_px <= 0:
        return False
    if len(st.buyers) < _w("EARLY_MIN_BUYERS", 12) or st.buy_sol < _w("EARLY_MIN_BUY_SOL", 6.0):
        return False
    if st.buy_sol / (st.sell_sol + 0.01) < _w("EARLY_MIN_BS_RATIO", 2.5):
        return False
    if max(st.by_buyer.values()) / st.buy_sol > _w("EARLY_MAX_TOP_BUYER", 0.30):
        return False
    gain = st.px / st.first_px - 1
    if not (_w("EARLY_MIN_GAIN", 0.10) <= gain <= _w("EARLY_MAX_GAIN", 1.5)):
        return False
    old = [p for t, p in st.hist if now - t >= 15]
    return bool(old) and st.px >= old[-1]  # не разворачивается


def _manage(tracker, mint, h, px, now, slip):
    """Выходы билета. True = позиция закрыта."""
    h["peak"] = max(h["peak"], px)
    pnl = px / h["entry"] - 1
    peak = h["peak"] / h["entry"] - 1
    age = now - h["t0"]
    out = px * (1 - slip)
    if peak >= 0.50 and not h["tp1"]:
        h["tp1"] = True
        tracker.partial_close_position(mint, out, 0.50, "EARLY Take Profit +50% (Risk Free)")
    reason = None
    if pnl <= -0.20:
        reason = f"EARLY Stop ({pnl * 100:.0f}%)"
    elif age >= 90 and peak < 0.10:
        reason = f"EARLY No Follow-through ({age:.0f}s)"
    elif peak >= 0.30 and (h["peak"] - px) / h["peak"] >= (0.50 if h["tp1"] else 0.35):
        reason = f"EARLY Trailing (peak +{peak * 100:.0f}%)"
    elif age >= 1800 and pnl < 0.10:
        reason = "EARLY Stagnant (30m)"
    elif now - h["last_ts"] > 120:
        reason = "EARLY Silent/Migrated"
    if reason:
        tracker.close_position(mint, out, reason)
        return True
    return False


async def early_scout_loop(analyzer, tracker):
    if not _w("EARLY_SCOUT_ENABLED", False):
        print("⚡ EARLY SCOUT выкл (EARLY_SCOUT_ENABLED=False).")
        return
    import websockets
    from sol_price import get_sol_price_sync

    shadow = bool(_w("EARLY_SHADOW", True))
    trk = _SimTracker(_w("EARLY_SHADOW_FILE", "early_shadow.jsonl")) if shadow else tracker
    max_early = _w("EARLY_SHADOW_MAX_POS", 30) if shadow else _w("EARLY_MAX_POS", 3)
    states, held = {}, {}
    slip = float(_w("EARLY_SLIPPAGE", 0.03))  # честность paper: вход дороже, выход дешевле
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
               "Origin": "https://pumpportal.fun"}
    print(f"⚡ EARLY SCOUT запущен ({'SHADOW — без денег' if shadow else 'PAPER-позиции'}).")

    while True:
        try:
            async with websockets.connect(config.PUMPPORTAL_WSS, extra_headers=headers, ping_interval=None, ping_timeout=None) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                keys = list(states) + list(held)
                if keys:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": keys}))

                async def unsub(mint):
                    try:
                        await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))
                    except Exception:
                        pass

                last_sweep = 0.0
                while True:
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                    except asyncio.TimeoutError:
                        m = None
                    now = time.time()

                    if m and m.get("mint"):
                        mint, tx, who = m["mint"], m.get("txType"), m.get("traderPublicKey")
                        sol = float(m.get("solAmount") or 0)
                        px = _px_usd(m, get_sol_price_sync())

                        if tx == "create":
                            states[mint] = _State(who, m.get("symbol"), px)
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

                        elif mint in held and px > 0:
                            h = held[mint]
                            h["last_px"], h["last_ts"] = px, now
                            if _manage(trk, mint, h, px, now, slip):
                                held.pop(mint, None)
                                await unsub(mint)

                        elif mint in states and tx in ("buy", "sell") and px > 0:
                            st = states[mint]
                            st.px = px
                            st.hist.append((now, px))
                            st.hist = [(t, p) for t, p in st.hist if now - t < 60]
                            if tx == "buy":
                                st.buyers.add(who)
                                st.by_buyer[who] = st.by_buyer.get(who, 0.0) + sol
                                st.buy_sol += sol
                            else:
                                st.sell_sol += sol
                                if who == st.creator:
                                    st.dev_sold = True
                            n_early = sum(1 for p in trk.get_open_positions().values()
                                          if str(getattr(p, "source", "")).startswith("EARLY"))
                            slots_ok = shadow or len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS
                            if (tx == "buy" and mint not in trk.positions and n_early < max_early
                                    and slots_ok and _entry_ok(st, now)):
                                entry = px * (1 + slip)
                                trk.add_position(st.sym, mint, entry, float(_w("EARLY_SIZE_USD", 8.0)),
                                                 is_mature=False,
                                                 source=f"EARLY:{len(st.buyers)}b/{st.buy_sol:.0f}sol")
                                if mint in trk.positions:
                                    held[mint] = {"entry": entry, "peak": entry, "t0": now, "tp1": False,
                                                  "last_px": px, "last_ts": now, "sym": st.sym,
                                                  "buyers": len(st.buyers), "buy_sol": st.buy_sol}
                                    if shadow:
                                        trk.meta[mint] = held[mint]
                                    states.pop(mint, None)
                                    print(f"⚡ EARLY {'SHADOW ' if shadow else ''}BUY {st.sym} {mint[:8]} "
                                          f"@ ${entry:.8f} ({len(st.buyers)} покупателей, {st.buy_sol:.1f} SOL)")

                    # Уборка: протухшие кандидаты + тайм-выходы тихих позиций
                    if now - last_sweep >= 2:
                        last_sweep = now
                        for mint, st in list(states.items()):
                            if now - st.t0 > _w("EARLY_MAX_AGE_SEC", 150):
                                states.pop(mint, None)
                                await unsub(mint)
                        for mint, h in list(held.items()):
                            p = trk.positions.get(mint)
                            if p is None or getattr(p, "status", "open") != "open":
                                held.pop(mint, None)
                                await unsub(mint)
                            elif _manage(trk, mint, h, h["last_px"], now, slip):
                                held.pop(mint, None)
                                await unsub(mint)
        except Exception as e:
            print(f"⚡ EARLY SCOUT ошибка: {type(e).__name__}: {e}. Переподключение через 5с.")
            await asyncio.sleep(5)


def _report(path="early_shadow.jsonl"):
    import os
    if not os.path.exists(path):
        print("Данных нет: включи EARLY_SCOUT_ENABLED и дай поработать.")
        return
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    n = len(rows)
    if not n:
        print("Файл пуст.")
        return
    g = [r["pnl_pct"] for r in rows]
    print(f"Виртуальных входов: {n}")
    print(f"В плюсе: {sum(1 for x in g if x > 0) * 100 // n}%, "
          f"средний результат: {sum(g) / n * 100:+.1f}% на сделку (после 3% проскальзывания)")
    print(f"Пик +50% и выше: {sum(1 for r in rows if r['peak_pct'] >= 0.5)}, "
          f"пик +100% и выше: {sum(1 for r in rows if r['peak_pct'] >= 1.0)}, "
          f"пик +300% и выше: {sum(1 for r in rows if r['peak_pct'] >= 3.0)}")
    print("Итог в $ с фикс. комиссией ($0.075 за сторону до $10, $0.45 от $10):")
    for size in (3, 8, 15):
        fee = 2 * (0.075 if size < 10 else 0.45)
        net = sum(size * x - fee for x in g)
        print(f"  билет ${size:<2}: {net:+.2f}$ всего, {net / n:+.3f}$ на сделку")
    print("Лучшие по пику:")
    for r in sorted(rows, key=lambda r: -r["peak_pct"])[:5]:
        print(f"  {r['sym'][:10]:<10} пик {r['peak_pct'] * 100:+.0f}% итог {r['pnl_pct'] * 100:+.0f}% ({r['reason'][:28]})")
    reasons = {}
    for r in rows:
        k = r["reason"].split("(")[0].strip()
        reasons[k] = reasons.get(k, 0) + 1
    print("Причины выхода:", ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        _report(sys.argv[2] if len(sys.argv) > 2 else "early_shadow.jsonl")
    else:
        print("Использование: python early_scout.py report [early_shadow.jsonl]")
