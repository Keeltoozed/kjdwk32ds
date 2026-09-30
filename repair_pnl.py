"""Ремонт фантомного PnL (NUTFLEX +$1.6M при +14.85%).

Dry-run по умолчанию: показывает битые сделки.
С --apply: переносит битые в карантинные ключи BAD_<mint> (из тотала/капитала
они исключаются и кодом, но карантин чистит историю визуально), чинит
original_amount_usd у нормальных сделок.

Битый = stored pnl не бьётся с ценовым движением в разы (см. _is_bad):
обычно это перепутанные единицы цены (entry/exit из разных источников) или
повреждённый amount_usd (11M вместо $4-100).
"""
import json
import sys

import config


def _is_bad(v: dict) -> tuple:
    try:
        e = float(v.get("entry_price_usd") or 0)
        x = float(v.get("exit_price_usd") or 0)
        p = float(v.get("pnl_usd") or 0)
        a = float(v.get("original_amount_usd") or v.get("amount_usd") or 0)
        if not e or a <= 0 or a > 1000:
            return (abs(p) > 100, f"entry={e} amount={a} pnl={p}")
        move = abs(x / e - 1) if e > 0 and x >= 0 else 1.0
        exp_max = a * (move + 0.05) + 2.0
        bad = abs(p) > exp_max * 3 and abs(p) > 50
        return (bad, f"move={move*100:.1f}% amt={a:.2f} pnl={p:.2f} exp_max={exp_max:.2f}")
    except Exception as ex:
        return (False, str(ex))


def _fix_original(v: dict) -> bool:
    if v.get("original_amount_usd"):
        return False
    rem = float(v.get("amount_usd", 0) or 0)
    tp1 = bool(v.get("tp1_done", False))
    moon = bool(v.get("is_moonbag", False))
    if tp1 and moon:
        v["original_amount_usd"] = rem / 0.4 if rem else rem
    elif moon:
        v["original_amount_usd"] = rem / 0.5 if rem else rem
    elif tp1:
        v["original_amount_usd"] = rem / 0.8 if rem else rem
    else:
        v["original_amount_usd"] = rem
    return True


def main():
    apply = "--apply" in sys.argv
    try:
        with open(config.PAPER_PORTFOLIO_FILE) as f:
            data = json.load(f) or {}
    except Exception as ex:
        print(f"Нет портфеля: {ex}")
        return
    bad, fixed = [], 0
    for k, v in list(data.items()):
        if not isinstance(v, dict):
            continue
        if _fix_original(v):
            fixed += 1
        if v.get("status") == "closed":
            is_bad, why = _is_bad(v)
            if is_bad:
                bad.append((k, v.get("symbol"), why, v.get("pnl_usd")))
                if apply:
                    data[f"BAD_{k}"] = dict(v, status="quarantined")
                    del data[k]
    print(f"Всего слотов: {len(data)}, битых closed: {len(bad)}, починено original_amount: {fixed}")
    for k, sym, why, pnl in bad[:30]:
        print(f"  BAD {sym} {k[:12]} pnl=${pnl} :: {why}")
    # Честный тотал без битых
    total = sum(float(v.get("pnl_usd") or 0) for v in data.values()
                if isinstance(v, dict) and v.get("status") == "closed")
    print(f"Честный total closed PnL (без карантина): ${total:.2f}")
    if bad and apply:
        with open(config.PAPER_PORTFOLIO_FILE, "w") as f:
            json.dump(data, f, indent=4)
        print(f"OK: {len(bad)} сделок в карантине BAD_* (история сохранена, тотал чистый).")
        print("Не забудь: тот же мусор лежит в Supabase PORTFOLIO_STATE_V3 — "
              "после рестарта бот перезапишет облако чистым файлом.")
    elif bad:
        print("Dry-run. Для лечения: python3 repair_pnl.py --apply")


if __name__ == "__main__":
    main()
