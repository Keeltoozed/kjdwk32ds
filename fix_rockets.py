#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_rockets.py — патч analyzer.py и main.py (Solana-ветка), чтобы бот видел ракеты
и чтобы было видно, КАКОЙ фильтр режет токены.

Запуск (из папки с файлами):
    python fix_rockets.py analyzer.py main.py

Что делает:
  1. ВОРОНКА: все Solana-отказы (print + return False) в analyze_token / analyze_token_xgboost /
     analyze_token_raydium / verify_1m_candles_not_dumping идут через self._rej():
     счётчик причин, сводка в лог раз в 10 мин ("📊 [ВОРОНКА-SOL 10м]"), shadow-лог в Supabase
     (не чаще раза в 5 мин на токен). Раньше для Solana shadow_tracker не видел ни одной причины.
  2. CURVE-RUNNER: ракеты на бондинг-кривой pump.fun (liq = fdv*0.4 <= ~13k) больше не режутся как
     LOTTERY-BLOCKED (порог runner liq>=20k был для них недостижим). Вход маленьким билетом
     (CURVE_RUNNER_SIZE_USD, по умолчанию $4) при перевесе покупателей и FDV до 26k (есть место до миграции).
     Для такого входа не применяются потолок m5<=250% и анти-вершинные 1м-фильтры
     (OVERHEAT / BLOWOFF / 5M-EXHAUSTION) — у ракеты они срабатывают по определению.
     Фильтры на дамп (1M-DUMP, 2-RED, WICK-DUMP, SPIKE-REVERSAL, BLOWOFF-TOP) остаются.
  3. СОЦСЕТИ: у свежих токенов (<90 мин) с сильным потоком покупок не требуем соцсети DexScreener
     (профиль на DS платный, у свежих ракет его почти никогда нет). RugCheck/GoPlus/холдеры остаются.
  4. main.py: билет CURVE-RUNNER ограничен по сайзу; молодые токены (<=30 мин) перепроверяются
     через 45с вместо 180с (ракета стартует неожиданно, кулдаун 3 мин её пропускал).

