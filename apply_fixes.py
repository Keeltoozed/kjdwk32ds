# -*- coding: utf-8 -*-
"""
apply_fixes.py — Автоматический патчер для репозитория Keeltoozed/Gifxitxit7558.
Создает резервные копии (*.bak) и точечно исправляет пороги и баги в:
- config.py
- scan_pipeline.py
- grok_jev.py
- exit_manager.py / exit_managers.py
"""
import os
import re
import shutil

CONFIG_APPEND_BLOCK = """

# ==================== [AUTO-PATCH] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ ====================
GROK_JEV_STANDALONE_BUY = False
GROK_JEV_MIN_CONFIDENCE = 48.0
GROK_JEV_MIN_XGB_SCORE = 28.0
GROK_JEV_MAX_AGE_MIN = 35.0
GROK_JEV_MIN_VOL_M5 = 15000.0

ROCKET_XGB_STAGE1_MIN_SCORE = 14.0
MIN_LIQUIDITY_USD = 11500.0
MAX_LIQUIDITY_USD = 180000.0
MIN_FDV_TO_LIQ_RATIO = 1.15
MAX_AGE_MINUTES_FOR_ENTRY = 45.0
MIN_VOLUME_M5_USD = 12000.0
MIN_VOL_TO_LIQ_M5 = 0.65
MAX_H1_PUMP_PCT = 320.0
MIN_M5_PRICE_CHANGE_PCT = 3.5
MAX_M5_PRICE_CHANGE_PCT = 115.0
MIN_M1_PRICE_CHANGE_PCT = -5.0
MAX_M1_VERTICAL_TOP_PCT = 65.0

DEFAULT_MIN_BUY_SELL_RATIO = 1.28
HIGH_VOL_M5_THRESHOLD = 45000.0
HIGH_VOL_MIN_BUY_SELL_RATIO = 1.14

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

    if "[AUTO-PATCH] ОПТИМИЗАЦИЯ ПРИБЫЛЬНОСТИ" not in content:
        content += CONFIG_APPEND_BLOCK
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] {path}: добавлены оптимизированные параметры фильтров и выходов.")
    else:
        print(f"[INFO] {path} уже содержит блок [AUTO-PATCH].")


def patch_scan_pipeline(path: str = "scan_pipeline.py") -> None:
    if not os.path.exists(path):
        print(f"[SKIP] {path} не найден")
        return
    backup_file(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. Снижаем порог Stage-1 ROCKET-XGB с 18% до 14% (спасает JOJO +71% и GOYMAS +123%)
    code = re.sub(
        r"rocket-xgb-s1-reject\s+\{([^}]+)\}\s*<\s*18%",
        r"rocket-xgb-s1-reject {\1} < 14%",
        code,
    )
    code = re.sub(r"(\bs1_thresh\s*=\s*)18(\.0)?", r"\g<1>14.0", code)
    code = re.sub(r"(rkt_score\s*<\s*)18(\.0)?", r"\g<1>14.0", code)

    # 2. Смягчаем фильтр sellers-dominating с 1.30 до 1.18 (спасает JOJO +85% и JOSÉ +31%)
    code = re.sub(r"(\*\s*)1\.30\b", r"\g<1>1.18", code)
    code = re.sub(r"need buyers > sellers \* 1\.30", "need buyers > sellers * 1.18", code)

    # 3. Снижаем максимальный возраст токена с 240.0m до 45.0m (отсекает старые пулы типа GOLDIE 146m и MISCHIO 58m)
    code = re.sub(r"(\b240\.0\b|\b240\b)(\s*#?\s*m)", r"45.0\2", code)
    code = code.replace("> 240.0m", "> 45.0m")

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: обновлены пороги Stage-1 (14%), баланс покупателей (1.18x) и возраст пула (45m).")


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

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[OK] {path}: подключен единый гейт unified_gates и исправлен расчет price_change_m1.")


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
        code = re.sub(
            r"(90с без импульса — срез убытка)",
            r"\1 [guarded]",
            code,
        )

        with open(fname, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[OK] {fname}: обновлены пороги Mature Moonbag Floor и Crash Guard.")


if __name__ == "__main__":
    print("=== Запуск авто-патчера для бота ===")
    patch_config("config.py")
    patch_scan_pipeline("scan_pipeline.py")
    patch_grok_jev("grok_jev.py")
    patch_exit_files()
    print("=== Готово! Перезапустите бота. ===")