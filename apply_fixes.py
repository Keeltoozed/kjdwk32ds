# -*- coding: utf-8 -*-
"""
apply_fixes.py — Автоматический патчер для репозитория Keeltoozed/kjdwk32ds.
Обновлено по результатам анализа реальных сделок из CSV (VICE, NIGGA, DEEP, ALMOND, QUEEN, Shrek, MEOW, SOYOUARE).
"""
import os
import re
import shutil

CONFIG_APPEND_BLOCK = """

# ==================== [AUTO-PATCH] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ v2 ====================
GROK_JEV_STANDALONE_BUY = False
GROK_JEV_MIN_CONFIDENCE = 48.0
GROK_JEV_MIN_XGB_SCORE = 16.0
GROK_JEV_MAX_AGE_MIN = 40.0
GROK_JEV_MIN_VOL_M5 = 3000.0

# 1. Объём и баланс покупателей:
MIN_VOLUME_M5 = 3000.0
MIN_TXNS_M5 = 15
MIN_BUY_SELL_RATIO = 1.18
DEFAULT_MIN_BUY_SELL_RATIO = 1.18
HIGH_VOL_M5_THRESHOLD = 15000.0
HIGH_VOL_MIN_BUY_SELL_RATIO = 1.12

# 2. Пороги ML модели:
ROCKET_XGB_STAGE1_THRESHOLD = 12.0
ROCKET_XGB_STAGE1_MIN_SCORE = 12.0
ROCKET_XGB_FINAL_THRESHOLD = 16.0
ROCKET_XGB_THRESHOLD = 16.0

# 3. Защита от покупки макушек (отсекает Shrek m5=+45%/m1=+30% и QUEEN m5=+43%) и 1-й минуты (MEOW 0.88m):
SOLANA_MIN_AGE_MIN = 1.5
JEV_EARLY_MIN_AGE_MIN = 1.5
JEV_MIN_AGE_MIN = 1.5
SOLANA_MAX_AGE_MIN = 40.0
JEV_MAX_AGE_MIN = 40.0
MAX_AGE_MINUTES_FOR_ENTRY = 40.0

MAX_ENTRY_M5_PCT = 28.0
PULLBACK_MAX_M5_PCT = 0.28
MAX_M5_PRICE_CHANGE_PCT = 28.0

MAX_ENTRY_M1_PCT = 15.0
JEV_MAX_M1_ENTRY_PCT = 15.0
PULLBACK_M1_MAX_PCT = 0.15
MAX_M1_VERTICAL_TOP_PCT = 15.0

JEV_MIN_M1_ENTRY_FLOOR_PCT = -3.5
JEV_TREND_CONFIRM_ENABLED = False

# 4. Реальный Stop-Loss -15% (вместо -28%/-32% на QUEEN и Shrek) и фиксация 55% на TP1 (+15%):
STOP_LOSS_PCT = -0.15
STOP_LOSS_UNLOCKED_PCT = -0.14
EMERGENCY_CAP_PCT = -0.20
FAST_STOP_LOSS_PCT = -15.0
EMERGENCY_STOP_LOSS_PCT = -20.0

TP1_TRIGGER_PCT = 0.14
TP1_SELL_FRAC = 0.55
TP2_TRIGGER_PCT = 0.32
TP2_SELL_FRAC = 0.45
BREAKEVEN_PCT = 0.12

MIN_LIQUIDITY_USD = 4000.0
MAX_LIQUIDITY_USD = 180000.0
MIN_FDV_TO_LIQ_RATIO = 1.15
MIN_VOLUME_M5_USD = 3000.0
MIN_VOL_TO_LIQ_M5 = 0.15
MAX_H1_PUMP_PCT = 180.0
MIN_M5_PRICE_CHANGE_PCT = 2.5
MIN_M1_PRICE_CHANGE_PCT = -3.5
# ==============================================================================
"""

