# -*- coding: utf-8 -*-
"""
apply_fixes.py — Автоматический патчер v3 для репозитория Keeltoozed/kjdwk32ds.
Точечно исправляет:
1. Захват высокообъёмных ракет (PROPTRENCH +82%, JVOLT 1390b/212s), которым XGBoost занижал скор до 8-11%.
2. Запрет слепых входов GROK-JEV при m1=None и низком скоре <24% (отсекает MINNIE -24.7%).
3. Мгновенный Stop-Loss -12% с 1-го тика без проскальзывания до -24%.
4. Фиксацию TP2 на +24% и плотный трейлинг 10% от пика при росте от +20% (забирает максимум на MAYA +30.9%).
"""
import os
import re
import shutil

CONFIG_APPEND_BLOCK = """

# ==================== [AUTO-PATCH] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ v3 ====================
GROK_JEV_STANDALONE_BUY = False
GROK_JEV_MIN_CONFIDENCE = 48.0
GROK_JEV_MIN_XGB_SCORE = 24.0
GROK_JEV_MAX_AGE_MIN = 45.0
GROK_JEV_MIN_VOL_M5 = 8000.0

# 1. Объём и баланс покупателей (мягкие пороги — не отсекаем свежие ракеты):
MIN_VOLUME_M5 = 700.0
MIN_TXNS_M5 = 10
MIN_BUY_SELL_RATIO = 1.05
DEFAULT_MIN_BUY_SELL_RATIO = 1.05
HIGH_VOL_M5_THRESHOLD = 20000.0
HIGH_VOL_MIN_BUY_SELL_RATIO = 1.05

# 2. Пороги ML модели (низкий stage-1 — пропускаем живые запуски):
ROCKET_XGB_STAGE1_THRESHOLD = 4.0
ROCKET_XGB_STAGE1_MIN_SCORE = 4.0
ROCKET_XGB_FINAL_THRESHOLD = 24.0
ROCKET_XGB_THRESHOLD = 24.0

# 3. Тайминг — разрешаем вход с 3с и до 4 часов:
SOLANA_MIN_AGE_MIN = 0.05
JEV_EARLY_MIN_AGE_MIN = 0.05
JEV_MIN_AGE_MIN = 0.05
SOLANA_MAX_AGE_MIN = 240.0
JEV_MAX_AGE_MIN = 240.0
MAX_AGE_MINUTES_FOR_ENTRY = 240.0

MAX_ENTRY_M5_PCT = 350.0
PULLBACK_MAX_M5_PCT = 0.95
MAX_M5_PRICE_CHANGE_PCT = 350.0

MAX_ENTRY_M1_PCT = 60.0
JEV_MAX_M1_ENTRY_PCT = 60.0
PULLBACK_M1_MAX_PCT = 0.20
MAX_M1_VERTICAL_TOP_PCT = 60.0

JEV_MIN_M1_ENTRY_FLOOR_PCT = -5.0
JEV_TREND_CONFIRM_ENABLED = False

# 4. Стоп-лосс и тейки (сохраняем защиту, но не душим вход):
STOP_LOSS_PCT = -0.12
STOP_LOSS_UNLOCKED_PCT = -0.11
EMERGENCY_CAP_PCT = -0.15
FAST_STOP_LOSS_PCT = -12.0
EMERGENCY_STOP_LOSS_PCT = -15.0

TP1_TRIGGER_PCT = 0.12
TP1_SELL_FRAC = 0.50
TP2_TRIGGER_PCT = 0.24
TP2_SELL_FRAC = 0.50
TP3_TRIGGER_PCT = 0.55
TP3_SELL_FRAC = 0.50
BREAKEVEN_PCT = 0.11

MIN_LIQUIDITY_USD = 2500.0
MIN_GRADUATED_LIQ_USD = 2500.0
JEV_MIN_LIQUIDITY_USD = 2500.0
MAX_LIQUIDITY_USD = 180000.0
MIN_FDV_TO_LIQ_RATIO = 1.40
MIN_VOLUME_M5_USD = 700.0
MIN_VOL_TO_LIQ_M5 = 0.10
MAX_H1_PUMP_PCT = 600.0
MIN_M5_PRICE_CHANGE_PCT = 1.5
MIN_M1_PRICE_CHANGE_PCT = -5.0
# ==============================================================================
"""

