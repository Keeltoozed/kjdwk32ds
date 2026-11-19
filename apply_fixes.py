# -*- coding: utf-8 -*-
"""
apply_fixes.py — Безопасный no-op верификатор для Render-деплоя.
ВНИМАНИЕ: все параметры стратегии, стопов, мунбэга (+110%) и анти-раг гейтов
теперь заданы напрямую в config.py / exit_managers.py / analyzer.py / grok_jev.py / main.py.
Скрипт НЕ перезаписывает исходные файлы, чтобы не откатывать актуальные фиксы при деплое.
"""


def verify_on_render() -> None:
    import importlib
    import config
    importlib.reload(config)

    print("\n" + "=" * 68, flush=True)
    print("✅ [RENDER VERIFICATION] АКТИВНЫЕ ПАРАМЕТРЫ СТРАТЕГИИ (config.py):", flush=True)
    print(
        f"   • STOP_LOSS / EMERGENCY       = {getattr(config, 'STOP_LOSS_PCT', 0)*100:.0f}% / {getattr(config, 'EMERGENCY_CAP_PCT', 0)*100:.0f}%",
        flush=True,
    )
    print(
        f"   • BREAKEVEN_PCT               = +{getattr(config, 'BREAKEVEN_PCT', 0)*100:.0f}%",
        flush=True,
    )
    print(
        f"   • TP1 / TP2 (Moonbag)         = +{getattr(config, 'TP1_TRIGGER_PCT', 0)*100:.0f}% ({int(getattr(config, 'TP1_SELL_FRAC', 0)*100)}%) / +{getattr(config, 'TP2_TRIGGER_PCT', 0)*100:.0f}% ({int(getattr(config, 'TP2_SELL_FRAC', 0)*100)}%)",
        flush=True,
    )
    print(
        f"   • ROCKET_XGB S1 / FINAL       = {getattr(config, 'ROCKET_XGB_STAGE1_THRESHOLD', '?')}% / {getattr(config, 'ROCKET_XGB_FINAL_THRESHOLD', '?')}%",
        flush=True,
    )
    print(
        f"   • MAX_ENTRY_M1 / M5           = +{getattr(config, 'MAX_ENTRY_M1_PCT', '?')}% / +{getattr(config, 'MAX_ENTRY_M5_PCT', '?')}%",
        flush=True,
    )
    print("=" * 68 + "\n", flush=True)


def run_all_patches() -> None:
    print("=== [apply_fixes] Режим безопасной верификации (файлы не перезаписываются) ===", flush=True)
    verify_on_render()


if __name__ == "__main__":
    run_all_patches()
