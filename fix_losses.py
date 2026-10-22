#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_losses.py — патч по РЕАЛЬНЫМ сделкам (68 шт. из trades_raydium_rows, 21-22 окт).

Что показали данные (pnl = % от билета):
  * Solana: 45 сделок, -394. Из них 4 рага по -98% дали -372. Без них Solana ~ 0 (-0.5/сделка, 29% win).
  * Все раги: пулы pumpswap возрастом 14-77 мин, 3 из 4 прошли RugCheck/GoPlus. Токены 10-30 мин: 0 побед из 7 (-308).
  * Токены 2-24ч: 62% побед (+195). pumpfun-кривая (стрим): 0/5. meteora: 0/6.
  * Скор модели >=90: -319 (17 сделок) — модель не отбирает, а скорее наоборот.
  * b/s > 2.5 (односторонний стакан): -212, 17% побед.
  * EVM (Base/BSC): +188, win 52% — рабочая часть бота, её не трогаем.

Патч (всё включается/выключается через config.py, значения по умолчанию указаны):
  1. SOL_MIN_AGE_GATE_MIN = 30   — AMM-токены Solana моложе 30 мин не покупаем (кривая pump.fun не затронута).
  2. SOL_BLOCK_DEXES = ("meteora",) — пулы этих DEX не покупаем.
  3. SOL_MAX_BS_RATIO = 2.5      — buy/sell m5 выше порога = накрутка, отказ (0 = выкл).
  4. SOL_MAX_SIZE_USD = 15.0     — потолок билета Solana-сканера ($15, 0 = выкл).
Отказы идут в воронку/shadow-лог (если стоит fix_rockets.py), так что видно, сколько хороших токенов гейт отрезал.

Запуск: python fix_losses.py analyzer.py main.py   (после fix_rockets.py; работает и без него)
Идемпотентно, атомарно (ошибка якоря/синтаксиса = ничего не записано), бэкапы *.bak_losses
"""
import re
import shutil
import sys

A_PATH = sys.argv[1] if len(sys.argv) > 1 else "analyzer.py"
M_PATH = sys.argv[2] if len(sys.argv) > 2 else "main.py"


def fail(msg):
    raise SystemExit("❌ " + msg + " — патч НЕ применён, файлы не тронуты")


def sub1(text, pattern, repl_fn, name, flags=re.M):
    ms = list(re.finditer(pattern, text, flags))
    if len(ms) != 1:
        fail(f"якорь '{name}': найдено {len(ms)} совпадений (нужно ровно 1)")
    m = ms[0]
    return text[:m.start()] + repl_fn(m) + text[m.end():]


def ind(block, pad):
    return "\n".join((pad + ln if ln.strip() else ln) for ln in block.strip("\n").split("\n"))


GATE_BLOCK = '''
# === SOL-GATES (по реальным сделкам): возраст AMM-пула, запрещённые DEX ===
_dex_g = str(pair_data.get("dexId", "")).lower()
if _dex_g not in ("pump", "pumpfun") and not mint.endswith("pump"):
    _gate_min = float(getattr(config, "SOL_MIN_AGE_GATE_MIN", 3.0))
    _blk = tuple(str(x).lower() for x in (getattr(config, "SOL_BLOCK_DEXES", ()) or ()))
    _gmsg = None
    if _dex_g in _blk:
        _gmsg = f"🚫 [DEX-BLOCK] {mint[:8]}: пул {_dex_g} в чёрном списке (0 побед в истории)."
    elif _gate_min > 0 and _age_min_pre < _gate_min:
        _gmsg = f"🚫 [AGE-GATE] {mint[:8]}: возраст {_age_min_pre:.0f}м < {_gate_min:.0f}м — рага-зона AMM-токенов, ждём."
    if _gmsg:
        if callable(getattr(self, "_rej", None)):
            return self._rej(mint, _gmsg)
        print(_gmsg)
        return False
'''

BS_BLOCK = '''
# === BS-CAP (по реальным сделкам): b/s m5 > 2.5 = накрутка/кабал, 17% побед ===
_bs_cap = float(getattr(config, "SOL_MAX_BS_RATIO", 2.5))
if _bs_cap > 0 and _s5 > 0 and (_b5 / _s5) > _bs_cap:
    _bmsg = f"🚫 [BS-CAP] {mint[:8]}: b/s {_b5}/{_s5} = {_b5 / _s5:.1f}x > {_bs_cap:.1f}x — односторонний стакан (накрутка)."
    if callable(getattr(self, "_rej", None)):
        return self._rej(mint, _bmsg)
    print(_bmsg)
    return False
'''

SIZE_BLOCK = '''
_sol_cap = float(getattr(config, "SOL_MAX_SIZE_USD", 15.0) or 0.0)
if _sol_cap > 0 and position_size > _sol_cap:
    position_size = _sol_cap
'''


def patch_analyzer(t):
    if "SOL_MIN_AGE_GATE_MIN" in t:
        print("ℹ️ analyzer.py: уже пропатчен — пропуск")
        return t
    # 1. возраст/dex-гейт сразу после проверки минимального возраста в analyze_token
    t = sub1(t,
             r'^(?P<i>[ \t]+)if _age_min_pre < _sol_min_age:[ \t]*\n[^\n]*\n(?P=i)[ \t]+return None[ \t]*\n',
             lambda m: m.group(0) + ind(GATE_BLOCK, m.group("i")) + "\n",
             "age-min block")
    # 2. b/s cap в Raydium-ветке (перед защитой от лавины продаж)
    t = sub1(t,
             r'^(?P<i>[ \t]+)# Защита от реальной лавины продаж[^\n]*\n',
             lambda m: ind(BS_BLOCK, m.group("i")) + "\n" + m.group(0),
             "avalanche anchor")
    return t


def patch_main(t):
    if "SOL_MAX_SIZE_USD" in t:
        print("ℹ️ main.py: уже пропатчен — пропуск")
        return t
    t = sub1(t,
             r'^(?P<i>[ \t]+)if position_size < \(1\.0 if \(_is_lot or _is_vipsig\) else 4\.0\):',
             lambda m: ind(SIZE_BLOCK, m.group("i")) + "\n" + m.group(0),
             "position_size min")
    return t


def main():
    with open(A_PATH, encoding="utf-8") as f:
        a_old = f.read()
    with open(M_PATH, encoding="utf-8") as f:
        m_old = f.read()
    a_new, m_new = patch_analyzer(a_old), patch_main(m_old)
    for name, src in ((A_PATH, a_new), (M_PATH, m_new)):
        try:
            compile(src, name, "exec")
        except SyntaxError as e:
            fail(f"после патча {name} не компилируется: {e}")
    for path, old, new in ((A_PATH, a_old, a_new), (M_PATH, m_old, m_new)):
        if new != old:
            shutil.copy(path, path + ".bak_losses")
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            print(f"✅ {path} пропатчен (бэкап {path}.bak_losses)")


if __name__ == "__main__":
    main()