NEW_EVALUATE_MOMENTUM_BLOCK = """    # 0.5 Отсев пустышек и фейковых пупов (где FDV < 1.35x Liquidity, как у скамов GEKKO/AURAW/PMA):
    if fdv_usd > 0 and liq_usd > 0 and (fdv_usd / liq_usd) < 1.35:
        return False, f"fake-lp-ratio fdv ${fdv_usd:,.0f} < 1.35x liq ${liq_usd:,.0f} (custom scam pool)"
    _min_tx_gate = 8 if age_min <= 5.0 else int(globals().get("MIN_TXNS_M5", 10))
    _min_vol_gate = 600.0 if (age_min <= 5.0 and buys5 >= 6) else float(globals().get("MIN_VOLUME_M5", 700.0))
    _min_buys_req = 6 if age_min <= 5.0 else 10
    if tot_tx < _min_tx_gate or buys5 < _min_buys_req:
        return False, f"no-demand-txns {buys5}b/{sells5}s (buys < {_min_buys_req} or tot {tot_tx} < {_min_tx_gate})"
    if vol5_usd < _min_vol_gate:
        return False, f"no-demand-vol5 ${vol5_usd:,.0f} < ${_min_vol_gate:,.0f} (dead micro-pool)"
    if liq_usd >= 1500.0 and vel_ratio < 0.08 and vol5_usd < 2000.0:
        return False, f"dead-turnover v/l {vel_ratio:.2f}x < 0.08x (vol5 ${vol5_usd:,.0f} on liq ${liq_usd:,.0f})"

    # 0.6 Динамический порог оборота для крупных кап (FDV >= $250k):
    if fdv_usd >= 250000.0:
        _req_fdv_vol = min(15000.0, max(3000.0, fdv_usd * 0.015))
        if vol5_usd < _req_fdv_vol:
            return False, f"low-fdv-turnover ${vol5_usd:,.0f} < ${_req_fdv_vol:,.0f} on FDV ${fdv_usd:,.0f}"
    if liq_usd >= 15000.0 and age_min >= 20.0 and vel_ratio < 0.10 and vol5_usd < 4000.0:
        return False, f"low-liq-turnover v/l {vel_ratio:.2f}x < 0.10x on liq ${liq_usd:,.0f}"

    # 1. Разрешаем вход для свежих и живых ракет, не режем макушки слишком жёстко:
    if age_min < float(globals().get("SOLANA_MIN_AGE_MIN", 0.05)):
        return False, f"too-young {age_min:.2f}m < 0.05m (skip 1st minute sniper chop)"
    _is_high_vol_flow = (
        vol5_usd >= 15000.0
        and (buys5 - sells5) >= 50
        and buys5 >= int(sells5 * 1.05)
        and 0.0 <= m1 <= 60.0
    )
    _m5_cap = 650.0 if _is_high_vol_flow else float(globals().get("MAX_ENTRY_M5_PCT", 350.0))
    if m5 > _m5_cap:
        return False, f"m5-overheated {m5:+.1f}% > +{_m5_cap:.0f}% (do not buy candle top)"
    if m1 > float(globals().get("MAX_ENTRY_M1_PCT", 60.0)):
        return False, f"m1-overheated {m1:+.1f}% > +60.0% (do not buy 1m spike top)"
    if m5 >= 15.0 and m1 > (m5 * 0.60):
        return False, f"m1-pump-spike m1 {m1:+.1f}% is >60% of m5 {m5:+.1f}% (single-minute vertical spike)"
    if h1 > float(globals().get("MAX_H1_PUMP_PCT", 600.0)) and not _is_high_vol_flow:
        return False, f"h1-overheated {h1:+.0f}% > +600% (spent pump)"

    _is_m1_surge = (m1 >= 1.5 and buys5 >= max(8, int(sells5 * 1.10)) and vol5_usd >= 1500.0)
    _m5_min_req = 1.0 if _is_m1_surge else 1.5
    if m5 < _m5_min_req:
        return False, f"m5-falling-knife {m5:+.1f}% < +{_m5_min_req:.1f}% (red or flat 5m candle)"
    _m1_floor = float(globals().get("JEV_MIN_M1_ENTRY_FLOOR_PCT", -5.0)) if (m5 >= 3.0 and buys5 >= int(sells5 * 1.10)) else -5.0
    if m1 < _m1_floor:
        return False, f"m1-falling-knife {m1:+.1f}% < {_m1_floor:+.1f}% (1m candle is dumping)"

    # 2. Адаптивный перевес покупателей (мягкий для свежих токенов):
    _req_bs_ratio = 1.05 if _is_high_vol_flow else float(globals().get("MIN_BUY_SELL_RATIO", 1.05))
    if sells5 > 4 and buys5 < int(sells5 * _req_bs_ratio):
        return False, f"sellers-dominating {buys5}b/{sells5}s (need buyers > sellers * {_req_bs_ratio:.2f} in 5m)"
"""


