"""Обучение ROCKET-модели: СВОИ закрытые сделки + публичный seed.

Источники:
1. Свои сделки (Supabase PORTFOLIO_STATE_V3 → локальные файлы):
   ml_features (entry-снапшот) + entry/max_price (пик). Вес x2 — наш рынок важнее.
2. Публичный seed (rocket_seed.py: архетипы WIF/BOME/MYRO + пыль/дампы/вершины).
   Вес x1 — baseline с первого дня, пока своих сделок мало.

Метка: пик >= ROCKET_PEAK_PCT (+50%) → ракета (1), иначе 0.
Гард: мало данных (< ROCKET_MIN_TRAIN или <5 каждого класса) → отказ,
старая модель НЕ затирается. Seed в одиночку гард проходит (300 строк),
поэтому файл создаётся сразу, а свои сделки постепенно перетягивают веса.

Запуск: python3 train_rocket.py
"""
import json
import os
import sqlite3
import sys

import config
import rocket_model as rm


def _rows_from_portfolio(data: dict, src: str) -> list:
    rows = []
    for k, v in (data or {}).items():
        try:
            if not isinstance(v, dict) or v.get("status") != "closed":
                continue
            feats = v.get("ml_features") or {}
            vec = rm.features_from_stored(feats)
            if vec is None:
                continue
            entry = float(v.get("entry_price_usd") or 0)
            peak = float(v.get("max_price_usd") or v.get("exit_price_usd") or 0)
            if not (entry > 0 and peak > 0):
                continue
            peak_pct = peak / entry - 1
            rows.append((vec, peak_pct, v.get("symbol", "?"), src))
        except Exception:
            continue
    return rows


def gather() -> list:
    rows = []
    # 1. Supabase
    url, key = getattr(config, "SUPABASE_URL", None), getattr(config, "SUPABASE_KEY", None)
    if url and key:
        try:
            from supabase import create_client
            sb = create_client(url, key)
            res = sb.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE_V3").execute()
            if res.data and res.data[0].get("features"):
                data = json.loads(res.data[0]["features"])
                rows += _rows_from_portfolio(data, "supabase")
                print(f"Supabase: {len(rows)} сделок с фичами")
        except Exception as e:
            print(f"Supabase недоступен: {e}")
    # 2. Локальные файлы
    seen_fn = []
    for fn in (getattr(config, "PAPER_PORTFOLIO_FILE", "portfolio.json"), "portfolio.json",
               "paper_portfolio.json"):
        if fn in seen_fn:
            continue
        seen_fn.append(fn)
        try:
            if os.path.exists(fn):
                with open(fn) as f:
                    data = json.load(f) or {}
                before = len(rows)
                have = {r[2] + str(r[1]) for r in rows}
                for r in _rows_from_portfolio(data, fn):
                    rows.append(r)
                print(f"{fn}: +{len(rows) - before} сделок")
        except Exception as e:
            print(f"{fn}: {e}")
    # Дедуп по (symbol, peak) — облако и файл пересекаются
    seen, uniq = set(), []
    for vec, peak, sym, src in rows:
        k = (sym, round(peak, 4))
        if k not in seen:
            seen.add(k)
            uniq.append((vec, peak, sym, src))
    return uniq


def _with_seed(rows: list):
    """Домешивает публичный seed. Свои сделки идут дважды (вес x2)."""
    thr = float(getattr(config, "ROCKET_PEAK_PCT", 0.50) or 0.50)
    own_y = [1 if peak >= thr else 0 for _, peak, _, _ in rows]
    own_X = [vec for vec, _, _, _ in rows]
    X, y = own_X * 2, own_y * 2  # свои сделки весом x2 — наш рынок важнее seed
    try:
        from rocket_seed import build_dataset as _seed
        sX, sy = _seed()
        return X + sX, y + sy, len(rows)
    except Exception as e:
        print(f"🚀 ROCKET: seed недоступен ({e}) — только свои сделки.")
        return X, y, len(rows)


def retrain_rocket_quiet() -> bool:
    """Тихий ретрейн для фоновой петли (не падает, не шумит без данных)."""
    try:
        rows = gather()
        need = int(getattr(config, "ROCKET_MIN_TRAIN", 40) or 40)
        X, y, n_own = _with_seed(rows)
        if len(X) < need or sum(y) < 5 or sum(y) > len(y) - 5:
            print(f"🚀 ROCKET: данных мало ({len(X)} строк, ракет {sum(y)}) — нужно >={need} и ≥5 каждого класса.")
            return False
        m = rm.train(X, y, getattr(config, "ROCKET_MODEL_PATH", "rocket_model.json"))
        print(f"🚀 ROCKET переобучена: n={m['n']} (своих {n_own}) rockets={m['rockets']} AUC={m['auc']}")
        top = sorted(m["importance"].items(), key=lambda kv: -kv[1])[:5]
        print("   Топ-фичи:", ", ".join(f"{k}={v}" for k, v in top))
        return True
    except Exception as e:
        print(f"🚀 ROCKET ретрейн пропущен: {type(e).__name__} {e}")
        return False


def main():
    rows = gather()
    print(f"Всего сделок с фичами: {len(rows)}")
    thr = float(getattr(config, "ROCKET_PEAK_PCT", 0.50) or 0.50)
    rockets = [(s, p) for _, p, s, _ in rows if p >= thr]
    print(f"Ракет (пик >= +{thr * 100:.0f}%): {len(rockets)}: " +
          ", ".join(f"{s}+{p * 100:.0f}%" for s, p in rockets[:15]))
    ok = retrain_rocket_quiet()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