NEW_EVALUATE_MOMENTUM_BLOCK = """    # 0.5 Адаптивный отсев мёртвых пустышек без объёма и спроса:
    _min_tx_gate = 12 if age_min <= 5.0 else int(globals().get("MIN_TXNS_M5", 15))
    _min_vol_gate = 2000.0 if (age_min <= 5.0 and buys5 >= 10) else float(globals().get("MIN_VOLUME_M5", 3000.0))
    _min_buys_req = 8 if age_min <= 5.0 else 10
    if tot_tx < _min_tx_gate or buys5 < _min_buys_req:
        return False, f"no-demand-txns {buys5}b/{sells5}s (buys < {_min_buys_req} or tot {tot_tx} < {_min_tx_gate})"
    if vol5_usd < _min_vol_gate:
        return False, f"no-demand-vol5 ${vol5_usd:,.0f} < ${_min_vol_gate:,.0f} (dead micro-pool)"
    if liq_usd >= 1500.0 and vel_ratio < 0.08 and vol5_usd < 2500.0:
        return False, f"dead-turnover v/l {vel_ratio:.2f}x < 0.08x (vol5 ${vol5_usd:,.0f} on liq ${liq_usd:,.0f})"

    # 0.6 Динамический порог оборота только для крупных кап (FDV >= $250k):
    if fdv_usd >= 250000.0:
        _req_fdv_vol = min(15000.0, max(4500.0, fdv_usd * 0.02))
        if vol5_usd < _req_fdv_vol:
            return False, f"low-fdv-turnover ${vol5_usd:,.0f} < ${_req_fdv_vol:,.0f} on FDV ${fdv_usd:,.0f} (sluggish chop, no rocket momentum)"
    if liq_usd >= 15000.0 and age_min >= 20.0 and vel_ratio < 0.15 and vol5_usd < 6000.0:
        return False, f"low-liq-turnover v/l {vel_ratio:.2f}x < 0.15x on liq ${liq_usd:,.0f} (stagnant flow)"

    # 1. Защита от покупки вершин (Shrek m5=+45%/m1=+30%, QUEEN m5=+43%) и разрешение микро-отката до -3.5%:
    if age_min < float(globals().get("SOLANA_MIN_AGE_MIN", 1.5)):
        return False, f"too-young {age_min:.2f}m < 1.5m (skip 1st minute sniper chop)"
    if m5 > float(globals().get("MAX_ENTRY_M5_PCT", 28.0)):
        return False, f"m5-overheated {m5:+.1f}% > +28.0% (do not buy candle top)"
    if m1 > float(globals().get("MAX_ENTRY_M1_PCT", 15.0)):
        return False, f"m1-overheated {m1:+.1f}% > +15.0% (do not buy 1m spike top)"
    if h1 > float(globals().get("MAX_H1_PUMP_PCT", 180.0)) and age_min >= 15.0:
        return False, f"h1-overheated {h1:+.0f}% > +180% at {age_min:.0f}m (spent pump)"

    _is_m1_surge = (m1 >= 1.5 and buys5 >= max(8, int(sells5 * 1.15)) and vol5_usd >= 1500.0)
    _m5_min_req = -0.5 if _is_m1_surge else 2.5
    if m5 < _m5_min_req:
        return False, f"m5-falling-knife {m5:+.1f}% < +{_m5_min_req:.1f}% (red or flat 5m candle)"
    _m1_floor = float(globals().get("JEV_MIN_M1_ENTRY_FLOOR_PCT", -3.5)) if (m5 >= 3.5 and buys5 >= int(sells5 * 1.15)) else 0.0
    if m1 < _m1_floor:
        return False, f"m1-falling-knife {m1:+.1f}% < {_m1_floor:+.1f}% (1m candle is dumping)"

    # 2. Адаптивный перевес покупателей (1.12x на высоком объёме >= $15k, иначе MIN_BUY_SELL_RATIO 1.18x):
    _req_bs_ratio = float(globals().get("HIGH_VOL_MIN_BUY_SELL_RATIO", 1.12)) if vol5_usd >= 15000.0 else float(globals().get("MIN_BUY_SELL_RATIO", 1.18))
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

    # 1. Обновляем функцию evaluate_entry_momentum
    mom_pattern = re.compile(
        r'    # 0\.5 (?:ЖЁСТКИЙ ОТСЕВ МЁРТВЫХ ПУСТЫШЕК|Адаптивный отсев мёртвых пустышек).*?need buyers > sellers \* \{_req_bs_ratio:\.2f\} in 5m\)"',
        re.DOTALL,
    )
    if mom_pattern.search(content):
        content = mom_pattern.sub(NEW_EVALUATE_MOMENTUM_BLOCK.rstrip(), content, count=1)
        print(f"[OK] {path}: функция evaluate_entry_momentum обновлена (блок вершин m5>28%/m1>15%, мин. возраст 1.5м).")

    # 2. Обновляем константы в теле config.py
    content = re.sub(r"^STOP_LOSS_PCT\s*=\s*-0\.\d+", "STOP_LOSS_PCT = -0.15", content, flags=re.M)
    content = re.sub(r"^EMERGENCY_CAP_PCT\s*=\s*-0\.\d+", "EMERGENCY_CAP_PCT = -0.20", content, flags=re.M)
    content = re.sub(r"^TP1_TRIGGER_PCT\s*=\s*0\.\d+", "TP1_TRIGGER_PCT = 0.14", content, flags=re.M)
    content = re.sub(r"^TP1_SELL_FRAC\s*=\s*0\.\d+", "TP1_SELL_FRAC = 0.55", content, flags=re.M)
    content = re.sub(r"^MAX_ENTRY_M5_PCT\s*=\s*\d+(\.\d+)?", "MAX_ENTRY_M5_PCT = 28.0", content, flags=re.M)
    content = re.sub(r"^MAX_ENTRY_M1_PCT\s*=\s*\d+(\.\d+)?", "MAX_ENTRY_M1_PCT = 15.0", content, flags=re.M)
    content = re.sub(r"^JEV_MAX_M1_ENTRY_PCT\s*=\s*\d+(\.\d+)?", "JEV_MAX_M1_ENTRY_PCT = 15.0", content, flags=re.M)
    content = re.sub(r"^SOLANA_MIN_AGE_MIN\s*=\s*\d+(\.\d+)?", "SOLANA_MIN_AGE_MIN = 1.5", content, flags=re.M)
    content = re.sub(r"^SOLANA_MAX_AGE_MIN\s*=\s*\d+(\.\d+)?", "SOLANA_MAX_AGE_MIN = 40.0", content, flags=re.M)
    content = re.sub(r"^JEV_MAX_AGE_MIN\s*=\s*\d+(\.\d+)?", "JEV_MAX_AGE_MIN = 40.0", content, flags=re.M)
    content = re.sub(r"^ROCKET_XGB_FINAL_THRESHOLD\s*=\s*\d+(\.\d+)?", "ROCKET_XGB_FINAL_THRESHOLD = 16.0", content, flags=re.M)
    content = re.sub(r"^ROCKET_XGB_THRESHOLD\s*=\s*\d+(\.\d+)?", "ROCKET_XGB_THRESHOLD = 16.0", content, flags=re.M)
    content = re.sub(r"^MIN_VOLUME_M5\s*=\s*\d+(\.\d+)?", "MIN_VOLUME_M5 = 3000.0", content, flags=re.M)
    content = re.sub(r"^MIN_TXNS_M5\s*=\s*\d+\b", "MIN_TXNS_M5 = 15", content, flags=re.M)
    content = re.sub(r"^MIN_BUY_SELL_RATIO\s*=\s*1\.\d+", "MIN_BUY_SELL_RATIO = 1.18", content, flags=re.M)
    content = re.sub(r"^JEV_MIN_M1_ENTRY_FLOOR_PCT\s*=\s*[-0-9.]+", "JEV_MIN_M1_ENTRY_FLOOR_PCT = -3.5", content, flags=re.M)

    # 3. Заменяем или добавляем финальный блок [AUTO-PATCH]
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
    print(f"[OK] {path}: STOP_LOSS_PCT=-15%, MAX_ENTRY_M5_PCT=28%, MAX_ENTRY_M1_PCT=15%, TP1=55% на +14%.")


def patch_analyzer(path: str = "analyzer.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. Заменяем холостые поисковые запросы DexScreener
    old_ds_urls = re.compile(
        r'_ds_search_urls\s*=\s*\(\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=pumpswap",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=SOL%20pump",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=meteora%20SOL",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=bonk%20SOL",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=raydium%20SOL",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=orca%20SOL",\s*\)',
        re.DOTALL,
    )
    new_ds_urls = (
        '_ds_search_urls = (\n'
        '                    "https://api.dexscreener.com/latest/dex/search?q=pumpswap",\n'
        '                    "https://api.dexscreener.com/latest/dex/search?q=pump",\n'
        '                    "https://api.dexscreener.com/latest/dex/search?q=SOL%20pump",\n'
        '                )'
    )
    code = old_ds_urls.sub(new_ds_urls, code)

    # 2. Смягчаем MATURE-STALL-GUARD для токенов старше 20 минут
    code = re.sub(
        r"_is_mature_breakout\s*=\s*\(_m5\s*>=\s*2\.0\s+and\s+_vm5_pre\s*>=\s*10000\.0\s+and\s+_b5_pre\s*>=\s*int\(_s5_pre\s*\*\s*1\.30\)\s+and\s+_tot_tx5_pre\s*>=\s*25\)",
        "_is_mature_breakout = (_m5 >= 2.0 and _vm5_pre >= 4500.0 and _b5_pre >= int(_s5_pre * 1.18) and _tot_tx5_pre >= 18)",
        code,
    )
    code = re.sub(
        r"if\s+not\s+_is_mature_breakout\s+and\s+\(_vm5_pre\s*<\s*8000\.0\s+or\s+_v2l_mat\s*<\s*0\.25\s+or\s+_m5\s*<\s*1\.0\):",
        "if not _is_mature_breakout and (_vm5_pre < 3500.0 or _v2l_mat < 0.15 or _m5 < 1.5):",
        code,
    )

    # 3. Адаптивный фильтр покупателей/продавцов
    code = re.sub(
        r'_base_bs_cfg\s*=\s*float\(getattr\(config,\s*"MIN_BUY_SELL_RATIO",\s*1\.30\)\)\s*\n\s*_min_bs_cfg\s*=\s*_base_bs_cfg',
        '_base_bs_cfg = float(getattr(config, "MIN_BUY_SELL_RATIO", 1.18))\n        _min_bs_cfg = 1.12 if _vm5_pre >= 15000.0 else _base_bs_cfg',
        code,
    )

    # 4. Убираем байпас перегретых свечей m5 > MAX_ENTRY_M5_PCT (чтобы не пускать QUEEN +43% и Shrek +45%)
    code = re.sub(
        r"if\s+_m5\s*>\s*_max_entry_m5:\s*\n\s*_fresh_grad_m5_cap_a\s*=.*?\n\s*_is_strong_buyer_breakout\s*=.*?\n\s*_is_cooling_pullback\s*=.*?\n\s*if\s+not\s+\(_is_strong_buyer_breakout\s+or\s+_is_cooling_pullback\):",
        "if _m5 > _max_entry_m5:\n            if True:",
        code,
    )

    # 5. Смягчаем SPIKE-REVERSAL в verify_1m_candles_not_dumping
    code = re.sub(
        r"if\s+c0_spike_pct\s*>=\s*15\.0\s+and\s+c0_wick_drop\s*>=\s*8\.0:",
        "if c0_spike_pct >= 28.0 and c0_wick_drop >= 15.0:",
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: заблокирован вход в перегретые свечи m5 > 28% (защита от Shrek/QUEEN).")


def patch_jup_discovery(path: str = "jup_discovery.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    old_jup_queries = re.compile(
        r'ds_queries\s*=\s*\(\s*'
        r'"https://api\.dexscreener\.com/token-boosts/latest/v1",\s*'
        r'"https://api\.dexscreener\.com/token-boosts/top/v1",\s*'
        r'"https://api\.dexscreener\.com/token-profiles/latest/v1",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=pumpswap",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=SOL%20pump",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=pumpswap%20SOL",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=bonk%20SOL",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=raydium%20SOL",\s*'
        r'"https://api\.dexscreener\.com/latest/dex/search\?q=orca%20SOL",\s*\)',
        re.DOTALL,
    )
    new_jup_queries = (
        'ds_queries = (\n'
        '                "https://api.dexscreener.com/token-boosts/latest/v1",\n'
        '                "https://api.dexscreener.com/token-boosts/top/v1",\n'
        '                "https://api.dexscreener.com/token-profiles/latest/v1",\n'
        '                "https://api.dexscreener.com/token-profiles/recent-updates/v1",\n'
        '                "https://api.dexscreener.com/community-takeovers/latest/v1",\n'
        '                "https://api.dexscreener.com/latest/dex/search?q=pumpswap",\n'
        '                "https://api.dexscreener.com/latest/dex/search?q=pump",\n'
        '            )'
    )
    code = old_jup_queries.sub(new_jup_queries, code)

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: очищены холостые поисковые запросы в ds_queries.")


def patch_scan_pipeline(path: str = "scan_pipeline.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    code = re.sub(
        r"rocket-xgb-s1-reject\s+\{([^}]+)\}\s*<\s*(?:18|14)%",
        r"rocket-xgb-s1-reject {\1} < 12%",
        code,
    )
    code = re.sub(r"(\bs1_thresh\s*=\s*)(?:18|14)(\.0)?", r"\g<1>12.0", code)
    code = re.sub(r"(rkt_score\s*<\s*)(?:18|14)(\.0)?", r"\g<1>12.0", code)
    code = re.sub(r"(\*\s*)1\.30\b", r"\g<1>1.18", code)
    code = re.sub(r"need buyers > sellers \* 1\.30", "need buyers > sellers * 1.18", code)

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: обновлены пороги Stage-1 (12%) и баланс покупателей (1.18x).")


def patch_grok_jev(path: str = "grok_jev.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    import_hook = (
        "try:\n"
        "    from unified_gates import check_unified_entry_gates, sanitize_and_enrich_features\n"
        "except ImportError:\n"
        "    check_unified_entry_gates = None\n"
        "    sanitize_and_enrich_features = None\n"
    )
    if "from unified_gates import" not in code:
        code = import_hook + "\n" + code

    code = re.sub(
        r'("price_change_m1"\s*:\s*)(None|pair\.get\([\'"][^\'"]+[\'"]\))',
        r'\1(pair.get("priceChange", {}).get("m1") or round(float(pair.get("priceChange", {}).get("m5") or 0.0) / 4.0, 2))',
        code,
    )
    code = re.sub(
        r'_req_bs\s*=\s*float\(getattr\(_c,\s*"MIN_BUY_SELL_RATIO",\s*1\.30\)\)',
        '_req_bs = 1.12 if vol_m5 >= 15000.0 else float(getattr(_c, "MIN_BUY_SELL_RATIO", 1.18))',
        code,
    )
    code = re.sub(
        r"if\s+_fm1\s*<\s*0\.0:",
        'if _fm1 < float(getattr(_c, "JEV_MIN_M1_ENTRY_FLOOR_PCT", -3.5)):',
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: исправлен FINAL-RECHECK и адаптивный фильтр покупателей.")


def patch_main(path: str = "main.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    code = re.sub(
        r"if\s+_pk_drop_live\s*<=\s*-15\.0\s+or\s+_lq_drop_live\s*<=\s*-15\.0:",
        "if _pk_drop_live <= -28.0 or _lq_drop_live <= -15.0:",
        code,
    )
    # Отключаем байпас перегретого m5 в scanner_loop
    code = re.sub(
        r"_is_breakout\s*=\s*\(_vol5_live\s*>=\s*8000\.0\s+and\s+_b5_sc\s*>=\s*int\(_s5_sc\s*\*\s*1\.2\)\s+and\s+_m5_live\s*<=\s*_max_allowed_m5\)",
        "_is_breakout = False",
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: отключен байпас перегретых свечей m5 > 28% в scanner_loop.")


def patch_exit_files() -> None:
    for fname in ("exit_manager.py", "exit_managers.py"):
        if not os.path.exists(fname):
            continue
        backup_file(fname)
        with open(fname, "r", encoding="utf-8") as f:
            code = f.read()

        # 1. Меняем дефолтный стоп в exit_managers.py с -0.32 на -0.15 (спасает от лоссов -31%..-32% как на QUEEN и Shrek)
        code = re.sub(
            r'getattr\(config,\s*"STOP_LOSS_PCT",\s*-0\.32\)',
            'getattr(config, "STOP_LOSS_PCT", -0.15)',
            code,
        )
        code = re.sub(
            r'getattr\(config,\s*"EMERGENCY_CAP_PCT",\s*-0\.40\)',
            'getattr(config, "EMERGENCY_CAP_PCT", -0.20)',
            code,
        )

        # 2. Защищаем Moonbag Floor (кейс ALMOND: пик +18.3% -> провал до -15.9% из-за ожидания 2 тиков):
        # Поднимаем пол мунбэга после TP1 (+14%) до +6% и закрываем МГНОВЕННО с 1-го тика, если это мунбэг!
        code = re.sub(
            r"elif\s+max_pnl_pct\s*>=\s*0\.15:\s*\n\s*_floor_pnl\s*=\s*0\.05\s*\n\s*_trail_dist\s*=\s*0\.15",
            "elif max_pnl_pct >= 0.14:\n                _floor_pnl = 0.06\n                _trail_dist = 0.12",
            code,
        )
        code = re.sub(
            r"if\s+_fl_c\s*>=\s*2\s+or\s+pnl_pct\s*<=\s*\(_floor_pnl\s*-\s*0\.05\):",
            "if _is_mb or _fl_c >= 2 or pnl_pct <= (_floor_pnl - 0.03):",
            code,
        )

        with open(fname, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[OK] {fname}: стоп снижен до -15%, а Moonbag Floor закрывает остаток в плюс с 1-го тика (фикс ALMOND).")


def verify_on_render() -> None:
    """Печатает в логи Render точные значения после патча."""
    import importlib
    import config
    importlib.reload(config)

    print("\n" + "=" * 68, flush=True)
    print("✅ [RENDER PATCH v2 VERIFICATION] ПРОВЕРКА ПРИМЕНЕННЫХ ФИКСОВ:", flush=True)
    print(f"   • STOP_LOSS_PCT               = {getattr(config, 'STOP_LOSS_PCT', '?')*100:.0f}% (было -28%/-32% -> стало -15%)", flush=True)
    print(f"   • MAX_ENTRY_M5_PCT            = +{getattr(config, 'MAX_ENTRY_M5_PCT', '?')}% (отсекает хаи Shrek +45% / QUEEN +43%)", flush=True)
    print(f"   • MAX_ENTRY_M1_PCT            = +{getattr(config, 'MAX_ENTRY_M1_PCT', '?')}% (отсекает свечки m1 +30%)", flush=True)
    print(f"   • SOLANA_MIN_AGE_MIN          = {getattr(config, 'SOLANA_MIN_AGE_MIN', '?')}m (отсекает 1-ю минуту MEOW 0.88m)", flush=True)
    print(f"   • TP1_TRIGGER / SELL_FRAC     = +{getattr(config, 'TP1_TRIGGER_PCT', 0)*100:.0f}% / {getattr(config, 'TP1_SELL_FRAC', 0)*100:.0f}%", flush=True)
    print(f"   • ROCKET_XGB_FINAL_THRESHOLD  = {getattr(config, 'ROCKET_XGB_FINAL_THRESHOLD', '?')}%", flush=True)
    print("=" * 68 + "\n", flush=True)


def run_all_patches() -> None:
    print("=== Запуск авто-патчера v2 для бота ===", flush=True)
    patch_config("config.py")
    patch_analyzer("analyzer.py")
    patch_jup_discovery("jup_discovery.py")
    patch_scan_pipeline("scan_pipeline.py")
    patch_grok_jev("grok_jev.py")
    patch_main("main.py")
    patch_exit_files()
    verify_on_render()


if __name__ == "__main__":
    run_all_patches()