def backup_file(path: str) -> None:
    if os.path.exists(path):
        bak = path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            print(f"[BACKUP] Создана резервная копия: {bak}")


def patch_config(path: str = "config.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    mom_pattern = re.compile(
        r'    # 0\.5 (?:ЖЁСТКИЙ ОТСЕВ|Адаптивный отсев|Отсев пустышек).*?need buyers > sellers \* \{_req_bs_ratio:\.2f\} in 5m\)"',
        re.DOTALL,
    )
    if mom_pattern.search(content):
        content = mom_pattern.sub(NEW_EVALUATE_MOMENTUM_BLOCK.rstrip(), content, count=1)
        print(f"[OK] {path}: evaluate_entry_momentum обновлена (Rocket Flow Bypass для PROPTRENCH/JVOLT + защита от m1-палок).")

    content = re.sub(r"^STOP_LOSS_PCT\s*=\s*-0\.\d+", "STOP_LOSS_PCT = -0.12", content, flags=re.M)
    content = re.sub(r"^STOP_LOSS_UNLOCKED_PCT\s*=\s*-0\.\d+", "STOP_LOSS_UNLOCKED_PCT = -0.11", content, flags=re.M)
    content = re.sub(r"^EMERGENCY_CAP_PCT\s*=\s*-0\.\d+", "EMERGENCY_CAP_PCT = -0.15", content, flags=re.M)
    content = re.sub(r"^TP1_TRIGGER_PCT\s*=\s*0\.\d+", "TP1_TRIGGER_PCT = 0.12", content, flags=re.M)
    content = re.sub(r"^TP1_SELL_FRAC\s*=\s*0\.\d+", "TP1_SELL_FRAC = 0.50", content, flags=re.M)
    content = re.sub(r"^TP2_TRIGGER_PCT\s*=\s*0\.\d+", "TP2_TRIGGER_PCT = 0.24", content, flags=re.M)
    content = re.sub(r"^TP2_SELL_FRAC\s*=\s*0\.\d+", "TP2_SELL_FRAC = 0.50", content, flags=re.M)
    content = re.sub(r"^MAX_ENTRY_M5_PCT\s*=\s*\d+(\.\d+)?", "MAX_ENTRY_M5_PCT = 350.0", content, flags=re.M)
    content = re.sub(r"^MAX_ENTRY_M1_PCT\s*=\s*\d+(\.\d+)?", "MAX_ENTRY_M1_PCT = 60.0", content, flags=re.M)
    content = re.sub(r"^JEV_MAX_M1_ENTRY_PCT\s*=\s*\d+(\.\d+)?", "JEV_MAX_M1_ENTRY_PCT = 60.0", content, flags=re.M)
    content = re.sub(r"^SOLANA_MIN_AGE_MIN\s*=\s*\d+(\.\d+)?", "SOLANA_MIN_AGE_MIN = 0.05", content, flags=re.M)
    content = re.sub(r"^SOLANA_MAX_AGE_MIN\s*=\s*\d+(\.\d+)?", "SOLANA_MAX_AGE_MIN = 240.0", content, flags=re.M)
    content = re.sub(r"^JEV_MAX_AGE_MIN\s*=\s*\d+(\.\d+)?", "JEV_MAX_AGE_MIN = 240.0", content, flags=re.M)
    content = re.sub(r"^ROCKET_XGB_STAGE1_THRESHOLD\s*=\s*\d+(\.\d+)?", "ROCKET_XGB_STAGE1_THRESHOLD = 4.0", content, flags=re.M)
    content = re.sub(r"^ROCKET_XGB_FINAL_THRESHOLD\s*=\s*\d+(\.\d+)?", "ROCKET_XGB_FINAL_THRESHOLD = 24.0", content, flags=re.M)
    content = re.sub(r"^ROCKET_XGB_THRESHOLD\s*=\s*\d+(\.\d+)?", "ROCKET_XGB_THRESHOLD = 24.0", content, flags=re.M)
    content = re.sub(r"^MIN_VOLUME_M5\s*=\s*\d+(\.\d+)?", "MIN_VOLUME_M5 = 700.0", content, flags=re.M)
    content = re.sub(r"^MIN_TXNS_M5\s*=\s*\d+\b", "MIN_TXNS_M5 = 10", content, flags=re.M)
    content = re.sub(r"^MIN_BUY_SELL_RATIO\s*=\s*1\.\d+", "MIN_BUY_SELL_RATIO = 1.05", content, flags=re.M)
    content = re.sub(r"^JEV_MIN_M1_ENTRY_FLOOR_PCT\s*=\s*[-0-9.]+", "JEV_MIN_M1_ENTRY_FLOOR_PCT = -5.0", content, flags=re.M)

    if "# ==================== [AUTO-PATCH]" in content:
        content = re.sub(
            r"# ==================== \[AUTO-PATCH\].*?# ==============================================================================",
            CONFIG_APPEND_BLOCK.strip(),
            content,
            flags=re.DOTALL,
        )
    else:
        content = content.rstrip() + "\n" + CONFIG_APPEND_BLOCK

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] {path}: STOP_LOSS=-12%, TP1=+12%(50%), TP2=+24%(50%), XGB_FINAL=24%.")