Все новые пороги — через getattr(config, ..., default): config.py менять не обязательно.
Патч идемпотентен и атомарен: если хоть один якорь не найден — ничего не пишется.
Бэкапы: <файл>.bak_rockets
"""
import re
import shutil
import sys

A_PATH = sys.argv[1] if len(sys.argv) > 1 else "analyzer.py"
M_PATH = sys.argv[2] if len(sys.argv) > 2 else "main.py"


def fail(msg):
    raise SystemExit("❌ " + msg + " — патч НЕ применён, файлы не тронуты")


def sub1(text, pattern, repl_fn, name):
    ms = list(re.finditer(pattern, text, re.M))
    if len(ms) != 1:
        fail(f"якорь '{name}': найдено {len(ms)} совпадений (нужно ровно 1)")
    m = ms[0]
    return text[:m.start()] + repl_fn(m) + text[m.end():]


def convert_rejections(text, fname, keyvar):
    """print(msg); return False  ->  return self._rej(key, msg) внутри одной функции."""
    m = re.search(r"^    (?:async )?def " + fname + r"\(", text, re.M)
    if not m:
        fail(f"функция {fname} не найдена")
    start = m.start()
    nxt = re.compile(r"^    (?:async def |def |@)", re.M).search(text, m.end())
    end = nxt.start() if nxt else len(text)
    body = text[start:end]
    rx = re.compile(r"^(?P<ind>[ \t]+)print\((?P<msg>.+)\)[ \t]*\n(?P=ind)return False[ \t]*$", re.M)
    new_body, n = rx.subn(lambda mm: f"{mm.group('ind')}return self._rej({keyvar}, {mm.group('msg')})", body)
    return text[:start] + new_body + text[end:], n


def indent_block(ind, block):
    return "\n".join((ind + ln if ln.strip() else ln) for ln in block.strip("\n").split("\n"))


REJ_METHOD = '''    def _rej(self, key: str, msg: str) -> bool:
        """Отказ Solana-входа: печать + воронка причин (сводка раз в 10 мин в лог) +
        shadow-лог через _deny (не чаще раза в 5 мин на токен). Никогда не бросает исключений."""
        try:
            import re as _re
            _m = _re.search(r"\\[([^\\]]+)\\]", msg)
            _head = _m.group(1) if _m else ""
            _rest = msg[_m.end():] if _m else msg
            _words = _re.findall(r"[А-Яа-яЁё]{2,}", _rest)[:4]
            tag = (_head + " " + " ".join(_words)).strip()[:48] or "misc"
            _now = time.time()
            _f = getattr(self, "_funnel", None)
            if _f is None:
                _f = self._funnel = {}
                self._funnel_ts = _now
            _f[tag] = _f.get(tag, 0) + 1
            if _now - getattr(self, "_funnel_ts", _now) >= 600.0:
                _top = sorted(_f.items(), key=lambda kv: kv[1], reverse=True)[:12]
                print(f"📊 [ВОРОНКА-SOL 10м] отказов {sum(_f.values())}: " + " | ".join(f"{k}={v}" for k, v in _top))
                _f.clear()
                self._funnel_ts = _now
            _seen = getattr(self, "_rej_seen", None)
            if _seen is None:
                _seen = self._rej_seen = {}
            if len(_seen) > 3000:
                _seen.clear()
            if _now - _seen.get(key, 0.0) >= 300.0:
                _seen[key] = _now
                return self._deny(key, tag, msg)
            print(msg)
            self._set_sig(key, f"SKIP {tag}")
        except Exception:
            try:
                print(msg)
            except Exception:
                pass
        return False

'''

CTX_BLOCK = '''
try:
    if not hasattr(self, "_eval_ctx"):
        self._eval_ctx = {}
    if len(self._eval_ctx) > 3000:
        self._eval_ctx.clear()
    self._eval_ctx[mint] = {
        "symbol": symbol or mint[:6], "chain": "solana",
        "price": float(pair_data.get("priceUsd", 0) or 0),
        "liq": float((pair_data.get("liquidity") or {}).get("usd", 0) or 0),
        "m5": float((pair_data.get("priceChange") or {}).get("m5", 0) or 0),
        "source": "SCANNER", "score": 0.0,
    }
except Exception:
    pass
'''

CURVE_BLOCK = '''
# === CURVE-RUNNER: ракеты на бондинг-кривой pump.fun ===
# На кривой liq = fdv*0.4 (<= ~$13k): порог runner (liq>=20k, vol5>=15k) для неё недостижим,
# а лотерея выключена -> любой m5>=60% на кривой резался как LOTTERY-BLOCKED.
# Пускаем маленьким билетом при реальном перевесе покупателей и месте до миграции.
_curve_runner = False
if (not _is_runner and getattr(config, "CURVE_RUNNER_ENABLED", True)
        and str(pair_data.get("dexId", "")).lower() in ("pump", "pumpfun")):
    _fdv_cr = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
    if (float(getattr(config, "CURVE_RUNNER_MIN_FDV", 9000.0)) <= _fdv_cr <= float(getattr(config, "CURVE_RUNNER_MAX_FDV", 26000.0))
            and _vm5_pre >= float(getattr(config, "CURVE_RUNNER_MIN_VOL5", 6000.0))
            and (_b5_pre + _s5_pre) >= int(getattr(config, "CURVE_RUNNER_MIN_TRADES", 25))
            and _s5_pre > 0 and _b5_pre >= _s5_pre * float(getattr(config, "CURVE_RUNNER_MIN_BS", 1.3))):
        _curve_runner = True
        _is_runner = True
        pair_data["_curve_runner"] = True
'''

FLOW_BLOCK = '''
_flow_ok_soc = bool(
    getattr(config, "SOCIALS_RELAX_FRESH", True)
    and _age_min_pre < 90.0 and _s5 > 0 and _b5 >= _s5 * 1.3
    and (_b5 + _s5) >= 25
    and float((pair_data.get("volume") or {}).get("m5", 0) or 0) >= 5000.0
)
'''

MAIN_SIZE_BLOCK = '''
_sig_cr = str(analyzer.get_signal(mint) or "")
if "RUNNER-" in _sig_cr:
    position_size = min(position_size, float(getattr(config, "CURVE_RUNNER_SIZE_USD", 4.0)))
    _is_lot = True
'''

MAIN_CD_BLOCK = '''
if mint in _graduated_set:
    _cd = 60
else:
    _cd = int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 180))
    try:
        _pc_cd = getattr(analyzer, "_prefetch_cache", {}).get(mint, (0, {}))[1] or {}
        _ca_cd = _pc_cd.get("pairCreatedAt") or 0
        if _ca_cd and (time.time() * 1000 - _ca_cd) / 60000.0 <= 30.0:
            _cd = min(_cd, int(getattr(config, "SOLANA_YOUNG_RESCAN_COOLDOWN", 45)))
    except Exception:
        pass
'''


def patch_analyzer(t):
    if "def _rej(" in t:
        print("ℹ️ analyzer.py: уже пропатчен — пропуск")
        return t
    # 1. метод _rej
    t = sub1(t, r"^    def _set_sig\(self, key: str, text: str\):",
             lambda m: REJ_METHOD + m.group(0), "_set_sig")
    # 2. отказы -> _rej
    total = 0
    for fname, key in (("analyze_token", "mint"), ("analyze_token_xgboost", "mint"),
                       ("analyze_token_raydium", "mint"), ("verify_1m_candles_not_dumping", "address")):
        t, n = convert_rejections(t, fname, key)
        print(f"   {fname}: отказов переведено в воронку: {n}")
        total += n
    if total < 20:
        fail(f"переведено подозрительно мало отказов ({total}) — формат файла отличается")
    # 3. контекст для shadow-лога
    t = sub1(t, r'^([ \t]+)symbol = base_token\.get\("symbol", ""\)\.upper\(\)[ \t]*$',
             lambda m: m.group(0) + "\n" + indent_block(m.group(1), CTX_BLOCK), "symbol/base_token")
    # 4. инициализация флага соцсетей
    t = sub1(t, r"^([ \t]+)_lottery = False[ \t]*$",
             lambda m: m.group(0) + "\n" + m.group(1) + "_flow_ok_soc = False", "_lottery = False")
    # 5. CURVE-RUNNER
    t = sub1(t, r"(?P<ind>[ \t]+)_is_runner = \(\s*_cur_liq_pre >= 20000 and\s+_vm5_pre >= 15000 and\s+"
                r"\(_b5_pre \+ _s5_pre\) >= 15 and\s+\(_b5_pre >= _s5_pre \* 1\.05 or _vm5_pre >= 35000\)\s*\)",
             lambda m: m.group(0) + "\n" + indent_block(m.group("ind"), CURVE_BLOCK), "_is_runner")
    t = sub1(t, r"^([ \t]+)if _m5 > 250\.0:[ \t]*$",
             lambda m: m.group(1) + "if _m5 > 250.0 and not _curve_runner:", "RUNNER-EXHAUSTION")
    # 6. соцсети
    t = sub1(t, r'^([ \t]+)_socials = \(pair_data\.get\("info"\) or \{\}\)\.get\("socials"\) or \[\][ \t]*$',
             lambda m: indent_block(m.group(1), FLOW_BLOCK) + "\n" + m.group(0), "_socials")
    t = sub1(t, r"and len\(_socials\) == 0 and not _lottery and _cur_liq < 25000:",
             lambda m: "and len(_socials) == 0 and not _lottery and not _flow_ok_soc and _cur_liq < 25000:", "SOCIAL-early")
    t = sub1(t, r"^([ \t]+)if not _socials_unknown and not has_socials and liquidity < 100000:[ \t]*$",
             lambda m: m.group(1) + "if not _socials_unknown and not has_socials and liquidity < 100000 and not _flow_ok_soc:",
             "SOCIAL-late")
    # 7. метка сигнала
    t = sub1(t, r"""_sig_name = f"\{'VIP-' if is_vip else ''\}\{'LOTTERY-' if _lottery else ''\}PUMP-XGB \{conf:\.0f\}%\"""",
             lambda m: """_sig_name = f"{'RUNNER-' if pair_data.get('_curve_runner') else ''}{'VIP-' if is_vip else ''}{'LOTTERY-' if _lottery else ''}PUMP-XGB {conf:.0f}%\"""",
             "_sig_name")
    # 8. 1м-фильтры: не применяем анти-вершинные к CURVE-RUNNER
    t = sub1(t, r'^([ \t]+)symbol = \(\(pair_data\.get\("baseToken"\) or \{\}\)\.get\("symbol"\) or address\[:8\]\)[ \t]*$',
             lambda m: m.group(0) + "\n" + m.group(1) + '_cr = bool(pair_data.get("_curve_runner"))', "verify_1m symbol")
    t = sub1(t, r"^([ \t]+)if c0_pct > max_m1_overheat:[ \t]*$",
             lambda m: m.group(1) + "if c0_pct > max_m1_overheat and not _cr:", "1M-OVERHEAT")
    t = sub1(t, r"^([ \t]+)if c1_pct > 60\.0 or \(c0_pct \+ c1_pct\) > 75\.0:[ \t]*$",
             lambda m: m.group(1) + "if (c1_pct > 60.0 or (c0_pct + c1_pct) > 75.0) and not _cr:", "1M-BLOWOFF")
    t = sub1(t, r"^([ \t]+)if low_5m > 0 and \(high_5m / low_5m\) >= 3\.8:[ \t]*$",
             lambda m: m.group(1) + "if low_5m > 0 and (high_5m / low_5m) >= 3.8 and not _cr:", "5M-EXHAUSTION")
    return t


