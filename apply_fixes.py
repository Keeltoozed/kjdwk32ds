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
        import os
        _base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(_base_dir, "analyzer.py"), encoding="utf-8") as _f:
            _an_src = _f.read()
        with open(os.path.join(_base_dir, "tracker.py"), encoding="utf-8") as _f:
            _tr_src = _f.read()
        with open(os.path.join(_base_dir, "market_data.py"), encoding="utf-8") as _f:
            _md_src = _f.read()
        with open(os.path.join(_base_dir, "goplus.py"), encoding="utf-8") as _f:
            _gp_src = _f.read()
        with open(os.path.join(_base_dir, "first_candle_rug_guard.py"), encoding="utf-8") as _f:
            _fcr_src = _f.read()
        with open(os.path.join(_base_dir, "main.py"), encoding="utf-8") as _f:
            _mn_src = _f.read()
        _has_an_gate = "curve-thin-liq" in _an_src and "try_claim_buy_flight" in _an_src and "bad-word-scam" in _an_src
        _has_tr_fw = "FIREWALL-CURVE-THIN-LIQ" in _tr_src
        _has_gt_fast = "get_pool_trades_cached" in _md_src and "max_wait" in _md_src and "get_pool_trades_cached" in _fcr_src
        _has_cabal_guard = "check_rugcheck_report_insiders" in _gp_src and "atomized-cabal-farm" in _an_src
        _has_onchain_mig = (
            "async def onchain_migration_feeder_loop" in _mn_src
            and '"onchain_migration_feeder"' in _mn_src
            and "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg" in _mn_src
            and "is_pump_base" in _mn_src
        )
        _has_ps_usdc = (
            "_CANONICAL_QUOTES" in _gp_src
            and "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" in _gp_src
            and hasattr(config, "is_established_breakout_runner")
        )
        _has_fresh_h1_jupfb = (
            "lite-api.jup.ag/tokens/v2/search?query=" in _an_src
            and float(config.get_max_entry_h1_pct(1.0, 10000.0, 50, 20, 12000.0, 35.0, fdv_usd=35000.0, is_curve=False)) >= 700.0
        )
        print(
            f"   • GATES ACTIVE                = analyzer.fast-gates:{'OK' if _has_an_gate else 'MISSING'} | tracker.firewall:{'OK' if _has_tr_fw else 'MISSING'} | gt.fail-fast:{'OK' if _has_gt_fast else 'MISSING'} | cabal.l7a:{'OK' if _has_cabal_guard else 'MISSING'} | onchain.mig-rpc:{'OK' if _has_onchain_mig else 'MISSING'} | ps.usdc-canon:{'OK' if _has_ps_usdc else 'MISSING'} | fresh-h1+jup-pf:{'OK' if _has_fresh_h1_jupfb else 'MISSING'}",
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