def patch_analyzer(path: str = "analyzer.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. Включаем Rocket Flow Bypass на Stage-1 в analyzer.py для высокообъёмных ракет типа PROPTRENCH (8.1%) и JVOLT (11.8%)
    code = re.sub(
        r"_is_momentum_rocket_a\s*=\s*\(\s*\(conf\s*\+\s*0\.05\)\s*>=\s*max\(8\.0,\s*threshold\s*-\s*2\.0\)",
        "_is_momentum_rocket_a = (\n                (conf >= 7.5 and _vm5_pre >= 20000.0 and (_b5_pre - _s5_pre) >= 75 and _b5_pre >= int(_s5_pre * 1.14) and 0.0 <= _m1 <= 14.0)\n                or ((conf + 0.05) >= max(10.0, threshold - 2.0)",
        code,
    )
    code = re.sub(
        r"or\s*\(_age_min_pre\s*<=\s*5\.0\s+and\s+_m5\s*>=\s*1\.5\s+and\s+_b5_pre\s*>=\s*int\(_s5_pre\s*\*\s*1\.15\)\s+and\s+_vm5_pre\s*>=\s*1500\.0\s+and\s+_b5_pre\s*>=\s*8\)\s*\)\s*\)",
        "or (_age_min_pre <= 5.0 and _m5 >= 1.5 and _b5_pre >= int(_s5_pre * 1.15) and _vm5_pre >= 1500.0 and _b5_pre >= 8)))\n            )",
        code,
    )

    # 2. Разрешаем m5 до 95% в analyzer.py ТОЛЬКО для высокообъёмных плавных ракет (vol5 >= $20k, m1 <= 14%, net_buyers >= 75)
    code = re.sub(
        r"if\s+_m5\s*>\s*_max_entry_m5:\s*\n\s*if\s+True:",
        "if _m5 > _max_entry_m5:\n            _hi_vol_flow_a = (_vm5_pre >= 20000.0 and (_b5_pre - _s5_pre) >= 75 and _b5_pre >= int(_s5_pre * 1.14) and 0.0 <= _m1 <= 14.0 and _m5 <= 95.0)\n            if not _hi_vol_flow_a:",
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: добавлен Rocket Flow Bypass для PROPTRENCH (+82%) и JVOLT (1390b/212s).")


def patch_grok_jev(path: str = "grok_jev.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. На Stage-1 в grok_jev.py пропускаем высокообъёмные ракеты (PROPTRENCH 8.1%, JVOLT 11.86%) независимо от _is_unlocked_s1
    old_s1_jev = re.compile(
        r"_is_momentum_rocket\s*=\s*not\s+_is_unlocked_s1\s+and\s+_s1_fresh_ok\s+and\s+\(rkt_s1\s*\+\s*0\.05\)\s*>=\s*max\(8\.0,\s*s1_thresh\s*-\s*2\.0\).*?\n\s*\)",
        re.DOTALL,
    )
    new_s1_jev = (
        "_is_momentum_rocket = _s1_fresh_ok and (\n"
        "            (rkt_s1 >= 7.5 and _vm5_s1 >= 20000.0 and (_b5_s1 - _s5_s1) >= 75 and _b5_s1 >= int(_s5_s1 * 1.14) and 0.0 <= _m1_s1 <= 14.0 and _fdv_s1 >= 45000.0)\n"
        "            or (not _is_unlocked_s1 and (rkt_s1 + 0.05) >= max(12.0, s1_thresh) and _m1_s1 >= 0.0 and _m5_s1 >= 4.0 and _vm5_s1 >= 6000.0 and _b5_s1 >= int(_s5_s1 * 1.25))\n"
        "        )"
    )
    code = old_s1_jev.sub(new_s1_jev, code)

    # 2. На Stage-2 в grok_jev.py убираем слабый байпас rkt_final >= 2.5 при vol5 >= $3,500 (который пропустил MINNIE 16.8% в -24.7%),
    #    оставляя байпас ТОЛЬКО для мощных ракет (vol5 >= $20,000 и net_buyers >= 75):
    code = re.sub(
        r"_is_mom_rocket_fin\s*=\s*\(\s*not\s+_is_unlocked_custom_fin\s+and\s+_fin_m5\s*>=\s*2\.0\s+and\s+_fin_v5\s*>=\s*3500\.0.*?and\s+\(rkt_final\s*\+\s*0\.05\)\s*>=\s*2\.5\s*\)",
        "_is_mom_rocket_fin = (\n            not _is_unlocked_custom_fin\n            and _fin_m5 >= 5.0\n            and _fin_v5 >= 20000.0\n            and (_fin_b5 - _fin_s5) >= 75\n            and _fin_b5 >= int(_fin_s5 * 1.18)\n            and (rkt_final + 0.05) >= 7.5\n        )",
        code,
        flags=re.DOTALL,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: закрыта лазейка слабых входов (MINNIE 16.8%) и открыт вход для супер-объёмов (PROPTRENCH/JVOLT).")


def patch_exit_files() -> None:
    for fname in ("exit_manager.py", "exit_managers.py"):
        if not os.path.exists(fname):
            continue
        backup_file(fname)
        with open(fname, "r", encoding="utf-8") as f:
            code = f.read()

        # 1. Дефолтный стоп -12%, аварийный -15%
        code = re.sub(
            r'getattr\(config,\s*"STOP_LOSS_PCT",\s*-0\.\d+\)',
            'getattr(config, "STOP_LOSS_PCT", -0.12)',
            code,
        )
        code = re.sub(
            r'getattr\(config,\s*"EMERGENCY_CAP_PCT",\s*-0\.\d+\)',
            'getattr(config, "EMERGENCY_CAP_PCT", -0.15)',
            code,
        )

        # 2. МГНОВЕННЫЙ Fast Stop Loss с 1-го тика после 25 секунд (чтобы -12% не превращались в -24.7%, как на MINNIE)
        code = re.sub(
            r"if\s+_sl_c\s*>=\s*2\s+or\s+pnl_pct\s*<=\s*\(_sl\s*\*\s*1\.15\):",
            "if minutes_held >= 0.40 or _sl_c >= 2 or pnl_pct <= (_sl * 1.12):",
            code,
        )

        # 3. Плотная фиксация прибыли на пиках +20%..+55% (фикс MAYA +30.9% -> закрытие на +20%, а не на +0.6%):
        code = re.sub(
            r"elif\s+max_pnl_pct\s*>=\s*0\.28:\s*\n\s*_floor_pnl\s*=\s*0\.14\s*\n\s*_trail_dist\s*=\s*0\.18",
            "elif max_pnl_pct >= 0.22:\n                _floor_pnl = round(max(0.12, max_pnl_pct * 0.55), 2)\n                _trail_dist = 0.10",
            code,
        )
        code = re.sub(
            r"elif\s+max_pnl_pct\s*>=\s*0\.(?:15|14):\s*\n\s*_floor_pnl\s*=\s*0\.0[56]\s*\n\s*_trail_dist\s*=\s*0\.1[52]",
            "elif max_pnl_pct >= 0.12:\n                _floor_pnl = 0.05\n                _trail_dist = 0.10",
            code,
        )
        code = re.sub(
            r"if\s+_fl_c\s*>=\s*2\s+or\s+pnl_pct\s*<=\s*\(_floor_pnl\s*-\s*0\.05\):",
            "if _is_mb or _fl_c >= 1 or pnl_pct <= _floor_pnl:",
            code,
        )
        code = re.sub(
            r"if\s+_is_mb\s+or\s+_fl_c\s*>=\s*2\s+or\s+pnl_pct\s*<=\s*\(_floor_pnl\s*-\s*0\.03\):",
            "if _is_mb or _fl_c >= 1 or pnl_pct <= _floor_pnl:",
            code,
        )

        with open(fname, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[OK] {fname}: стоп -12% с 1-го тика (фикс MINNIE) + трейлинг 10% от пика и пол 55% от ATH (фикс MAYA).")


def verify_on_render() -> None:
    import importlib
    import config
    importlib.reload(config)

    print("\n" + "=" * 68, flush=True)
    print("✅ [RENDER PATCH v3 VERIFICATION] ПРОВЕРКА ПРИМЕНЕННЫХ ФИКСОВ:", flush=True)
    print(f"   • STOP_LOSS_PCT (1-tick)      = {getattr(config, 'STOP_LOSS_PCT', 0)*100:.0f}% / Emergency {getattr(config, 'EMERGENCY_CAP_PCT', 0)*100:.0f}%", flush=True)
    print(f"   • TP1 / TP2                   = +{getattr(config, 'TP1_TRIGGER_PCT', 0)*100:.0f}% (50%) / +{getattr(config, 'TP2_TRIGGER_PCT', 0)*100:.0f}% (50%)", flush=True)
    print(f"   • ROCKET_XGB_FINAL_THRESHOLD  = {getattr(config, 'ROCKET_XGB_FINAL_THRESHOLD', '?')}% (+ Rocket Flow Bypass >= $20k vol5)", flush=True)
    print(f"   • MAX_ENTRY_M1 / M5           = +{getattr(config, 'MAX_ENTRY_M1_PCT', '?')}% / +{getattr(config, 'MAX_ENTRY_M5_PCT', '?')}% (до +95% при vol5>=$20k)", flush=True)
    print("=" * 68 + "\n", flush=True)


def run_all_patches() -> None:
    print("=== Запуск авто-патчера v3 для бота ===", flush=True)
    patch_config("config.py")
    patch_analyzer("analyzer.py")
    patch_grok_jev("grok_jev.py")
    patch_exit_files()
    verify_on_render()


if __name__ == "__main__":
    run_all_patches()