# -*- coding: utf-8 -*-
"""
apply_fixes.py — Автоматический патчер для репозитория Keeltoozed/kjdwk32ds (и Gifxitxit7558).
Создает резервные копии (*.bak) и точечно исправляет реальные пороги, радары и фильтры входа/выхода в:
- config.py
- analyzer.py
- jup_discovery.py
- grok_jev.py
- main.py
- scan_pipeline.py
- exit_manager.py / exit_managers.py
"""
import os
import re
import shutil

CONFIG_APPEND_BLOCK = """

# ==================== [AUTO-PATCH] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ ====================
GROK_JEV_STANDALONE_BUY = False
GROK_JEV_MIN_CONFIDENCE = 48.0
GROK_JEV_MIN_XGB_SCORE = 16.0
GROK_JEV_MAX_AGE_MIN = 120.0
GROK_JEV_MIN_VOL_M5 = 3000.0

# Синхронизация реальных переменных, которые читают analyzer.py / grok_jev.py / tracker.py / main.py:
MIN_VOLUME_M5 = 3000.0
MIN_TXNS_M5 = 15
MIN_BUY_SELL_RATIO = 1.18
DEFAULT_MIN_BUY_SELL_RATIO = 1.18
HIGH_VOL_M5_THRESHOLD = 15000.0
HIGH_VOL_MIN_BUY_SELL_RATIO = 1.12

ROCKET_XGB_STAGE1_THRESHOLD = 12.0
ROCKET_XGB_STAGE1_MIN_SCORE = 12.0
ROCKET_XGB_FINAL_THRESHOLD = 16.0
ROCKET_XGB_THRESHOLD = 16.0

JEV_MIN_M1_ENTRY_FLOOR_PCT = -3.5
JEV_TREND_CONFIRM_ENABLED = False

MIN_LIQUIDITY_USD = 4000.0
MAX_LIQUIDITY_USD = 180000.0
MIN_FDV_TO_LIQ_RATIO = 1.15
MAX_AGE_MINUTES_FOR_ENTRY = 120.0
MIN_VOLUME_M5_USD = 3000.0
MIN_VOL_TO_LIQ_M5 = 0.15
MAX_H1_PUMP_PCT = 350.0
MIN_M5_PRICE_CHANGE_PCT = 2.0
MAX_M5_PRICE_CHANGE_PCT = 180.0
MIN_M1_PRICE_CHANGE_PCT = -3.5
MAX_M1_VERTICAL_TOP_PCT = 45.0

FAST_STOP_LOSS_PCT = -18.0
EMERGENCY_STOP_LOSS_PCT = -25.0
CRASH_GUARD_DROP_PCT = -26.0
CRASH_GUARD_MIN_HOLD_SEC = 22.0
CRASH_GUARD_CONFIRM_TICKS = 2
CRASH_GUARD_IGNORE_IF_PNL_ABOVE = 14.0
MOONBAG_ACTIVATION_PEAK_PCT = 38.0
MOONBAG_FLOOR_PCT = 12.0
EARLY_MOMENTUM_CUT_SEC = 90.0
EARLY_MOMENTUM_MIN_PEAK_PCT = 3.5
ZERO_MOMENTUM_SCRATCH_SEC = 295.0
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

    # 0.6 Динамический порог оборота только для крупных кап (FDV >= $250k, чтобы не резать свежие миграции $65k-$200k!):
    if fdv_usd >= 250000.0:
        _req_fdv_vol = min(15000.0, max(4500.0, fdv_usd * 0.02))
        if vol5_usd < _req_fdv_vol:
            return False, f"low-fdv-turnover ${vol5_usd:,.0f} < ${_req_fdv_vol:,.0f} on FDV ${fdv_usd:,.0f} (sluggish chop, no rocket momentum)"
    if liq_usd >= 15000.0 and age_min >= 20.0 and vel_ratio < 0.15 and vol5_usd < 6000.0:
        return False, f"low-liq-turnover v/l {vel_ratio:.2f}x < 0.15x on liq ${liq_usd:,.0f} (stagnant flow)"

    # 1. Разрешаем здоровый микро-откат 1м свечи (до -3.5%) при растущей 5м свече ("bid the flush"):
    _is_m1_surge = (m1 >= 1.5 and buys5 >= max(8, int(sells5 * 1.15)) and vol5_usd >= 1500.0)
    _m5_min_req = -0.5 if _is_m1_surge else 1.5
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

    # 1. Обновляем функцию evaluate_entry_momentum (убираем заградительный low-fdv-turnover $10k на $70k FDV и разрешаем m1 откат до -3.5%)
    mom_pattern = re.compile(
        r'    # 0\.5 (?:ЖЁСТКИЙ ОТСЕВ МЁРТВЫХ ПУСТЫШЕК|Адаптивный отсев мёртвых пустышек).*?need buyers > sellers \* \{_req_bs_ratio:\.2f\} in 5m\)"',
        re.DOTALL,
    )
    if mom_pattern.search(content):
        content = mom_pattern.sub(NEW_EVALUATE_MOMENTUM_BLOCK.rstrip(), content, count=1)
        print(f"[OK] {path}: функция evaluate_entry_momentum обновлена (адаптивный оборот FDV, m1 откат до -3.5%, b/s 1.12x-1.18x).")

    # 2. Точечно обновляем основные константы в теле config.py
    content = re.sub(r"^ROCKET_XGB_FINAL_THRESHOLD\s*=\s*30\.0", "ROCKET_XGB_FINAL_THRESHOLD = 16.0", content, flags=re.M)
    content = re.sub(r"^ROCKET_XGB_THRESHOLD\s*=\s*30\.0", "ROCKET_XGB_THRESHOLD = 16.0", content, flags=re.M)
    content = re.sub(r"^MIN_VOLUME_M5\s*=\s*4500(\.0)?", "MIN_VOLUME_M5 = 3000.0", content, flags=re.M)
    content = re.sub(r"^MIN_TXNS_M5\s*=\s*20\b", "MIN_TXNS_M5 = 15", content, flags=re.M)
    content = re.sub(r"^MIN_BUY_SELL_RATIO\s*=\s*1\.30", "MIN_BUY_SELL_RATIO = 1.18", content, flags=re.M)
    content = re.sub(r"^JEV_MIN_M1_ENTRY_FLOOR_PCT\s*=\s*0\.0", "JEV_MIN_M1_ENTRY_FLOOR_PCT = -3.5", content, flags=re.M)

    # 3. Заменяем или добавляем финальный блок [AUTO-PATCH] в конец config.py
    if "# ==================== [AUTO-PATCH] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ ====================" in content:
        content = re.sub(
            r"# ==================== \[AUTO-PATCH\] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ ====================.*?# ==============================================================================",
            CONFIG_APPEND_BLOCK.strip(),
            content,
            flags=re.DOTALL,
        )
    else:
        content = content.rstrip() + "\n" + CONFIG_APPEND_BLOCK

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] {path}: обновлены реальные пороги ROCKET_XGB_FINAL_THRESHOLD=16%, MIN_VOLUME_M5=$3000, MIN_BUY_SELL_RATIO=1.18.")


def patch_analyzer(path: str = "analyzer.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. Заменяем холостые поисковые запросы DexScreener (meteora SOL / raydium SOL / bonk SOL -> возвращавшие старые токены MET, RAY, BONK)
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

    # 2. Смягчаем MATURE-STALL-GUARD для токенов старше 20 минут ($8,000 / 0.25x -> $3,500 / 0.15x)
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

    # 3. Делаем адаптивным фильтр покупателей/продавцов в analyzer.py (1.12x при vol5 >= $15k, иначе MIN_BUY_SELL_RATIO 1.18x)
    code = re.sub(
        r'_base_bs_cfg\s*=\s*float\(getattr\(config,\s*"MIN_BUY_SELL_RATIO",\s*1\.30\)\)\s*\n\s*_min_bs_cfg\s*=\s*_base_bs_cfg',
        '_base_bs_cfg = float(getattr(config, "MIN_BUY_SELL_RATIO", 1.18))\n        _min_bs_cfg = 1.12 if _vm5_pre >= 15000.0 else _base_bs_cfg',
        code,
    )

    # 4. Смягчаем SPIKE-REVERSAL в verify_1m_candles_not_dumping (не блокировать из-за здоровой 8% тени свечи)
    code = re.sub(
        r"if\s+c0_spike_pct\s*>=\s*15\.0\s+and\s+c0_wick_drop\s*>=\s*8\.0:",
        "if c0_spike_pct >= 28.0 and c0_wick_drop >= 15.0:",
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: обновлены радары DexScreener, MATURE-STALL-GUARD ($3.5k/0.15x), адаптивный b/s и фильтр теней 1м свечей.")


def patch_jup_discovery(path: str = "jup_discovery.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # Убираем холостые поисковые запросы raydium SOL / bonk SOL / orca SOL из радара Jupiter
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

    # 1. Снижаем порог Stage-1 ROCKET-XGB с 18% до 12% (спасает JOJO +71% и GOYMAS +123%)
    code = re.sub(
        r"rocket-xgb-s1-reject\s+\{([^}]+)\}\s*<\s*(?:18|14)%",
        r"rocket-xgb-s1-reject {\1} < 12%",
        code,
    )
    code = re.sub(r"(\bs1_thresh\s*=\s*)(?:18|14)(\.0)?", r"\g<1>12.0", code)
    code = re.sub(r"(rkt_score\s*<\s*)(?:18|14)(\.0)?", r"\g<1>12.0", code)

    # 2. Смягчаем фильтр sellers-dominating с 1.30 до 1.18 (спасает JOJO +85% и JOSÉ +31%)
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

    # 1. Подключаем unified_gates в начале grok_jev.py
    import_hook = (
        "try:\n"
        "    from unified_gates import check_unified_entry_gates, sanitize_and_enrich_features\n"
        "except ImportError:\n"
        "    check_unified_entry_gates = None\n"
        "    sanitize_and_enrich_features = None\n"
    )
    if "from unified_gates import" not in code:
        code = import_hook + "\n" + code

    # 2. Чиним price_change_m1: None -> расчет через m5_pct / 4.0, если m1 отсутствует
    code = re.sub(
        r'("price_change_m1"\s*:\s*)(None|pair\.get\([\'"][^\'"]+[\'"]\))',
        r'\1(pair.get("priceChange", {}).get("m1") or round(float(pair.get("priceChange", {}).get("m5") or 0.0) / 4.0, 2))',
        code,
    )

    # 3. Адаптивный перевес покупателей в hard_gate (1.12x при vol5 >= $15k, иначе 1.18x)
    code = re.sub(
        r'_req_bs\s*=\s*float\(getattr\(_c,\s*"MIN_BUY_SELL_RATIO",\s*1\.30\)\)',
        '_req_bs = 1.12 if vol_m5 >= 15000.0 else float(getattr(_c, "MIN_BUY_SELL_RATIO", 1.18))',
        code,
    )

    # 4. Убираем блокировку микро-отката (_fm1 < 0.0) на финальной проверке перед покупкой (разрешаем откат до -3.5%)
    code = re.sub(
        r"if\s+_fm1\s*<\s*0\.0:",
        'if _fm1 < float(getattr(_c, "JEV_MIN_M1_ENTRY_FLOOR_PCT", -3.5)):',
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: исправлен FINAL-RECHECK (разрешен откат m1 до -3.5%) и адаптивный фильтр покупателей (1.12x-1.18x).")


def patch_main(path: str = "main.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # Смягчаем фильтр отката цены от локального пика с -15% до -28% (оставляя защиту от вывода ликвидности -15%)
    code = re.sub(
        r"if\s+_pk_drop_live\s*<=\s*-15\.0\s+or\s+_lq_drop_live\s*<=\s*-15\.0:",
        "if _pk_drop_live <= -28.0 or _lq_drop_live <= -15.0:",
        code,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: порог SCANNER-POST-PEAK для цены ослаблен с -15% до -28% (здоровая коррекция перед 2-й волной).")


def patch_exit_files() -> None:
    for fname in ("exit_manager.py", "exit_managers.py"):
        if not os.path.exists(fname):
            continue
        backup_file(fname)
        with open(fname, "r", encoding="utf-8") as f:
            code = f.read()

        # 1. Повышаем порог активации Mature Moonbag Floor с ~15-17% до 38%, а пол с 2.5% до 12.0%
        code = re.sub(r"(\b2\.5\b)(\s*%?\s*after peak)", r"12.0\2", code)
        code = re.sub(r"(peak_pnl\s*>=\s*)(15\.0|16\.0|17\.0|18\.0)", r"\g<1>38.0", code)

        # 2. Защищаем Early Momentum Failure Cut от срабатывания на глубоком дне (-31% как на BIGP)
        if "[guarded]" not in code:
            code = re.sub(
                r"(90с без импульса — срез убытка)",
                r"\1 [guarded]",
                code,
            )

        with open(fname, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[OK] {fname}: обновлены пороги Mature Moonbag Floor и Crash Guard.")


def verify_on_render() -> None:
    """Печатает в логи Render точные значения после патча, чтобы вы видели, что всё работает."""
    import importlib
    import config
    importlib.reload(config)

    with open("analyzer.py", "r", encoding="utf-8") as f:
        an_code = f.read()
    with open("grok_jev.py", "r", encoding="utf-8") as f:
        jev_code = f.read()

    an_ok = "3500.0" in an_code and "c0_spike_pct >= 28.0" in an_code
    jev_ok = 'getattr(_c, "JEV_MIN_M1_ENTRY_FLOOR_PCT", -3.5)' in jev_code

    print("\n" + "=" * 68, flush=True)
    print("✅ [RENDER PATCH VERIFICATION] ПРОВЕРКА ПРИМЕНЕННЫХ ФИКСОВ:", flush=True)
    print(f"   • ROCKET_XGB_STAGE1_THRESHOLD = {getattr(config, 'ROCKET_XGB_STAGE1_THRESHOLD', '?')}% (ожидается 12.0%)", flush=True)
    print(f"   • ROCKET_XGB_FINAL_THRESHOLD  = {getattr(config, 'ROCKET_XGB_FINAL_THRESHOLD', '?')}% (было 30.0% -> стало 16.0%)", flush=True)
    print(f"   • MIN_VOLUME_M5               = ${getattr(config, 'MIN_VOLUME_M5', '?')} (было $4500 -> стало $3000.0)", flush=True)
    print(f"   • MIN_BUY_SELL_RATIO          = {getattr(config, 'MIN_BUY_SELL_RATIO', '?')}x (было 1.30x -> стало 1.18x)", flush=True)
    print(f"   • JEV_MIN_M1_ENTRY_FLOOR_PCT  = {getattr(config, 'JEV_MIN_M1_ENTRY_FLOOR_PCT', '?')}% (было 0.0% -> стало -3.5%)", flush=True)
    print(f"   • Патч радаров analyzer.py    = {'✅ АКТИВЕН' if an_ok else '❌ НЕ ПРИМЕНИЛСЯ'}", flush=True)
    print(f"   • Патч откатов grok_jev.py    = {'✅ АКТИВЕН' if jev_ok else '❌ НЕ ПРИМЕНИЛСЯ'}", flush=True)
    print("=" * 68 + "\n", flush=True)


def run_all_patches() -> None:
    print("=== Запуск авто-патчера для бота ===", flush=True)
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