def patch_main(t):
    if "CURVE_RUNNER_SIZE_USD" in t:
        print("ℹ️ main.py: уже пропатчен — пропуск")
        return t
    t = sub1(t, r"^([ \t]+)if position_size < \(1\.0 if \(_is_lot or _is_vipsig\) else 4\.0\):",
             lambda m: indent_block(m.group(1), MAIN_SIZE_BLOCK) + "\n" + m.group(0), "position_size min")
    t = sub1(t, r'^([ \t]+)_cd = 60 if mint in _graduated_set else int\(getattr\(config, "SOLANA_RESCAN_COOLDOWN", 180\)\)[ \t]*$',
             lambda m: indent_block(m.group(1), MAIN_CD_BLOCK), "_cd cooldown")
    return t


def main():
    with open(A_PATH, encoding="utf-8") as f:
        a_old = f.read()
    with open(M_PATH, encoding="utf-8") as f:
        m_old = f.read()
    a_new = patch_analyzer(a_old)
    m_new = patch_main(m_old)
    # проверка синтаксиса ДО записи
    for name, src in ((A_PATH, a_new), (M_PATH, m_new)):
        try:
            compile(src, name, "exec")
        except SyntaxError as e:
            fail(f"после патча {name} не компилируется: {e}")
    if a_new != a_old:
        shutil.copy(A_PATH, A_PATH + ".bak_rockets")
        with open(A_PATH, "w", encoding="utf-8") as f:
            f.write(a_new)
        print(f"✅ {A_PATH} пропатчен (бэкап {A_PATH}.bak_rockets)")
    if m_new != m_old:
        shutil.copy(M_PATH, M_PATH + ".bak_rockets")
        with open(M_PATH, "w", encoding="utf-8") as f:
            f.write(m_new)
        print(f"✅ {M_PATH} пропатчен (бэкап {M_PATH}.bak_rockets)")


if __name__ == "__main__":
    main()
