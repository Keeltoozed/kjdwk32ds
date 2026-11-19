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
    print(
        f"   • CURVE_MIN_LIQ_USD / UNGRAD  = ${getattr(config, 'CURVE_MIN_LIQ_USD', 0):,.0f} / {getattr(config, 'ALLOW_UNGRADUATED_PUMPFUN', '?')} (SNIPER={getattr(config, 'SNIPER_ENTRIES_ENABLED', '?')})",
        flush=True,
    )
    print(
        f"   • MIN_GRADUATED_LIQ / SLOTS   = ${getattr(config, 'MIN_GRADUATED_LIQ_USD', 0):,.0f} / {getattr(config, 'MAX_CONCURRENT_POSITIONS', '?')} slots",
        flush=True,
    )
    print(
        f"   • STREAK COOLDOWN / FCR PS    = {getattr(config, 'STREAK_COOLDOWN_ENABLED', '?')} ({getattr(config, 'STREAK_MAX_CONSECUTIVE_LOSSES', '?')}L/{getattr(config, 'STREAK_COOLDOWN_MINUTES', '?')}m) / {getattr(config, 'FCR_CANONICAL_PUMPSWAP_DURATION_SEC', '?')}s",
        flush=True,
    )
    print(
        f"   • STAIRCASE / SLOW-COOK EXIT  = {getattr(config, 'AUDITOR_STAIRCASE_ENABLED', '?')} / {getattr(config, 'SLOW_COOK_PROFIT_EXIT_MIN', '?')}m-{getattr(config, 'SLOW_COOK_HARD_EXIT_MIN', '?')}m",
        flush=True,
    )
    print(
        f"   • SHADOW_WATCH_LIMIT          = {getattr(config, 'SHADOW_WATCH_LIMIT', '?')}",
        flush=True,
    )
    try:
        import inspect
        import analyzer as _an_v
        import tracker as _tr_v
        _has_an_gate = "curve-thin-liq" in inspect.getsource(_an_v.Analyzer._analyze_token_inner)
        _has_tr_fw = "FIREWALL-CURVE-THIN-LIQ" in inspect.getsource(_tr_v.PaperTracker.add_position)
        print(
            f"   • GATES ACTIVE                = analyzer.curve-thin-liq:{'OK' if _has_an_gate else 'MISSING'} | tracker.firewall:{'OK' if _has_tr_fw else 'MISSING'}",
            flush=True,
        )
    except Exception as _ve:
        print(f"   • GATES CHECK ERR             = {_ve}", flush=True)
    print("=" * 68 + "\n", flush=True)


def run_all_patches() -> None:
    print("=== [apply_fixes] Режим безопасной верификации (файлы не перезаписываются) ===", flush=True)
    verify_on_render()


if __name__ == "__main__":
    run_all_patches()
