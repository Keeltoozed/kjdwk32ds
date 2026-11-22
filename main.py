import sys
import os

# Гарантируем немедленный вывод логов на Render / Docker (без задержек буферизации stdout)
os.environ["PYTHONUNBUFFERED"] = "1"
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

import asyncio
import threading
import time
import json
import streamlit as st
import pandas as pd
import config
from analyzer import Analyzer
from rug_wallet_analysis import quick_rug_check, full_rug_wallet_audit
from wallet_behavior_monitor import (init_position_wallet_state, remove_position_state,
                                    monitor_position_wallets, _POSITION_WALLET_STATE)
from tracker import PaperTracker
from fomo_scanner import fomo_loop

# === 1. ФОНОВЫЙ ТОРГОВЫЙ БОТ ===
async def position_security_auditor_loop(analyzer, tracker):
    """Фоновый аудит безопасности открытых позиций (Honeypot, Freeze, Liquidity Drain).
    Работает РАЗДЕЛЬНО от position_manager_loop (раз в 30-40 сек), чтобы сетевые проверки
    Jupiter и GoPlus НИКОГДА не задерживали секундный мониторинг цен и стоп-лоссов."""
    print("🕵️ Запуск фонового аудитора безопасности позиций (Honeypot & Liq Drain Guard)...")
    await asyncio.sleep(8.0)
    while True:
        try:
            open_positions = tracker.get_open_positions()
            # Очищаем состояния мониторинга кошельков для закрытых позиций
            _open_mints = set(open_positions.keys())
            for _stale_mint in list(_POSITION_WALLET_STATE.keys()):
                if _stale_mint not in _open_mints:
                    remove_position_state(_stale_mint)
            for mint, position in list(open_positions.items()):
                if mint.startswith("0x") or getattr(position, "chain", "solana") != "solana":
                    continue
                now_ts = time.time()
                minutes_held = (now_ts - getattr(position, "entry_time", now_ts)) / 60.0
                
                # 1. Проверка продаваемости через Jupiter (Honeypot / Freeze)
                _last_chk = getattr(position, "last_security_check_ts", 0.0)
                if minutes_held >= 0.75 and (now_ts - _last_chk) > 35:
                    position.last_security_check_ts = now_ts
                    try:
                        from jupiter import JupiterAPI
                        _jup_ok, _jup_why = await JupiterAPI.is_token_sellable(mint)
                        if not _jup_ok:
                            _cur_px_chk = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                            # Защита от ложного списания (кейс UNPOOLED +2000%): если цена активна и есть ликвидность, не списываем в 0!
                            if _cur_px_chk > 0 and position.amount_usd > 0:
                                print(f"⚠️ [JUP-QUOTE-WARN] {position.symbol} ({mint[:10]}): временная задержка маршрута Jupiter ({_jup_why}), но цена ${ _cur_px_chk:.8f} активна — позиция удерживается.")
                            else:
                                _strikes = getattr(position, "unsellable_strikes", 0) + 1
                                position.unsellable_strikes = _strikes
                                if _strikes >= 3:
                                    print(f"🚨 [SOL-SELL-BLOCKED] {position.symbol} ({mint[:10]}): {_jup_why} (подтверждено {_strikes}x) — токен непродаваем/рагпулл, списание в 0.0$ (-100%).")
                                    tracker.close_position(mint, 0.0, f"SOL Rug Pull / Unsellable ({_jup_why})")
                                    continue
                                else:
                                    print(f"⚠️ [SOL-SELL-WARN] {position.symbol} ({mint[:10]}): {_jup_why} (страйк {_strikes}/3, проверка через 35с)")
                        else:
                            position.unsellable_strikes = 0
                    except Exception:
                        pass
                    
                    try:
                        import goplus as _gp
                        _g_ok, _g_why = await _gp.check_solana(mint)
                        _w_low = str(_g_why or "").lower()
                        if not _g_ok and any(k in _w_low for k in ("honeypot", "cannot_sell", "freeze", "freezable", "closable", "balance_mutable", "frozen", "non_transferable", "transfer_hook", "default_account")):
                            print(f"🚨 [SOL-HONEYPOT] {position.symbol} ({mint[:10]}): {_g_why} — токен непродаваем/заморожен, списание в -100%.")
                            tracker.close_position(mint, 0.0, f"SOL Honeypot / Unsellable ({_g_why})")
                            continue
                    except Exception:
                        pass

                # 2. Ранний перехват вывода ликвидности (Early Liquidity Drain Escape - 0.5s Live Protection)
                _last_lchk = getattr(position, "last_liq_chk", 0.0)
                if (now_ts - _last_lchk) > 0.5:
                    position.last_liq_chk = now_ts
                    try:
                        import market_data as _md
                        _td_sol = await asyncio.wait_for(_md.get_token_data(mint, max_cache_age=2.5), timeout=1.5)
                        if _td_sol:
                            _dex_sol = str(_td_sol.get("dexId") or "").lower()
                            _mint_sol = str(mint or "").lower()
                            _is_curve_sol = _mint_sol.endswith("pump") and _dex_sol in ("", "pump", "pumpfun", "pump-amm")

                            if _is_curve_sol:
                                # На нативной pump.fun кривой ликвидность залочена в смарт-контракте — дев физически не может снять пул!
                                # DexScreener часто отдаёт liq=None или liq=0 для pump.fun (это ложно выбивало ракету 莽小六 +518% в -99.99%).
                                pass
                            else:
                                _liq_obj = _td_sol.get("liquidity") or {}
                                _has_liq_data = bool(_liq_obj and ("usd" in _liq_obj or "quote" in _liq_obj))
                                _sol_liq = float((_liq_obj.get("usd", 0) or 0))
                                _quote_liq = float((_liq_obj.get("quote", 0) or 0))
                                _q_sym = str(((_td_sol.get("quoteToken") or {}).get("symbol") or "")).upper()

                                _init_liq = float(getattr(position, "initial_liquidity_usd", 0.0) or 0.0)
                                if _init_liq <= 0 and _sol_liq > 0:
                                    position.initial_liquidity_usd = _sol_liq
                                    _init_liq = _sol_liq
                                _peak_liq = float(getattr(position, "peak_liquidity_usd", 0.0) or _init_liq or 0.0)
                                if _sol_liq > _peak_liq:
                                    position.peak_liquidity_usd = _sol_liq
                                    _peak_liq = _sol_liq

                                # Проверка потери ликвидности (0 ликвидности, слив девом или падение пула):
                                _is_zero_liq = (_sol_liq <= 0.0)
                                _crit_drained = (_sol_liq <= 200.0) or (_q_sym in ("SOL", "WSOL") and _quote_liq < 1.0)
                                _liq_crash_pct = ((_peak_liq - _sol_liq) / _peak_liq) if _peak_liq > 0 else 0.0
                                _cfg_drain_exit = float(getattr(config, "LIQ_DRAIN_EXIT_PCT", 0.40))
                                _cfg_drain_fast = float(getattr(config, "LIQ_DRAIN_FAST_PCT", 0.30))

                                # МГНОВЕННЫЙ выход: ликва просела больше чем на LIQ_DRAIN_EXIT_PCT от пика — не ждём обнуления!
                                # Для сожжённых/залоченных пулов (каноничный PumpSwap / Raydium LP lock>=80%) дев физически не может вывести LP,
                                # а просадка USD-ликвидности на 30% в AMM (xy=k) — это обычное движение цены, которое режет штатный Stop-Loss (-10%), а не паник-сейл в -54%!
                                _is_rh_pos_aud = False
                                try:
                                    import rug_hunter as _rh_aud
                                    _is_rh_pos_aud = _rh_aud.is_rug_hunter_position(position)
                                except Exception:
                                    _is_rh_pos_aud = False
                                _is_locked_amm = (not _is_rh_pos_aud) and (bool(getattr(position, "is_lp_locked_80", False)) or not bool(getattr(position, "is_unlocked_pool", False)))
                                _rh_liq_thr = float(getattr(config, "RUG_HUNTER_LIQ_DRAIN_PCT", 0.15))
                                _eff_drain_exit = _rh_liq_thr if _is_rh_pos_aud else (0.68 if _is_locked_amm else _cfg_drain_exit)
                                _eff_drain_fast = _rh_liq_thr if _is_rh_pos_aud else (0.60 if _is_locked_amm else _cfg_drain_fast)
                                _emergency_exit = _is_zero_liq or _crit_drained or (_liq_crash_pct >= _eff_drain_exit and _peak_liq >= 3000.0)
                                # Экстренный случай: за один тик ликва обвалилась — паник-сейл сразу
                                _fast_drain = _liq_crash_pct >= _eff_drain_fast and _peak_liq >= 3000.0

                                if _has_liq_data and (_is_zero_liq or _crit_drained):
                                    # ЗАЩИТА ОТ ЛОЖНОГО СПИСАНИЯ В -101% (кейс HOLM: спам-пул $3 поверх живой кривой, после чего цена +77%):
                                    # 1) Для .pump проверяем живое состояние бондинг-кривой ончейн: если кривая не закрыта и в ней >= 1 SOL — это не раг!
                                    _rescued_live_curve = False
                                    if _mint_sol.endswith("pump"):
                                        try:
                                            _st_dr = await asyncio.wait_for(_md.get_live_pump_curve_state(mint), timeout=1.5)
                                            if _st_dr and not _st_dr.get("complete") and float(_st_dr.get("real_sol", 0) or 0) >= 1.0 and float(_st_dr.get("price_usd", 0) or 0) > 0:
                                                position.current_price_usd = float(_st_dr["price_usd"])
                                                position._zero_liq_streak = 0
                                                _rescued_live_curve = True
                                        except Exception:
                                            pass
                                    if _rescued_live_curve:
                                        continue
                                    # 2) Для AMM-пулов проверяем ончейн PumpSwap / требуем 2 тика подтверждения (0.5с) против разового глюка DexScreener:
                                    _onchain_alive = False
                                    if _dex_sol == "pumpswap":
                                        try:
                                            _ps_dr = await asyncio.wait_for(
                                                _md.get_live_pumpswap_pool_state(mint, str(getattr(position, "pair_address", "") or _td_sol.get("pairAddress") or "")),
                                                timeout=1.5,
                                            )
                                            if _ps_dr and float(_ps_dr.get("liq_usd", 0) or 0) >= 500.0 and float(_ps_dr.get("price_usd", 0) or 0) > 0:
                                                position.current_price_usd = float(_ps_dr["price_usd"])
                                                position._zero_liq_streak = 0
                                                _onchain_alive = True
                                        except Exception:
                                            pass
                                    if _onchain_alive:
                                        continue
                                    _zl_cnt = int(getattr(position, "_zero_liq_streak", 0) or 0) + 1
                                    position._zero_liq_streak = _zl_cnt
                                    # Катастрофический дрейн: если ликва рухнула до <= $50 при пике >= $2500 или просадка >= 90% — выход СРАЗУ на 1 тике!
                                    _is_catastrophic_drain = (_sol_liq <= 50.0 and _peak_liq >= 2500.0) or _liq_crash_pct >= 0.90
                                    if _zl_cnt < 2 and not _is_catastrophic_drain:
                                        continue
                                    # Физическое обнуление пула девом (LP <= $200 или 0, подтверждено 2 тика / ончейн) — выход МГНОВЕННО
                                    _px_now = 0.0 if (_is_zero_liq or _crit_drained or _sol_liq <= 250.0) else float(getattr(position, "current_price_usd", 0.0) or 0.0)
                                    _msg = f"Zero/Drained Liq ${_sol_liq:.0f}"
                                    print(f"🚨 [LIQ-LOSS] {position.symbol} ({mint[:8]}): {_msg} (подтверждено {_zl_cnt} тика) — выход МГНОВЕННО!")
                                    tracker.close_position(mint, _px_now, f"Liquidity Drain Rug (Liq ${_sol_liq:.0f} from ${_peak_liq:.0f})")
                                    continue
                                elif _has_liq_data and (_emergency_exit or _fast_drain):
                                    position._zero_liq_streak = 0
                                    # Защита от разового тика/шума DexScreener (кейс AUDACITY: просадка 33% на 0м -> паник-сейл в -54%):
                                    # требуем 2 тика подтверждения, если ликвидность всё ещё > $1,000
                                    _drain_cnt = getattr(position, "_fast_drain_streak", 0) + 1
                                    position._fast_drain_streak = _drain_cnt
                                    if _drain_cnt >= 2 or _sol_liq <= 800.0 or _liq_crash_pct >= 0.70:
                                        _px_now = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                                        _tag = "Liquidity Drain Escape" if _emergency_exit else "Fast Liquidity Drain Panic"
                                        _msg = f"Liq Crash ${_sol_liq:.0f} from ${_peak_liq:.0f} (-{_liq_crash_pct*100:.0f}%)"
                                        print(f"🚨 [LIQ-LOSS] {position.symbol} ({mint[:8]}): {_msg} (подтверждено 2 тика) — выход!")
                                        tracker.close_position(mint, _px_now, f"{_tag} (Liq dropped to ${_sol_liq:.0f} from ${_peak_liq:.0f})")
                                        continue
                                elif _has_liq_data and _liq_crash_pct >= 0.25 and _peak_liq >= 3000.0:
                                    position._fast_drain_streak = 0
                                    # Умеренный дроп 25%+: требуем 3 тика устойчивого подтверждения (не режем на рыночных шумах и кэше DexScreener)
                                    _drop_cnt = getattr(position, "_liq_drop_streak", 0) + 1
                                    position._liq_drop_streak = _drop_cnt
                                    if _drop_cnt >= 3:
                                        _px_now = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                                        print(f"🚨 [LIQ-LOSS] {position.symbol} ({mint[:8]}): Liq Drop ${_sol_liq:.0f} from ${_peak_liq:.0f} (-{_liq_crash_pct*100:.0f}%) — подтверждённый выход (3 тика)!")
                                        tracker.close_position(mint, _px_now, f"Confirmed Liquidity Drop (Liq ${_sol_liq:.0f} from ${_peak_liq:.0f})")
                                        continue
                                else:
                                    position._zero_liq_streak = 0
                                    position._fast_drain_streak = 0
                                    position._liq_drop_streak = 0

                            # 2.5 🎯 Лайв-монитор рагера и многокошелькового слива для позиций RUG-HUNTER (каждые 1.5с):
                            try:
                                import rug_hunter as _rh_live
                                if _rh_live.is_rug_hunter_position(position):
                                    _rh_last_t = float(getattr(position, "_rh_live_chk_ts", 0.0) or 0.0)
                                    if (now_ts - _rh_last_t) >= 1.5:
                                        position._rh_live_chk_ts = now_ts
                                        _rh_exit_now, _rh_exit_why = await _rh_live.check_live_rugger_activity(position, _td_sol)
                                        if _rh_exit_now:
                                            _px_rh = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                                            print(f"🚨 [RUG-HUNTER-LIVE] {position.symbol} ({mint[:8]}): {_rh_exit_why} — экстренный выход!")
                                            tracker.close_position(mint, _px_rh, _rh_exit_why)
                                            continue
                            except Exception:
                                pass

                            # 3. Мониторинг кошельков (сиビル, флипперы, аномальная активность после входа)
                            _wm_chk = getattr(position, "last_wallet_mon_chk", 0.0)
                            if (now_ts - _wm_chk) > float(getattr(config, "WALLET_MONITOR_INTERVAL_SEC", 10)):
                                position.last_wallet_mon_chk = now_ts
                                try:
                                    from solana.rpc.async_api import AsyncClient as _AC
                                    _wm_client = _AC(config.HELIUS_RPC_URL)
                                    _wm_exit, _wm_why, _wm_det = await monitor_position_wallets(_wm_client, mint)
                                    try:
                                        await _wm_client.close()
                                    except Exception:
                                        pass
                                    if _wm_exit:
                                        _px_now = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                                        print(f"🚨 [WALLET-MON] {position.symbol} ({mint[:8]}): {_wm_why} — выход по поведению кошельков!")
                                        tracker.close_position(mint, _px_now, f"Wallet Behavior Alert ({_wm_why})")
                                        continue
                                except Exception as _wm_e:
                                    print(f"⚠️ [WALLET-MON] {position.symbol} ({mint[:8]}): {type(_wm_e).__name__}: {_wm_e}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ Ошибка в position_security_auditor_loop: {e}")
        await asyncio.sleep(0.5)


def _update_confirmed_peak_px(position, current_price: float, prev_price: float) -> tuple:
    """Confirm >=18% single-tick upward spikes over 2 ticks before raising max_price_usd.
    Returns (suppress_eval_peak, pre_tick_confirmed_px) so 1-tick wicks do not arm tight ATH floors."""
    _pre_conf = float(getattr(position, "_confirmed_px", 0.0) or prev_price or getattr(position, "entry_price_usd", 0.0) or 0.0)
    if getattr(position, "max_price_usd", 0.0) <= 0:
        position.max_price_usd = current_price
        position.peak_time = time.time()
        return False, _pre_conf
    _peak_ref = max(float(position.max_price_usd or 0.0), _pre_conf, float(getattr(position, "entry_price_usd", 0.0) or 0.0))
    _pend_px = float(getattr(position, "_pending_peak_px", 0.0) or 0.0)
    if _pend_px > 0.0:
        if current_price >= _pend_px * 0.88:
            if current_price > _pend_px * 1.18:
                # Confirm Tick 1 (_pend_px), but require another tick before trusting the new +18% jump
                if _pend_px > position.max_price_usd:
                    position.max_price_usd = _pend_px
                    position.peak_time = time.time()
                _new_conf = max(_pre_conf, _pend_px)
                position._confirmed_px = _new_conf
                position._pending_peak_px = current_price
                return True, _new_conf
            _hi = max(float(position.max_price_usd or 0.0), _pend_px, current_price)
            if _hi > position.max_price_usd:
                position.max_price_usd = _hi
                position.peak_time = time.time()
            position._pending_peak_px = 0.0
            return False, _pre_conf
        position._pending_peak_px = 0.0
    if current_price > position.max_price_usd:
        if _peak_ref > 0 and current_price > _peak_ref * 1.18:
            position._pending_peak_px = current_price
            return True, _pre_conf
        position.max_price_usd = current_price
        position.peak_time = time.time()
    return False, _pre_conf


async def position_manager_loop(analyzer, tracker):
    from jupiter import JupiterAPI
    print("🛡️ Запуск менеджера позиций (быстрый трекинг Stop-Loss)...")
    while True:
        try:
            open_positions = tracker.get_open_positions()
            
            _now_sec = time.time()
            if not hasattr(position_manager_loop, "_last_heartbeat"):
                position_manager_loop._last_heartbeat = 0.0
            if (_now_sec - position_manager_loop._last_heartbeat) >= 30.0:
                position_manager_loop._last_heartbeat = _now_sec
                _n_open = len(open_positions)
                _cap = tracker.get_total_capital() if hasattr(tracker, "get_total_capital") else getattr(tracker, 'cash', 0.0)
                print(f"🛡️ [HEARTBEAT] Менеджер позиций активен: {_n_open} открытых позиций, капитал ${_cap:.2f}", flush=True)
            
            # ОПТИМИЗАЦИЯ СКОРОСТИ: Запрашиваем цены для ВСЕХ позиций ОДНИМ запросом с жестким таймаутом и привязкой к пулу входа.
            mints_to_fetch = [m for m, p in open_positions.items()
                              if not m.startswith("0x")
                              and getattr(p, "chain", "solana") == "solana"]
            pair_hints = {m: getattr(p, "pair_address", "") for m, p in open_positions.items() if getattr(p, "pair_address", "")}
            bulk_prices = {}
            if mints_to_fetch:
                try:
                    bulk_prices = await asyncio.wait_for(JupiterAPI.get_prices(mints_to_fetch, pair_hints=pair_hints, priority=True), timeout=2.0)
                except Exception:
                    bulk_prices = {}
            
            for mint, position in list(open_positions.items()):
                # Robinhood (0x...) ведёт отдельный robinhood_loop со своими ценами DS
                if mint.startswith("0x") or getattr(position, "chain", "solana") != "solana":
                    continue

                # 1. Приоритет №1: Свежий WSS-стрим из PumpPortal (нулевая задержка, миллисекунды)
                # Любая свежая WSS котировка (< 5с) имеет высший приоритет для мгновенного стопа/тейка
                wss_px = float(getattr(position, "wss_price", 0.0) or 0.0)
                wss_ts = float(getattr(position, "wss_price_ts", 0.0) or 0.0)
                wss_fresh = wss_px > 0.0 and (time.time() - wss_ts) < 5.0

                api_price = 0.0
                if wss_fresh:
                    current_price = wss_px
                else:
                    # 2. Берем цену из Raydium/Gecko/DS балка
                    api_price = bulk_prices.get(mint, 0.0)
                    if not api_price:
                        # Добор только если нет цены и с жестким таймаутом 1.5с (не вешаем Stop-Loss луп!)
                        try:
                            import market_data as _md
                            _td0 = await asyncio.wait_for(_md.get_token_data(mint, max_cache_age=5.0, priority=True), timeout=1.5)
                            api_price = float((_td0.get("priceUsd", 0) or 0)) if _td0 else 0.0
                        except Exception:
                            pass
                    if api_price > 0.0:
                        current_price = api_price
                        position.last_live_price_ts = time.time()
                    else:
                        # Если оба источника не дали цену в этом тике — проверяем насколько стара цена
                        _last_known = float(getattr(position, "current_price_usd", 0.0) or getattr(position, "entry_price_usd", 0.0) or 0.0)
                        _last_live_ts = float(getattr(position, "last_live_price_ts", 0.0) or position.entry_time)
                        _stale_sec = time.time() - _last_live_ts
                        _last_fb_try = float(getattr(position, "_last_jup_fb_ts", 0.0) or 0.0)
                        if _stale_sec > 8.0 and (time.time() - _last_fb_try) >= 8.0:
                            position._last_jup_fb_ts = time.time()
                            # Пробуем запросить ончейн: сначала Pump.fun bonding curve (фикс Dead Stale Pool Cut на Etch/藏狐), затем Jupiter и свежий Pair Data
                            _fb_px = 0.0
                            _pos_mf_fb = getattr(position, "ml_features", None) or {}
                            _pos_dex_fb = str(_pos_mf_fb.get("dex") or getattr(position, "dex_id", "") or "").lower()
                            if _pos_dex_fb in ("pump", "pumpfun") or mint.lower().endswith("pump"):
                                try:
                                    import market_data as _md_fb
                                    _st_fb = await asyncio.wait_for(_md_fb.get_live_pump_curve_state(mint), timeout=1.5)
                                    if _st_fb and not _st_fb.get("complete") and float(_st_fb.get("price_usd", 0) or 0) > 0:
                                        _fb_px = float(_st_fb["price_usd"])
                                except Exception:
                                    _fb_px = 0.0
                            if _fb_px <= 0.0:
                                try:
                                    from jupiter import JupiterAPI
                                    _fb_px = float(await asyncio.wait_for(JupiterAPI.get_price(mint), timeout=1.5) or 0.0)
                                except Exception:
                                    _fb_px = 0.0
                            if _fb_px <= 0.0:
                                try:
                                    import market_data as _md_fb
                                    _td_fb = await asyncio.wait_for(_md_fb.get_token_data(mint, max_cache_age=0.0), timeout=1.5)
                                    _fb_px = float((_td_fb.get("priceUsd", 0) or 0)) if _td_fb else 0.0
                                except Exception:
                                    _fb_px = 0.0
                            if _fb_px > 0:
                                current_price = _fb_px
                                api_price = _fb_px
                                position.current_price_usd = _fb_px
                                position.last_live_price_ts = time.time()
                                position.price_updated_at = time.time()
                            else:
                                current_price = _last_known
                        else:
                            current_price = _last_known

                        # Если котировок нет больше 180 сек — проверяем продаваемость через Jupiter
                        if api_price <= 0.0 and _stale_sec > 180.0:
                            try:
                                from jupiter import JupiterAPI
                                _sell_ok, _sell_why = await JupiterAPI.is_token_sellable(mint)
                                if not _sell_ok:
                                    tracker.close_position(mint, 0.0, f"Dead Stale Pool Cut ({_stale_sec:.0f}s no quotes / {_sell_why})")
                                    continue
                                else:
                                    _j_live = await JupiterAPI.get_price(mint)
                                    if _j_live > 0:
                                        current_price = _j_live
                                        position.current_price_usd = _j_live
                                        position.last_live_price_ts = time.time()
                                        position.price_updated_at = time.time()
                                        continue
                            except Exception:
                                pass
                            tracker.close_position(mint, current_price if current_price > 0 else _last_known, f"Dead Stale Pool Cut ({_stale_sec:.0f}s no quotes)")
                            continue
                
                # Всегда фиксируем время активного мониторинга позиции менеджером
                position.price_checked_at = time.time()
                if api_price > 0.0 or wss_fresh:
                    position.price_updated_at = time.time()

                # Обновляем живую цену для GROWTH/EARLY-позиций прямо здесь (каждую секунду),
                # а исполнение медленных стопов/стагнантов оставляет growth_loop
                if str(getattr(position, "source", "")).startswith("GROWTH") or str(getattr(position, "source", "")).startswith("EARLY"):
                    if current_price > 0.0:
                        position.current_price_usd = current_price
                        if current_price > position.max_price_usd:
                            position.max_price_usd = current_price
                        if position.entry_price_usd > 0:
                            _pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd
                            position.current_pnl_usd = position.amount_usd * _pnl_pct
                    continue

                prev_price = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                if current_price <= 0.0:
                    minutes_held = (time.time() - position.entry_time) / 60
                    _fz = getattr(position, "first_zero_ts", 0.0) or time.time()
                    position.first_zero_ts = _fz
                    # Если цена 0.0 больше 45 секунд — проверяем продаваемость через Jupiter
                    if (time.time() - _fz) > 45:
                        from jupiter import JupiterAPI
                        _sell_ok, _sell_why = await JupiterAPI.is_token_sellable(mint)
                        if not _sell_ok:
                            print(f"🚨 [SOL-ZERO-RUG] {position.symbol} ({mint[:8]}): цена 0.00 и токен подтвержденно непродаваем ({_sell_why}) — списание в 0.0$ (-100%).")
                            tracker.close_position(mint, 0.0, f"Rug Pull / Unsellable ({_sell_why})")
                            continue
                        else:
                            # Токен ПРОДАВАЕМ на Jupiter! Значит ликвидность ЕСТЬ, и это НЕ рагпулл!
                            # Запрашиваем живую ончейн-цену Jupiter и восстанавливаем позицию
                            _jup_px = await JupiterAPI.get_price(mint)
                            if _jup_px > 0:
                                current_price = _jup_px
                                position.current_price_usd = _jup_px
                                position.first_zero_ts = 0.0
                                position.price_updated_at = time.time()
                                print(f"🔄 [PRICE-RECOVERED] {position.symbol} ({mint[:8]}): цена восстановлена через Jupiter ончейн: ${_jup_px:.8f}")
                            elif prev_price > 0:
                                current_price = prev_price
                                position.first_zero_ts = 0.0
                            continue
                    continue
                position.first_zero_ts = 0.0
                    
                # Фантом цены (смена источника DS->node, пыль с liq 0, сбой децималов): один тик
                # +5000% — не реальный рост, а битые данные котировок. Пик не обновляем, трек не ломаем.
                if position.entry_price_usd > 0 and current_price / position.entry_price_usd > 50:
                    _cur_t = time.time()
                    _last_phantom_log = getattr(position, "_last_phantom_log", 0.0)
                    if (_cur_t - _last_phantom_log) >= 30.0:
                        position._last_phantom_log = _cur_t
                        print(f"⚠️ Phantom price {mint[:8]}: x{current_price/position.entry_price_usd:.0f} за тик — аномалия отфильтрована (игнор).")
                    continue
                _suppress_eval_peak, _pre_tick_conf = _update_confirmed_peak_px(position, current_price, prev_price)

                pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd > 0 else 0.0
                max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd > 0 else 0.0
                minutes_held = (time.time() - position.entry_time) / 60

                # Сразу обновляем текущие значения для дашборда и хранилища (чтобы continue ниже не морозили данные в UI)
                position.current_price_usd = current_price
                position.current_pnl_usd = position.amount_usd * pnl_pct

                _last_pos_log = getattr(position, "_last_log_ts", 0.0)
                if (_now_sec - _last_pos_log) >= 10.0:
                    position._last_log_ts = _now_sec
                    _pnl_str = f"{pnl_pct*100:+.1f}%" if position.entry_price_usd > 0 else "0%"
                    print(f"🛡️ [TRACK] {position.symbol} ({mint[:8]}): цена ${current_price:.8f} ({_pnl_str}) | пик: ${position.max_price_usd:.8f} | держим {minutes_held:.1f}м", flush=True)

                # Периодически обновляем живую ликвидность пула и сделки (каждые 8 сек или при просадке <= -4% каждые 2.5 сек)
                _last_pd_ts = float(getattr(position, "last_pair_data_ts", 0.0) or 0.0)
                _now_t = time.time()
                _refresh_interval = 2.5 if pnl_pct <= -0.04 else 8.0
                if (_now_t - _last_pd_ts) >= _refresh_interval:
                    try:
                        import market_data as _md
                        _cached_td = getattr(_md, "_TD_CACHE", {}).get(mint)
                        if _cached_td and isinstance(_cached_td[1], dict) and (time.monotonic() - _cached_td[0]) < 8.0:
                            _live_pd = _cached_td[1]
                        else:
                            _live_pd = await asyncio.wait_for(_md.get_token_data(mint, max_cache_age=4.0, priority=True), timeout=0.8)
                        if isinstance(_live_pd, dict) and _live_pd:
                            position.pair_data = _live_pd
                            position.last_pair_data = _live_pd
                            position.last_pair_data_ts = _now_t
                            _cur_l_chk = float(((_live_pd.get("liquidity") or {}).get("usd", 0) or 0))
                            if _cur_l_chk > 0:
                                if not hasattr(position, "peak_liquidity_usd") or _cur_l_chk > position.peak_liquidity_usd:
                                    position.peak_liquidity_usd = _cur_l_chk
                    except Exception:
                        pass

                # Infant Dump: применяется ТОЛЬКО для нематурных токенов (сырых кривых pump.fun).
                # Зрелые токены (is_mature=True, вроде ASTRALIS) оцениваются ИСКЛЮЧИТЕЛЬНО через MatureExitManager,
                # чтобы обычный спред Bid-Ask (-5%..-10%) на первых секундах не выбивал ракету до её пампа!
                try:
                    if not getattr(position, "is_mature", False):
                        _inf_w = float(getattr(config, "INFANT_WINDOW_MIN", 0.5))
                        _inf_p = -abs(float(getattr(config, "INFANT_DUMP_PCT", -0.090)))
                        if minutes_held < _inf_w and pnl_pct <= _inf_p:
                            tracker.close_position(mint, current_price, f"Infant Dump ({pnl_pct*100:.1f}%)")
                            continue
                except Exception:
                    pass
                
                peak_ts = getattr(position, "peak_time", 0.0) or position.entry_time
                minutes_since_peak = (time.time() - peak_ts) / 60 if peak_ts else 0

                # === ЧАСТИЧНАЯ ФИКСАЦИЯ TP1 (+110%, 50% -> MOONBAG) И TP2 (+250%) ИСПОЛНЯЕТСЯ ДО CRASH GUARD ===
                # Гарантирует, что на взрывной ракете (Buncoin +310%, DCTC +763%, GTASZN +635%) мы ВСЕГДА
                # сначала забираем 50% на +110% (окупаем ставку), а остаток ведём как Risk-Free Moonbag!
                _pd_tmp = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
                _pos_unl_tp = config.is_position_unlocked_lp(
                    mint,
                    _pd_tmp,
                    bool(getattr(position, "is_lp_locked_80", False)),
                )
                position.is_unlocked_pool = _pos_unl_tp
                _is_rh_tp = False
                try:
                    import rug_hunter as _rh_tp_mod
                    _is_rh_tp = _rh_tp_mod.is_rug_hunter_position(position)
                except Exception:
                    _is_rh_tp = False
                if _is_rh_tp:
                    _rh_src_u = str(getattr(position, "source", "") or "").upper()
                    if "UNLOCKED" in _rh_src_u or "SEEDED" in _rh_src_u:
                        _pos_unl_tp = True
                        position.is_unlocked_pool = True
                    _tp1_sol_thr, _tp1_sell, _tp2, _tp2_sell, _tp3, _tp3_sell, _tp4, _tp4_sell, _tp5, _tp5_sell = _rh_tp_mod.get_rug_hunter_tp_ladder(_pos_unl_tp)
                else:
                    _tp1_sol_thr, _tp1_sell, _tp2, _tp2_sell, _tp3, _tp3_sell, _tp4, _tp4_sell, _tp5, _tp5_sell = config.get_sol_tp_ladder(_pos_unl_tp)
                _min_tp_ticket = float(getattr(config, "PARTIAL_TP_MIN_TICKET_USD", 0.50))
                _pos_val = float(getattr(position, "original_amount_usd", 0.0) or getattr(position, "amount_usd", 0.0) or 0.0)
                _allow_partial_tp = getattr(config, "PARTIAL_TP_ENABLED", True) and (_pos_val >= _min_tp_ticket)
                _is_small_pos = _pos_val < _min_tp_ticket
                if _allow_partial_tp and not _is_small_pos and not _suppress_eval_peak:
                    if pnl_pct >= _tp1_sol_thr and not getattr(position, "tp1_done", False) and float(getattr(position, "amount_usd", 0.0) or 0.0) > 0.05:
                        tracker.partial_close_position(mint, current_price, _tp1_sell, f"Take Profit +{_tp1_sol_thr*100:.0f}% (Tier-1 {int(_tp1_sell*100)}%, Principal Shield / Moonbag)")
                        position.tp1_done = True
                        position.is_moonbag = True
                        position.last_tp_ts = time.time()
                    if pnl_pct >= _tp2 and not getattr(position, "tp2_done", False) and float(getattr(position, "amount_usd", 0.0) or 0.0) > 0.05:
                        if _tp2_sell >= 0.99:
                            tracker.close_position(mint, current_price, f"Moonbag TP2 +{_tp2*100:.0f}% (Target +{pnl_pct*100:.1f}%)")
                            continue
                        else:
                            tracker.partial_close_position(mint, current_price, _tp2_sell, f"Take Profit +{_tp2*100:.0f}% (Tier-2 {int(_tp2_sell*100)}%, Risk-Free Moonbag)")
                            position.tp2_done = True
                            position.is_moonbag = True
                            position.last_tp_ts = time.time()
                    if _tp3_sell > 0 and pnl_pct >= _tp3 and not getattr(position, "tp3_done", False) and float(getattr(position, "amount_usd", 0.0) or 0.0) > 0.05:
                        tracker.partial_close_position(mint, current_price, _tp3_sell, f"Take Profit +{_tp3*100:.0f}% (Tier-3 {int(_tp3_sell*100)}%)")
                        position.tp3_done = True
                        position.is_moonbag = True
                        position.last_tp_ts = time.time()
                    if _tp4_sell > 0 and pnl_pct >= _tp4 and not getattr(position, "tp4_done", False) and float(getattr(position, "amount_usd", 0.0) or 0.0) > 0.05:
                        tracker.partial_close_position(mint, current_price, _tp4_sell, f"Take Profit +{_tp4*100:.0f}% (Tier-4 {int(_tp4_sell*100)}%)")
                        position.tp4_done = True
                        position.last_tp_ts = time.time()
                    if _tp5_sell > 0 and pnl_pct >= _tp5 and not getattr(position, "tp5_done", False) and float(getattr(position, "amount_usd", 0.0) or 0.0) > 0.05:
                        tracker.partial_close_position(mint, current_price, _tp5_sell, f"Take Profit +{_tp5*100:.0f}% (Tier-5 {int(_tp5_sell*100)}%)")
                        position.tp5_done = True
                        position.last_tp_ts = time.time()

                prev_ts = getattr(position, "price_checked_at", 0.0)
                # 1-тиковый фильтр аномального провала котировки (>38% за 1 тик; для risk-free moonbag в плюсе >= +40% шире: >45%):
                # Не триггерим в первые CRASH_GUARD_MIN_HOLD_SEC (20с) и требуем 2 тика подтверждения для защищённых пулов!
                _anchor_px = float(getattr(position, "_crash_anchor_px", 0.0) or 0.0)
                _ref_crash_px = _anchor_px if _anchor_px > 0 else (_pre_tick_conf if _pre_tick_conf > 0 else prev_price)
                _is_unl_cg = _pos_unl_tp
                _cg_min_hold_min = float(getattr(config, "CRASH_GUARD_MIN_HOLD_SEC", 20.0) or 20.0) / 60.0
                _is_mb_cg = bool(getattr(position, "is_moonbag", False) or getattr(position, "tp1_done", False))
                _crash_mult = 0.70 if _is_unl_cg else (0.55 if (_is_mb_cg and pnl_pct >= 0.40) else 0.62)
                if minutes_held >= _cg_min_hold_min and prev_ts > 0 and (time.time() - prev_ts) < 60 and _ref_crash_px > 0 \
                        and current_price <= _ref_crash_px * _crash_mult and max_pnl_pct >= 0.15 and pnl_pct > -0.20:
                    if _anchor_px <= 0:
                        position._crash_anchor_px = _ref_crash_px
                    _cg_n = int(getattr(position, "crash_confirm_n", 0) or 0) + 1
                    position.crash_confirm_n = _cg_n
                    # Для защищённых Pump/Raydium пулов строго требуем 2 тика подтверждения (защита от разового сбоя котировки):
                    _cg_req = 1 if _is_unl_cg else 2
                    if _cg_n < _cg_req:
                        print(f"⚠️ [SOL-TICK-VERIFY] {position.symbol} ({mint[:8]}): резкий 1-тик {pnl_pct*100:+.1f}% (-{(1 - current_price/_ref_crash_px)*100:.0f}%) — ждём {_cg_req}-й тик подтверждения.")
                        continue
                    # ПЕРЕД ЗАКРЫТИЕМ ПО CRASH GUARD — ДВОЙНАЯ ПРОВЕРКА ЧЕРЕЗ JUPITER / DIRECT DEX:
                    # Защита от кейса NMS (фантомный прокол / сбой котировок при реальном пампе):
                    try:
                        from jupiter import JupiterAPI
                        _live_px_check = await JupiterAPI.get_price(mint)
                        if _live_px_check and _live_px_check > _ref_crash_px * _crash_mult:
                            print(f"🛡️ [CRASH-GUARD-RESCUED] {position.symbol} ({mint[:8]}): ложный краш-тик! Live DEX price ${_live_px_check:.6f} > prev ({(1 - _live_px_check/_ref_crash_px)*100:+.1f}%). Позиция спасена.")
                            current_price = _live_px_check
                            position.current_price_usd = _live_px_check
                            position._confirmed_px = _live_px_check
                            position.crash_confirm_n = 0
                            position._crash_anchor_px = 0.0
                            position.price_checked_at = time.time()
                            continue
                    except Exception:
                        pass
                    position._crash_anchor_px = 0.0
                    tracker.close_position(mint, current_price,
                                           f"Crash Guard (-{(1 - current_price/_ref_crash_px)*100:.0f}% за {(time.time()-prev_ts):.0f} сек)")
                    continue
                else:
                    position.crash_confirm_n = 0
                    position._crash_anchor_px = 0.0
                    if not _suppress_eval_peak:
                        position._confirmed_px = current_price
                position.price_checked_at = time.time()
                
                # Обновляем текущие значения для отображения в интерфейсе
                position.current_price_usd = current_price
                position.current_pnl_usd = position.amount_usd * pnl_pct
                position.price_checked_at = time.time()
                if current_price > 0 and (api_price > 0.0 or wss_fresh):
                    position.price_updated_at = time.time()

                # Даем 5 секунд передышки после взятия частичного тейка ТОЛЬКО пока позиция уверенно в плюсе (>= +4.5%):
                if _allow_partial_tp and not _is_small_pos and (time.time() - float(getattr(position, "last_tp_ts", 0.0) or 0.0)) < 5.0 and pnl_pct >= 0.045:
                    continue
                from exit_managers import MatureExitManager
                _saved_max_px = position.max_price_usd if _suppress_eval_peak else None
                mature_exit_reason = MatureExitManager.evaluate_exit(position, current_price)
                if _saved_max_px is not None:
                    position.max_price_usd = _saved_max_px
                if mature_exit_reason == "PARTIAL_TP1_TRIGGER":
                    if _allow_partial_tp:
                        tracker.partial_close_position(mint, current_price, _tp1_sell, f"Take Profit Flush +{pnl_pct*100:.1f}% (after peak +{max_pnl_pct*100:.1f}%)")
                        position.tp1_done = True
                        position.is_moonbag = True
                        position.last_tp_ts = time.time()
                    continue
                if mature_exit_reason:
                    _px_to_close = current_price
                    if "Absolute Liquidity Loss" in mature_exit_reason:
                        # При абсолютной потере ликвидности (пул $0) — цена продажи 0.0 (списание):
                        _px_to_close = 0.0
                    elif minutes_held < 2.5 and pnl_pct <= -0.10 and any(k in mature_exit_reason for k in ("Emergency Cap", "Stop Loss", "Quick Scratch", "Early Momentum")):
                        try:
                            _live_em_px = None
                            _pos_mf = getattr(position, "ml_features", None) or {}
                            _pos_dex = str(_pos_mf.get("dex") or getattr(position, "dex_id", "") or "").lower()
                            _pos_pair = str(getattr(position, "pair_address", "") or "")
                            import market_data as _md_em
                            if _pos_dex in ("pump", "pumpfun") or (mint.lower().endswith("pump") and not _pos_pair):
                                _st_em = await _md_em.get_live_pump_curve_state(mint)
                                if _st_em and not _st_em.get("complete") and float(_st_em.get("price_usd", 0) or 0) > 0:
                                    _live_em_px = float(_st_em["price_usd"])
                            elif _pos_dex == "pumpswap":
                                _ps_em = await _md_em.get_live_pumpswap_pool_state(mint, _pos_pair)
                                if _ps_em:
                                    if float(_ps_em.get("liq_usd", 0) or 0) < 800.0:
                                        _px_to_close = float(_ps_em.get("price_usd", 0) or 0.0)
                                        _live_em_px = _px_to_close
                                    elif float(_ps_em.get("price_usd", 0) or 0) > 0:
                                        _live_em_px = float(_ps_em["price_usd"])
                            if not _live_em_px and not (_px_to_close == 0.0):
                                from jupiter import JupiterAPI
                                _live_em_px = await JupiterAPI.get_price(mint)
                            if _live_em_px and _live_em_px > 0 and position.entry_price_usd > 0:
                                _live_em_pnl = (_live_em_px - position.entry_price_usd) / position.entry_price_usd
                                # Порог спасения от ложного тика DexScreener синхронизирован с реальным SL (фикс GME: тик -40.8%, живой ончейн -11.5% при SL -20%):
                                _is_unl_em = bool(getattr(position, "is_unlocked_pool", False))
                                _sl_em = -abs(float(getattr(config, "STOP_LOSS_UNLOCKED_PCT", -0.15))) if _is_unl_em else -abs(float(getattr(config, "STOP_LOSS_PCT", -0.20)))
                                _rescue_thr = -0.10 if any(k in mature_exit_reason for k in ("Quick Scratch", "Early Momentum")) else (_sl_em * 0.85)
                                if _live_em_pnl > _rescue_thr:
                                    print(f"🛡️ [EARLY-EXIT-RESCUED] {position.symbol} ({mint[:8]}): ложный тик {pnl_pct*100:+.1f}%, живой ончейн/Jupiter ${_live_em_px:.8f} ({_live_em_pnl*100:+.1f}% > {_rescue_thr*100:+.1f}%) — позиция спасена!")
                                    position.current_price_usd = _live_em_px
                                    position.emerg_confirm_n = 0
                                    position.sl_confirm_n = 0
                                    continue
                                current_price = _live_em_px
                                _px_to_close = _live_em_px
                        except Exception:
                            pass
                    # ЧЕСТНАЯ PAPER-СИМУЛЯЦИЯ: если дев выдернул LP (пул пустой), продать по котировке НЕВОЗМОЖНО.
                    # DexScreener/Jupiter продолжают показывать последнюю цену, но в пуле 0 SOL -> реальный выход = $0 (кейс mihao-клон).
                    if _px_to_close > 0:
                        try:
                            import market_data as _md_rug
                            _pd_rug = await asyncio.wait_for(_md_rug.get_token_data(mint, max_cache_age=2.0, priority=True), timeout=1.2)
                            _liq_rug = float((((_pd_rug or {}).get("liquidity") or {}).get("usd", 0) or 0)) if (_pd_rug and (_pd_rug.get("liquidity") or {}).get("usd") is not None) else -1.0
                            _dex_rug = str((_pd_rug or {}).get("dexId") or "").lower()
                            if _dex_rug in ("pump", "pumpfun", "moonshot", "meteoradbc"):
                                _liq_rug = -1.0  # bonding curve: у DexScreener ликвидность всегда 0, это не раг
                            _peak_liq_rug = float(getattr(position, "peak_liquidity_usd", 0.0) or getattr(position, "initial_liquidity_usd", 0.0) or 0.0)
                            if _liq_rug >= 0 and (_liq_rug < 300.0 or (_peak_liq_rug >= 3000.0 and _liq_rug < _peak_liq_rug * 0.10)):
                                print(f"🚨 [LP-PULLED] {position.symbol} ({mint[:8]}): пул осушен (liq ${_liq_rug:.0f}, пик ${_peak_liq_rug:,.0f}) — реальная продажа невозможна, списание в $0.")
                                _px_to_close = 0.0
                                mature_exit_reason = f"LP Pulled Rug (liq ${_liq_rug:.0f} from ${_peak_liq_rug:,.0f}) | {mature_exit_reason}"
                        except Exception:
                            pass
                    tracker.close_position(mint, _px_to_close, mature_exit_reason)
                    continue

                # Позиция находится под управлением MatureExitManager:
                # Если менеджер выходов вернул None — держим позицию и даём ракете свободно расти!
                # Не пускаем позицию в устаревшие скальперские фильтры (Profit Floor +5%), которые срезали прибыль на копейки.
                continue
                    
                # === STALL TIMER (DEAD MOMENTUM EXIT - 210с на консолидацию без затягивания убытка) ===
                _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 210.0))
                _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.075))
                if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.04 and pnl_pct <= _stall_loss:
                    tracker.close_position(mint, current_price, f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)")
                    continue

                # === ТАЙМАУТ ПОСЛЕ ИМПУЛЬСА (только для не-mature скальпов +32..+50%) ===
                if minutes_held >= 10.0 and 0.32 <= max_pnl_pct < 0.50 and pnl_pct <= -0.08:
                    tracker.close_position(mint, current_price, f"Early Fade Cut (был пик +{max_pnl_pct*100:.0f}%, слив до {pnl_pct*100:.1f}%)")
                    continue
                if minutes_held >= 12.0 and 0.32 <= max_pnl_pct < 0.50 and minutes_since_peak >= 8 and pnl_pct <= 0.025:
                    tracker.close_position(mint, current_price, f"Post-Rocket Fade Cut ({minutes_since_peak:.0f}m after peak, pnl {pnl_pct*100:+.1f}%)")
                    continue

                # === STAGNANT EXIT (PRO: время по заслугам) ===
                _hold_min = float(getattr(config, "STAGNANT_HOLD_MIN", 25.0) or 25.0)
                _loss_min = (float(getattr(config, "STAGNANT_LOSS_MIN", 15.0) or 15.0)
                             if max_pnl_pct >= 0.05 else
                             float(getattr(config, "STAGNANT_LOSS_MIN_FAST", 12.0) or 12.0))
                if minutes_held >= _loss_min and pnl_pct <= -0.075 and max_pnl_pct < 0.05:
                    _graces = getattr(position, "stagnant_graces", 0)
                    try:
                        _td = await analyzer.fetch_token_data(mint)
                        if not _td:
                            continue
                        _tx5 = ((_td.get("txns") or {}).get("m5", {}) or {})
                        _alive = ((_tx5.get("buys", 0) or 0) + (_tx5.get("sells", 0) or 0)) >= 3
                    except Exception:
                        continue
                    if _alive and _graces < 1 and pnl_pct > -0.04:
                        position.stagnant_graces = _graces + 1
                        print(f"⏳ {mint[:8]}: живой флет ({pnl_pct*100:.1f}%), грейс {position.stagnant_graces}/1 — ждём выстрел.")
                        continue
                    from jupiter import JupiterAPI
                    _j_ok, _j_why = await JupiterAPI.is_token_sellable(mint)
                    if not _j_ok:
                        print(f"🚨 [SOL-STAGNANT-RUG] {position.symbol} ({mint[:8]}): флет-убыток и непродаваем ({_j_why}) — списание в 0.0$ (-100%).")
                        tracker.close_position(mint, 0.0, f"Stagnant RUG / Unsellable ({_j_why})")
                    else:
                        tracker.close_position(mint, current_price, f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)")
                    continue
                if minutes_held >= _hold_min and abs(pnl_pct) < 0.05 and max_pnl_pct < 0.05:
                    from jupiter import JupiterAPI
                    _j_ok, _j_why = await JupiterAPI.is_token_sellable(mint)
                    if not _j_ok:
                        print(f"🚨 [SOL-STAGNANT-RUG] {position.symbol} ({mint[:8]}): флет и непродаваем ({_j_why}) — списание в 0.0$ (-100%).")
                        tracker.close_position(mint, 0.0, f"Stagnant RUG / Unsellable ({_j_why})")
                    else:
                        tracker.close_position(mint, current_price, f"Stagnant Near Zero ({_hold_min:.0f} min flat)")
                    continue
                
                # === ЖЕСТКИЙ RISK MANAGEMENT (CRO LEVEL) ===
                
                # Умный расчет минимального порога для покрытия комиссий
                priority_fee_usd = 0.075 if position.amount_usd < 10.0 else 0.45
                min_fee_pct = (priority_fee_usd + 0.02 * position.amount_usd) / position.amount_usd
                
                # 1. СКАЛЬП-ТРЕЙЛИНГ - ВЫКЛЮЧЕН (не режем ракеты на первом 5% откате при пике +10%)
                if getattr(config, "SCALP_PROFIT_ENABLED", False) and max_pnl_pct >= 0.10 and max_pnl_pct < getattr(config, "TRAILING_ACTIVATION_PCT", 0.45):
                    drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                    if drop_from_max >= 0.05:
                        tracker.close_position(mint, current_price, f"Scalp Profit (peak +{max_pnl_pct*100:.0f}%)")
                        continue
                        
                # === TAKE-PROFIT LADDER (только для позиций > $20) ===
                if _allow_partial_tp and not _is_small_pos:
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and pnl_pct >= _tp1_sol_thr and not getattr(position, "tp1_done", False):
                        tracker.partial_close_position(mint, current_price, _tp1_sell, f"Take Profit +{_tp1_sol_thr*100:.0f}% (Tier-1 {int(_tp1_sell*100)}%)")
                        position.tp1_done = True
                        position.is_moonbag = True
                        continue
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and pnl_pct >= _tp2 and not getattr(position, "tp2_done", False):
                        tracker.partial_close_position(mint, current_price, _tp2_sell, f"Take Profit +{_tp2*100:.0f}% (Tier-2 {int(_tp2_sell*100)}%)")
                        position.tp2_done = True
                        continue
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and pnl_pct >= _tp3 and not getattr(position, "tp3_done", False):
                        tracker.partial_close_position(mint, current_price, _tp3_sell, f"Take Profit +{_tp3*100:.0f}% (Tier-3 {int(_tp3_sell*100)}%)")
                        position.tp3_done = True
                        continue
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and pnl_pct >= _tp4 and not getattr(position, "tp4_done", False):
                        tracker.partial_close_position(mint, current_price, _tp4_sell, f"Take Profit +{_tp4*100:.0f}% (Tier-4 {int(_tp4_sell*100)}%)")
                        position.tp4_done = True
                        continue
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and pnl_pct >= _tp5 and not getattr(position, "tp5_done", False):
                        tracker.partial_close_position(mint, current_price, _tp5_sell, f"Take Profit +{_tp5*100:.0f}% (Tier-5 {int(_tp5_sell*100)}%)")
                        position.tp5_done = True
                        continue

                # === ВЫХОД ДЛЯ МИКРО-ПЫЛИ (< $0.50) ===
                if _is_small_pos and pnl_pct >= _tp1_sol_thr and not getattr(position, "tp1_done", False):
                    tracker.close_position(mint, current_price, f"Dust Pos Exit +{pnl_pct*100:.1f}% (TP1 {_tp1_sol_thr*100:.0f}%)")
                    position.tp1_done = True
                    continue

                # 1.5 ГАРАНТИРОВАННЫЙ НЕСНИЖАЕМЫЙ ПОРОГ ПРИБЫЛИ (PROFIT FLOOR LOCK)
                # До +50% монета свободно дышит, никаких микро-полов по 5%!
                _floor_pnl = None
                if max_pnl_pct >= 5.00:       # Гипер-ракета (+500%+): держим 70% прибыли
                    _floor_pnl = round(max_pnl_pct * 0.70, 2)
                elif max_pnl_pct >= 2.50:     # Мега-ракета (+250%..+500%): держим 160% прибыли
                    _floor_pnl = 1.60
                elif max_pnl_pct >= 1.00:     # Пик +100%+ -> 60%
                    _floor_pnl = 0.60
                elif max_pnl_pct >= 0.50:     # Пик +50%+ -> 25%
                    _floor_pnl = 0.25
                if _floor_pnl is not None and pnl_pct <= _floor_pnl:
                    _fc_s = int(getattr(position, "floor_confirm_n", 0) or 0) + 1
                    position.floor_confirm_n = _fc_s
                    _fast_dump = pnl_pct <= 0.01 or (position.max_price_usd > 0 and (position.max_price_usd - current_price) / position.max_price_usd >= 0.25)
                    if _fc_s < 2 and not _fast_dump:
                        continue
                    if pnl_pct >= 0:
                        tracker.close_position(mint, current_price, f"Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%)")
                    else:
                        tracker.close_position(mint, current_price, f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.0f}%)")
                    continue
                else:
                    position.floor_confirm_n = 0

                # 2. СУЖАЮЩИЙСЯ ПАРАБОЛИЧЕСКИЙ ТРЕЙЛИНГ-СТОП (Включается от +75% пика с 2-тиковым подтверждением!)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if max_pnl_pct >= 4.00:
                    trail_distance = 0.16
                elif max_pnl_pct >= 2.00:
                    trail_distance = 0.20
                else:
                    trail_distance = 0.25
                if max_pnl_pct >= 0.75:
                    if drop_from_max >= trail_distance:
                        _tc_s = int(getattr(position, "trail_confirm_n", 0) or 0) + 1
                        position.trail_confirm_n = _tc_s
                        if _tc_s >= 2:
                            tracker.close_position(mint, current_price, f"Trailing Stop (peak +{max_pnl_pct*100:.0f}%, drop {drop_from_max*100:.0f}%)")
                            continue
                    else:
                        position.trail_confirm_n = 0

                _pt_sol = getattr(position, "peak_time", 0.0) or getattr(position, "entry_time", 0.0) or time.time()
                _peak_age_sol = (time.time() - _pt_sol) / 60
                if max_pnl_pct >= 0.35 and _peak_age_sol >= 25 and pnl_pct > 0.05 and drop_from_max >= 0.08:
                    tracker.close_position(mint, current_price, f"Runner Stagnation (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_sol:.0f}м без ATH)")
                    continue
                elif minutes_held >= 30 and _peak_age_sol >= 25:
                    if pnl_pct > 0.10:
                        tracker.close_position(mint, current_price, f"Inactive Pool TP (+{pnl_pct*100:.1f}%, {minutes_held:.0f}м)")
                        continue
                    elif pnl_pct < -0.05:
                        tracker.close_position(mint, current_price, f"Inactive Pool Cut ({pnl_pct*100:.1f}%, {minutes_held:.0f}м)")
                        continue

                # Breakeven / Soft Guard резерв (только после 2+ минут и пробоя +32%, не душим стартовые волны +10..+25%):
                if minutes_held >= 8.0 and max_pnl_pct >= 0.25 and pnl_pct <= -0.10:
                    tracker.close_position(mint, current_price, f"Soft Guard (был пик +{max_pnl_pct*100:.0f}%, слив до {pnl_pct*100:.1f}%)")
                    continue
                _be_sol = float(getattr(config, "BREAKEVEN_PCT", 999.0))
                if _be_sol < 10.0 and (minutes_held >= 1.0 or max_pnl_pct >= 0.30) and max_pnl_pct >= _be_sol and pnl_pct <= 0.025:
                    tracker.close_position(mint, current_price, f"Breakeven (был пик +{max_pnl_pct*100:.0f}%)")
                    continue

                # 3.2 GROK BOT x JEV: СТОП ПОД ФИТИЛЬ (по умолчанию ОТКЛЮЧЕН для сохранения ракет)
                if getattr(config, "JEV_WICK_STOP_ENABLED", False):
                    _wick_stop = float(getattr(position, "stop_under_wick", 0.0) or 0.0)
                    _wick_pct = float(getattr(position, "wick_stop_loss_pct", 0.0) or 0.0)
                    if minutes_held >= 2.0:
                        _eff_wick_pct = min(-0.16, _wick_pct) if _wick_pct < 0 else -0.18
                        _eff_wick_stop = min(_wick_stop, position.entry_price_usd * 0.84) if _wick_stop > 0 else 0.0
                        if (_eff_wick_stop > 0 and current_price <= _eff_wick_stop) or pnl_pct <= _eff_wick_pct:
                            _wc_s = int(getattr(position, "wick_confirm_n", 0) or 0) + 1
                            position.wick_confirm_n = _wc_s
                            if _wc_s >= 2:
                                if _eff_wick_stop > 0 and current_price <= _eff_wick_stop:
                                    tracker.close_position(mint, current_price, f"JEV Wick Stop (${current_price:.6f} <= ${_eff_wick_stop:.6f}, {pnl_pct*100:.1f}%)")
                                else:
                                    tracker.close_position(mint, current_price, f"JEV Wick Stop ({pnl_pct*100:.1f}% <= {_eff_wick_pct*100:.1f}%)")
                                continue
                        else:
                            position.wick_confirm_n = 0
                                
                # 3. АВАРИЙНЫЙ КЭП из config.py (-18% вместо -30%): не даём одной сделке съедать 3 профита!
                _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.18)))
                if pnl_pct <= _emerg_cap:
                    tracker.close_position(mint, current_price, f"Emergency Cap ({pnl_pct*100:.0f}%)")
                    continue

                # 3.5 ЖЕСТКИЙ Stop Loss из config.py (-14% после 1.2 мин)
                if minutes_held >= 1.2 and pnl_pct <= config.STOP_LOSS_PCT:
                    tracker.close_position(mint, current_price, f"Hard Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)")
                    continue
                    
                # 4. УМНЫЙ ВЫХОД ПО ВРЕМЕНИ (Stagnant / Dead Coin Cut для Solana)
                # Даём ракетам 25 мин на консолидацию и раскачку (не режем живые пулы на 8-й минуте)
                _dead_hold = float(getattr(config, "STAGNANT_HOLD_MIN", 25.0) or 25.0)
                if minutes_held >= _dead_hold and pnl_pct < -0.05:
                    tracker.close_position(mint, current_price, f"Dead Coin Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)")
                    continue
                    
                # Если монета болтается во флете больше 25 минут - выходим, освобождаем капитал
                if minutes_held >= _dead_hold and pnl_pct < 0.05 and max_pnl_pct < 0.15:
                    tracker.close_position(mint, current_price, f"Stagnant Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)")
                    continue
                    
                # Старый Time Exit (резервный)
                if minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
                    tracker.close_position(mint, current_price, "Time-based Exit")
                    continue
            # Сохраняем обновлённые цены одним разом за цикл (файл + Supabase)
            tracker.save_portfolio()
        except Exception as e:
            print(f"Ошибка в менеджере позиций: {e}")
        # Балк-цены DexScreener/GT: сверхбыстрый запрос за ~300мс на все позиции за цикл.
        # 0.5 сек: субсекундная скорость трекинга открытых позиций для мемкоинов.
        await asyncio.sleep(0.5)
from birth_tracker import BirthTracker, birth_tracker
from scan_pipeline import ScanWindow, MISSING as _SCAN_MISSING, apply_result as _scan_apply

async def birth_wss_loop(analyzer, tracker):
    import websockets
    import json
    print("👶 Запуск Роддома: сбор базы данных новых токенов для сканера (без авто-покупки на 0-секунде)...")

    async def _fallback_poll_births():
        """Фоновый сбор свежих токенов и ПРЯМОЙ ОНЧЕЙН-СБОР МИГРАЦИЙ Pump.fun -> PumpSwap.
        Работает напрямую через Solana RPC (39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg),
        поэтому ловит 100% выпускников за 3-10с даже когда PumpPortal WSS молчит, а GT в 429!"""
        import aiohttp as _aio_mig
        from http_client import fetch_json, get_session as _mig_get_session
        _MIG_AUTH = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
        _seen_mig_sigs = set()
        _last_gt_poll = 0.0
        _last_rpc_mig_poll = 0.0
        _mig_first_run = True

        async def _rpc_call_mig(method: str, params: list, timeout: float = 5.0):
            _urls = ["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"]
            _h_url = getattr(config, "HELIUS_RPC_URL", "") or ""
            if _h_url and "api.mainnet-beta" not in _h_url:
                _urls.append(_h_url)
            sess = await _mig_get_session()
            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            for _u in _urls:
                try:
                    async with sess.post(_u, json=payload, timeout=_aio_mig.ClientTimeout(total=timeout)) as resp:
                        if resp.status == 200:
                            js = await resp.json(content_type=None)
                            if isinstance(js, dict) and js.get("result") is not None:
                                return js["result"]
                except Exception:
                    continue
            return None

        while True:
            try:
                _now_f = time.time()

                # 0. ПРЯМОЙ ОНЧЕЙН-МОНИТОР МИГРАЦИЙ PUMP.FUN -> PUMPSWAP (каждые 10с, 0 зависимости от PumpPortal WSS!)
                if _now_f - _last_rpc_mig_poll >= 10.0:
                    _last_rpc_mig_poll = _now_f
                    try:
                        _lim = 25 if _mig_first_run else 12
                        _sigs = await _rpc_call_mig("getSignaturesForAddress", [_MIG_AUTH, {"limit": _lim}], timeout=5.0)
                        if isinstance(_sigs, list) and _sigs:
                            _new_sigs = []
                            for _s in _sigs:
                                if not isinstance(_s, dict) or _s.get("err") is not None:
                                    continue
                                _sg = str(_s.get("signature") or "")
                                _bt = int(_s.get("blockTime") or 0)
                                if not _sg or _sg in _seen_mig_sigs:
                                    continue
                                _seen_mig_sigs.add(_sg)
                                # На старте догружаем все миграции за последние 25 мин, далее — только свежие (< 10 мин)
                                _max_bt_age = 1500.0 if _mig_first_run else 600.0
                                if _bt > 0 and (_now_f - _bt) > _max_bt_age:
                                    continue
                                _new_sigs.append((_sg, _bt))
                            _mig_first_run = False
                            if len(_seen_mig_sigs) > 600:
                                _seen_mig_sigs.clear()
                                for _s in _sigs:
                                    if isinstance(_s, dict) and _s.get("signature"):
                                        _seen_mig_sigs.add(str(_s["signature"]))

                            async def _extract_mig_mints(sig_str: str, bt_ts: int):
                                tx = await _rpc_call_mig(
                                    "getTransaction",
                                    [sig_str, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                                    timeout=4.5,
                                )
                                if not isinstance(tx, dict):
                                    return []
                                meta = tx.get("meta") or {}
                                found = []
                                seen_m = set()
                                for bal in (meta.get("postTokenBalances") or []):
                                    m_addr = str(bal.get("mint") or "").strip()
                                    if m_addr.endswith("pump") and 32 <= len(m_addr) <= 44 and m_addr not in seen_m:
                                        seen_m.add(m_addr)
                                        found.append((m_addr, bt_ts))
                                return found

                            if _new_sigs:
                                _tx_res = await asyncio.gather(
                                    *[_extract_mig_mints(sg, bt) for sg, bt in _new_sigs[:10]],
                                    return_exceptions=True,
                                )
                                _added_cnt = 0
                                _added_mints = set()
                                for _r_item in _tx_res:
                                    if not isinstance(_r_item, list):
                                        continue
                                    for _mm, _bt_m in _r_item:
                                        if _mm in _added_mints:
                                            continue
                                        _added_mints.add(_mm)
                                        birth_tracker.add_token(_mm)
                                        birth_tracker.add_graduated(_mm)
                                        _added_cnt += 1
                                        _age_sec = max(0.0, _now_f - _bt_m) if _bt_m > 0 else 0.0
                                        print(f"🏁 [ONCHAIN-MIGRATION] {_mm[:8]}..pump (мигрировал {_age_sec:.0f}с назад) → в приоритет сканера!", flush=True)
                                if _added_cnt:
                                    print(f"🛰️ [ONCHAIN-MIG-POLLER] Поймано {_added_cnt} свежих миграций Pump.fun → PumpSwap напрямую из блокчейна Solana!", flush=True)
                    except Exception:
                        pass

                # 1. DexScreener профили и бусты
                st, d = await fetch_json("https://api.dexscreener.com/token-profiles/latest/v1", timeout=6, retries=1)
                if st == 200 and isinstance(d, list):
                    for item in d:
                        if item.get("chainId") == "solana":
                            m = item.get("tokenAddress")
                            if m and (m.endswith("pump") or len(m) > 32):
                                birth_tracker.add_token(m)
                st_b, d_b = await fetch_json("https://api.dexscreener.com/token-boosts/latest/v1", timeout=6, retries=1)
                if st_b == 200 and isinstance(d_b, list):
                    for item in d_b:
                        if item.get("chainId") == "solana":
                            m = item.get("tokenAddress")
                            if m and (m.endswith("pump") or len(m) > 32):
                                birth_tracker.add_token(m)

                # 2. GeckoTerminal New Pools (каждые 10с): дополнительный захват свежих миграций Raydium/PumpSwap
                if _now_f - _last_gt_poll >= 10.0:
                    _last_gt_poll = _now_f
                    st_gt, d_gt = await fetch_json("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", timeout=6, retries=0)
                    if st_gt == 200 and isinstance(d_gt, dict):
                        for p in (d_gt.get("data") or []):
                            attrs = p.get("attributes") or {}
                            rel = p.get("relationships") or {}
                            base_id = (rel.get("base_token", {}).get("data") or {}).get("id", "")
                            dex_id = str((rel.get("dex", {}).get("data") or {}).get("id", "")).lower()
                            mint = base_id.replace("solana_", "").strip()
                            pool_addr = attrs.get("address", "")
                            if mint and 32 <= len(mint) <= 44 and not mint.startswith("0x"):
                                birth_tracker.add_token(mint)
                                # Если это миграция на PumpSwap / Raydium — немедленно в приоритетную очередь graduated!
                                if "pumpswap" in dex_id or "raydium" in dex_id:
                                    birth_tracker.add_graduated(mint, pool_addr)
            except Exception:
                pass
            await asyncio.sleep(5)

    asyncio.create_task(_fallback_poll_births())

    uri = config.PUMPPORTAL_WSS
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Origin": "https://pumpportal.fun"
    }
    while True:
        try:
            import inspect
            _ws_kw = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
            _ws_opts = {_ws_kw: headers}
            async with websockets.connect(uri, **_ws_opts) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                async for message in ws:
                    data = json.loads(message)
                    mint = data.get("mint")
                    if mint:
                        birth_tracker.add_token(mint)
                        if str(data.get("txType") or "").lower() == "migrate" or data.get("pool"):
                            birth_tracker.add_graduated(mint, str(data.get("pool") or ""))
        except Exception as e:
            # Не спамим при 502, фоновый поллер подхватывает новые токены
            await asyncio.sleep(15)

async def onchain_migration_feeder_loop(analyzer=None):
    """Выделенный супервизируемый ончейн-монитор миграций Pump.fun -> PumpSwap (Solana RPC).
    Работает независимо от мёртвого PumpPortal WSS (и без второго сокета!) и от 429 GeckoTerminal:
    каждые 8с опрашивает getSignaturesForAddress(39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg)
    и мгновенно кладёт все свежие .pump выпускники в birth_tracker.add_graduated() (приоритет #0)."""
    import aiohttp as _aio_mig
    from http_client import get_session as _mig_get_session

    _MIG_AUTH = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
    _SKIP_MIG_MINTS = {
        "So11111111111111111111111111111111111111112",   # WSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
        "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",  # USD1
        "he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A",  # hSOL
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # mSOL
        "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # JitoSOL
        "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",  # bSOL
    }
    _seen_mig_sigs = set()
    _mig_first_run = True
    _rpc_sem = asyncio.Semaphore(4)
    print("🛰️ [ONCHAIN-MIG-POLLER] Запущен прямой ончейн-монитор миграций Pump.fun -> PumpSwap (Solana RPC, 8с)!", flush=True)

    async def _rpc_call_mig(method: str, params: list, timeout: float = 5.0):
        _urls = ["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"]
        _h_url = getattr(config, "HELIUS_RPC_URL", "") or ""
        if _h_url and "api.mainnet-beta" not in _h_url:
            _urls.append(_h_url)
        sess = await _mig_get_session()
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for _u in _urls:
            try:
                async with sess.post(_u, json=payload, timeout=_aio_mig.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        js = await resp.json(content_type=None)
                        if isinstance(js, dict) and js.get("result") is not None:
                            return js["result"]
            except Exception:
                continue
        return None

    async def _extract_mig_mints(sig_str: str, bt_ts: int):
        async with _rpc_sem:
            tx = await _rpc_call_mig(
                "getTransaction",
                [sig_str, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                timeout=4.5,
            )
        if not isinstance(tx, dict):
            return []
        meta = tx.get("meta") or {}
        logs = meta.get("logMessages") or []
        if logs and not any(("Instruction: Migrate" in l or "Instruction: CreatePool" in l) for l in logs):
            return []
        pre_bals = meta.get("preTokenBalances") or []
        post_bals = meta.get("postTokenBalances") or []
        pre_mints = {str(b.get("mint") or "").strip() for b in pre_bals if b.get("mint")}
        found = []
        seen_m = set()
        for bal in post_bals + pre_bals:
            m_addr = str(bal.get("mint") or "").strip()
            if not m_addr or m_addr in _SKIP_MIG_MINTS or m_addr in seen_m or not (32 <= len(m_addr) <= 44):
                continue
            ui_amt = float(((bal.get("uiTokenAmount") or {}).get("uiAmount") or 0.0))
            decs = int(((bal.get("uiTokenAmount") or {}).get("decimals") or 0))
            # Ловим И .pump-минты, И не-vanity выпускники Pump.fun (кейс Raja 7ycjv7Rn..hcMr: 6 знаков, ~200M токенов в пуле, уже был в preTokenBalances)
            is_pump_base = m_addr.endswith("pump") or (m_addr in pre_mints and decs == 6 and ui_amt >= 1_000_000.0)
            if is_pump_base:
                seen_m.add(m_addr)
                found.append((m_addr, bt_ts))
        return found

    while True:
        try:
            _now_f = time.time()
            _lim = 25 if _mig_first_run else 12
            _sigs = await _rpc_call_mig("getSignaturesForAddress", [_MIG_AUTH, {"limit": _lim}], timeout=5.0)
            if isinstance(_sigs, list) and _sigs:
                _new_sigs = []
                _max_bt_age = 1500.0 if _mig_first_run else 600.0
                for _s in _sigs:
                    if not isinstance(_s, dict) or _s.get("err") is not None:
                        continue
                    _sg = str(_s.get("signature") or "")
                    _bt = int(_s.get("blockTime") or 0)
                    if not _sg or _sg in _seen_mig_sigs:
                        continue
                    _seen_mig_sigs.add(_sg)
                    if _bt > 0 and (_now_f - _bt) > _max_bt_age:
                        continue
                    _new_sigs.append((_sg, _bt))
                _mig_first_run = False
                if len(_seen_mig_sigs) > 600:
                    _seen_mig_sigs.clear()
                    for _s in _sigs:
                        if isinstance(_s, dict) and _s.get("signature"):
                            _seen_mig_sigs.add(str(_s["signature"]))

                if _new_sigs:
                    _batch_lim = 18 if len(_new_sigs) > 10 else len(_new_sigs)
                    _tx_res = await asyncio.gather(
                        *[_extract_mig_mints(sg, bt) for sg, bt in _new_sigs[:_batch_lim]],
                        return_exceptions=True,
                    )
                    _added_mints = []
                    _added_set = set()
                    for _r_item in _tx_res:
                        if not isinstance(_r_item, list):
                            continue
                        for _mm, _bt_m in _r_item:
                            if _mm in _added_set:
                                continue
                            _added_set.add(_mm)
                            _added_mints.append(_mm)
                            birth_tracker.add_token(_mm)
                            birth_tracker.add_graduated(_mm)
                            _age_sec = max(0.0, _now_f - _bt_m) if _bt_m > 0 else 0.0
                            print(f"🏁 [ONCHAIN-MIGRATION] {_mm[:8]}..{_mm[-4:]} (мигрировал {_age_sec:.0f}с назад) → в приоритет сканера!", flush=True)
                    if _added_mints:
                        if analyzer is not None:
                            _cur_d = list(getattr(analyzer, "_cached_discovery_mints", None) or [])
                            _merged = list(dict.fromkeys(_added_mints + _cur_d))
                            analyzer._cached_discovery_mints = _merged[:250]
                            analyzer._cached_discovery_time = _now_f
                        print(f"🛰️ [ONCHAIN-MIG-POLLER] Поймано {len(_added_mints)} свежих миграций Pump.fun → PumpSwap напрямую из блокчейна Solana!", flush=True)
        except Exception as _me:
            print(f"⚠️ [ONCHAIN-MIG-POLLER] Ошибка опроса RPC: {_me}", flush=True)
        await asyncio.sleep(8)


async def discovery_feeder_loop(analyzer):
    """Фоновый агрегатор источников (Jupiter 5m/1h/6h, GeckoTerminal, DexScreener search/boosts).
    Обновляет кэш кандидатов analyzer._cached_discovery_mints каждые 15 сек.
    Благодаря этому scanner_loop разбирает свежие миграции мгновенно (0с задержки вместо 17-33с)!
    """
    print("🛰️ [DISCOVERY-FEEDER] Фоновый агрегатор запущен (Jupiter, GT, DexScreener)...")
    while True:
        try:
            mints = []
            # 1. Jupiter Discovery (toptrending 5m/1h/6h, toporganicscore, toptraded, recent)
            try:
                from jup_discovery import fetch_jup_tokens
                j_mints = await fetch_jup_tokens()
                if j_mints:
                    mints.extend(j_mints)
            except Exception:
                pass

            # 2. GeckoTerminal Trending (5m + 1h + new_pools)
            try:
                import market_data
                gt_pools = await market_data.get_trending()
                if gt_pools:
                    mints.extend([p.get("tokenAddress") for p in gt_pools if p.get("tokenAddress")])
            except Exception:
                pass

            # 3. DexScreener бусты, профили и радар поиска (pumpswap, raydium, orca)
            try:
                ds_tokens = await analyzer.fetch_latest_tokens()
                if ds_tokens:
                    mints.extend([p.get("tokenAddress") for p in ds_tokens if p.get("tokenAddress")])
            except Exception:
                pass

            # 0. Сохраняем свежие ончейн-миграции Pump.fun первыми (не перезатираем их при 429 от Jupiter/GT на Render!)
            try:
                _onchain_grads = list(getattr(birth_tracker, "graduated", {}).keys()) + list(
                    birth_tracker.get_waiting_room_graduates(min_age_sec=0.0, max_age_sec=1800.0) or []
                )
                if _onchain_grads:
                    mints = _onchain_grads + mints
            except Exception:
                pass

            if mints:
                deduped = list(dict.fromkeys(
                    m.strip() for m in mints
                    if m and isinstance(m, str) and 32 <= len(m.strip()) <= 44 and not m.strip().startswith("0x")
                ))
                analyzer._cached_discovery_mints = deduped
                analyzer._cached_discovery_time = time.time()
        except Exception as e:
            print(f"⚠️ [DISCOVERY-FEEDER] Ошибка сбора: {e}")
        await asyncio.sleep(5)


async def scanner_loop(analyzer, tracker):
    print("🚀 Запуск PhantBot Scanner (Поиск новых монет)...")
    processed_mints = {}
    _grad_none = {}  # mint -> число None от DS (скам/нестандарт: после 3 — хороним, не жрём циклы)
    _latest_cache = (0.0, [])   # (ts, tokens): кэш fetch_latest_tokens
    _last_idle_log = 0.0
    _last_scan_log = 0.0
    _last_wr_log = 0.0
    while True:
        _scan_win = None
        _entered = False
        _cycle_t0 = time.time()
        _n_analyzed = 0
        _launch_n = 0
        try:
            open_count = len(tracker.get_open_positions())
            # Дневной kill-switch: ВЫКЛЮЧЕН (KILL_SWITCH_ENABLED=False) - блок ниже не срабатывает
            import time as _t
            day_start = _t.time() - (_t.time() % 86400)
            day_pnl = sum(getattr(p, "pnl_usd", 0) or 0 for p in tracker.positions.values()
                          if getattr(p, "status", "") == "closed" and getattr(p, "exit_time", 0) and p.exit_time >= day_start)
            if getattr(config, "KILL_SWITCH_ENABLED", True) and day_pnl <= -config.MAX_DAILY_LOSS_USD:
                print(f"🛑 KILL-SWITCH: дневной PnL ${day_pnl:.2f} <= -${config.MAX_DAILY_LOSS_USD}. Входы на паузе 5мин.")
                await asyncio.sleep(int(getattr(config, "KILL_SWITCH_PAUSE", 300)))
                continue
            if tracker.can_open_new_position(config.MAX_CONCURRENT_POSITIONS):
                mints_to_scan = []

                # 0. СВЕЖИЕ МИГРАЦИИ ПЕРВЫМИ (МГНОВЕННО БЕЗ СЕТЕВЫХ ЗАДЕРЖЕК):
                # Только что вышли из кёрва в Raydium/PumpSwap пул — окно ракеты секунды!
                graduated_mints = []
                try:
                    _raw_grads = birth_tracker.drain_graduated() or []
                    _now_g = time.time()
                    _fresh_cd_g = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 8))
                    _open_set_g = set(tracker.positions.keys())
                    for _gm in _raw_grads:
                        if not _gm or str(_gm).strip().startswith("0x") or _gm in _open_set_g:
                            birth_tracker.remove_graduated(_gm)
                            continue
                        if hasattr(tracker, "is_loss_cooldown_active") and tracker.is_loss_cooldown_active(_gm, float(getattr(config, "MINT_LOSS_COOLDOWN_SEC", 1800.0))):
                            birth_tracker.remove_graduated(_gm)
                            continue
                        if _now_g - processed_mints.get(_gm, 0.0) >= _fresh_cd_g:
                            graduated_mints.append(_gm)
                            if len(graduated_mints) >= 25:
                                break
                except Exception:
                    pass
                if graduated_mints:
                    print(f"🏁 Разбираю {len(graduated_mints)} свежих миграций ПЕРВЫМИ (0с задержки)!")
                    mints_to_scan.extend(graduated_mints)
                _graduated_set = set(graduated_mints)

                # 1. Готовый кэш дискавери из фонового фидера (Jupiter, GT, DexScreener): 0мс задержки!
                cached_discovery = getattr(analyzer, "_cached_discovery_mints", None)
                if not cached_discovery:
                    # Фоновый сборщик ещё не успел собрать при первом запуске — быстрый разовый опрос
                    try:
                        from jup_discovery import fetch_jup_tokens
                        cached_discovery = await fetch_jup_tokens() or []
                    except Exception:
                        cached_discovery = []
                if cached_discovery:
                    mints_to_scan.extend(cached_discovery)

                _now_scan_ts = time.time()
                if graduated_mints or (_now_scan_ts - _last_scan_log) >= 30.0:
                    _last_scan_log = _now_scan_ts
                    print(f"🔎 Сканируем монеты... (Открыто: {open_count}/{config.MAX_CONCURRENT_POSITIONS}, кандидатов: {len(mints_to_scan)})")
                
                # 2. Уличные Токены (Берем созревающие ракеты)
                if getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
                    mature_mints = birth_tracker.get_mature_tokens(float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.05)), 25)
                    mature_mints.sort(key=lambda _bm: float(birth_tracker.get_age(_bm) or 999.0))
                else:
                    # Если сырые кёрвы выключены — берём только проверенные миграции / waiting room
                    _wr_mints = list(getattr(birth_tracker, "waiting_room", {}).keys())
                    mature_mints = [m for m in _wr_mints if m not in _graduated_set]
                if mature_mints:
                    if graduated_mints or (_now_scan_ts - _last_wr_log) >= 30.0:
                        _last_wr_log = _now_scan_ts
                        print(f"🎂 В Waiting Room созревает {len(mature_mints)} миграций (ожидают индексации DEX и появления объёма)")
                    mints_to_scan.extend(mature_mints)

                # 3. Ручной вотчлист юзера (увидел рано на fomo - бот исполняет по своим гейтам)
                for _w in getattr(config, "USER_WATCHLIST", []) or []:
                    if _w and _w not in mints_to_scan:
                        mints_to_scan.append(_w)

                # Удаляем дубликаты и уже открытые позиции, порядок сохраняем (миграции остаются первыми)
                _open_mints_set = set(tracker.positions.keys())
                mints_to_scan = list(dict.fromkeys(
                    m.strip() for m in mints_to_scan
                    if m and m.strip() and 32 <= len(m.strip()) <= 44 and not m.strip().startswith("0x") and m.strip() not in _open_mints_set
                ))

                # ⚡ Пакетный префетч: вся очередь за ceil(N/30) запросов к DexScreener
                # вместо N одиночных. analyze_token() дальше читает из кэша без сети.
                try:
                    _now_pf = time.time()
                    _base_cd_pf = int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 30))
                    _fresh_cd_pf = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 8))
                    _pf_list = [
                        m for m in mints_to_scan
                        if m not in tracker.positions
                        and (_now_pf - processed_mints.get(m, 0.0)) >= (_fresh_cd_pf if m in _graduated_set else 15)
                    ][:300]
                    _pf_n = await analyzer.prefetch_token_data(_pf_list)
                    if _pf_list:
                        print(f"⚡ Префетч: {_pf_n}/{len(_pf_list)} токенов за {(len(_pf_list) + 29) // 30} запрос(а) DS.")
                except Exception as _pfe:
                    print(f"⚠️ prefetch err: {type(_pfe).__name__}")
                
                # ⚡ Сортировка по импульсу и фильтрация мертвых/старых токенов
                _REAL_NATIVE_MINTS = {
                    "So11111111111111111111111111111111111111112",   # WSOL
                    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
                    "USDH1SM1ojcxNC3Pec8NyUfqjf9qQyrZyZjBaP8Gh4",   # USDH
                    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",   # MSOL
                    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",   # BSOL
                    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # JitoSOL
                    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # WBTC
                    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # WETH
                }
                _ranked_mints = []
                _now_r = time.time()
                _base_cd_r = int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 30))
                _fresh_cd_r = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 8))
                for mint in mints_to_scan:
                    if not mint or mint in tracker.positions:
                        continue
                    if mint in _REAL_NATIVE_MINTS:
                        processed_mints[mint] = _now_r + 86400
                        continue
                    _c_pre = getattr(analyzer, "_prefetch_cache", {}).get(mint, (0, {}))[1]
                    _ca_r = (_c_pre.get("pairCreatedAt") or 0) if isinstance(_c_pre, dict) else 0
                    _age_r = (_now_r * 1000 - _ca_r) / 60000.0 if _ca_r else 0.2
                    _is_fresh_r = (mint in _graduated_set) or (_age_r <= 30.0)
                    _cd_r = _fresh_cd_r if _is_fresh_r else _base_cd_r
                    # ВАЖНО: Если токен ещё на кулдауне — НЕ перезаписываем processed_mints[mint] старыми данными из кэша каждые 3с!
                    if (_now_r - processed_mints.get(mint, 0.0)) < _cd_r:
                        continue
                    if _c_pre and isinstance(_c_pre, dict):
                        _base = _c_pre.get("baseToken", {}) or {}
                        _sym = str(_base.get("symbol", "")).upper().strip()
                        # Блокируем зомби-тикеры ТОЛЬКО если токен старый (>60м). Молодые мемы (кейс USDC +1403%) пропускаем!
                        if _age_r > 60.0 and _sym in ("SOL", "WSOL", "USDC", "USDT", "ETH", "BTC", "RAY"):
                            processed_mints[mint] = _now_r + 86400
                            continue

                        _c_dex = str(_c_pre.get("dexId", "")).lower()
                        if "meteora" in _c_dex and _c_dex != "meteoradbc" and not _c_pre.get("_lp_verified", False):
                            _c_tx_m = (_c_pre.get("txns") or {}).get("m5", {}) or {}
                            _c_b5_m = int(_c_tx_m.get("buys", 0) or 0)
                            _c_s5_m = int(_c_tx_m.get("sells", 0) or 0)
                            _c_v5_m = float((_c_pre.get("volume") or {}).get("m5", 0) or 0)
                            _c_l_m = float((_c_pre.get("liquidity") or {}).get("usd", 0) or 0)
                            _c_m5_m = float(((_c_pre.get("priceChange") or {}).get("m5", 0) or 0))
                            _rh_s5_sc_req = int(getattr(config, "RUG_HUNTER_EARLY_MIN_SELLS", 1)) if _age_r < 1.8 else 6
                            _rh_b5_sc_req = max(int(getattr(config, "RUG_HUNTER_EARLY_MIN_BUYS", 6)) if _age_r < 1.8 else 18, int(_c_s5_m * 1.10))
                            _rh_min_vol_sc = float(getattr(config, "RUG_HUNTER_MIN_VOL5_USD", 350.0)) if _age_r <= 0.8 else 800.0
                            _rh_min_m5_sc = -2.0 if _age_r <= 1.2 else 3.5
                            _rh_met_sc_ok = (
                                bool(getattr(config, "RUG_HUNTER_ENABLED", False))
                                and bool(getattr(config, "RUG_HUNTER_UNLOCKED_LP_ENABLED", False))
                                and float(getattr(config, "RUG_HUNTER_UNLOCKED_MIN_AGE_MIN", 0.15)) <= _age_r <= float(getattr(config, "RUG_HUNTER_UNLOCKED_MAX_AGE_MIN", 1.80))
                                and _c_l_m >= float(getattr(config, "RUG_HUNTER_MIN_LIQ_USD", 7500.0))
                                and _c_v5_m >= _rh_min_vol_sc
                                and _c_s5_m >= _rh_s5_sc_req
                                and _c_b5_m >= _rh_b5_sc_req
                                and _c_m5_m >= _rh_min_m5_sc
                            )
                            if not _rh_met_sc_ok:
                                processed_mints[mint] = (_now_r + 5) if _age_r <= 2.0 else (_now_r + 600)
                                continue
                        if not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False) and config.is_unmigrated_curve_token(mint, _c_pre):
                            # КРИТИЧНО: если токен уже в _graduated_set (пришёл из subscribeMigration), НЕ выкидываем его!
                            # DexScreener часто отстаёт на 1-3 мин и показывает старый dexId='pumpfun', тогда как
                            # analyzer.fetch_token_data() сразу проваливается в Jupiter v2 и видит свежий PumpSwap-пул за 2с!
                            if mint not in _graduated_set:
                                processed_mints[mint] = _now_r - _cd_r + 30
                                continue

                        _c_at = _c_pre.get("pairCreatedAt") or 0
                        _c_age_m = (_now_r * 1000 - _c_at) / 60000.0 if _c_at else 0.2
                        _c_pc = _c_pre.get("priceChange") or {}
                        _c_m5 = float(_c_pc.get("m5", 0) or 0)
                        _c_tx = (_c_pre.get("txns") or {}).get("m5", {}) or {}
                        _c_b5 = int(_c_tx.get("buys", 0) or 0)
                        _c_s5 = int(_c_tx.get("sells", 0) or 0)
                        _c_trades = _c_b5 + _c_s5
                        _c_vol5 = float((_c_pre.get("volume") or {}).get("m5", 0) or 0)
                        _c_liq = float((_c_pre.get("liquidity") or {}).get("usd", 0) or 0)
                        _c_fdv = float(_c_pre.get("fdv", 0) or _c_pre.get("marketCap", 0) or 0)

                        # 0мс отсев мёртвых трупов:
                        # 1) Слитые в $2-$21 выпускники Pump.fun (MGOAT $12, AMAT $3) — бан на 1ч + выкидываем из очереди миграций
                        if mint.endswith("pump") and _c_dex not in ("pump", "pumpfun") and 0 < _c_fdv < 1000.0:
                            processed_mints[mint] = _now_r + 3600
                            try:
                                birth_tracker.remove_graduated(mint)
                            except Exception:
                                pass
                            continue
                        # 2) Пустые $2,037 старты сырой кривой без покупателей — не забиваем ими 60 слотов сканера каждые 8с!
                        if config.is_unmigrated_curve_token(mint, _c_pre):
                            from sol_price import get_sol_price_sync as _get_sol_sc
                            _sol_sc = float(_get_sol_sc() or 75.0)
                            if _sol_sc <= 0:
                                _sol_sc = 75.0
                            _min_c_sc = max(float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 2750.0)), float(getattr(config, "MIN_PUMP_CURVE_MCAP_SOL", 38.0)) * _sol_sc)
                            if (0 < _c_fdv < _min_c_sc) or (_c_trades == 0 and _c_vol5 == 0):
                                processed_mints[mint] = _now_r - _cd_r + (90 if _c_trades == 0 else 45)
                                continue

                        _max_sc_age = float(getattr(config, "SOLANA_MAX_AGE_MIN", 240.0))
                        _max_sc_fdv = float(getattr(config, "JEV_MAX_MCAP_USD", 1500000.0))
                        _is_est_brk_sc = config.is_established_breakout_runner(
                            age_min=_c_age_m, liq_usd=_c_liq, vol5_usd=_c_vol5, m5_pct=_c_m5,
                            buys5=_c_b5, sells5=_c_s5, fdv_usd=_c_fdv,
                            is_curve=config.is_unmigrated_curve_token(mint, _c_pre),
                        )
                        if _c_vol5 >= 15000.0 or _is_est_brk_sc:
                            _max_sc_fdv = max(_max_sc_fdv, 2500000.0)
                        if _is_est_brk_sc:
                            _max_sc_age = max(_max_sc_age, float(getattr(config, "ESTABLISHED_BREAKOUT_MAX_AGE_MIN", 4320.0)))

                        # Свежая миграция (только что вышедшая на DEX в пределах раннего окна):
                        _is_fresh_grad = ((mint in _graduated_set) or (_c_age_m <= 20.0 and (config.is_launchpad_mint(mint) or _c_dex == "pumpswap"))) and (_c_age_m <= 25.0) and (_c_fdv <= _max_sc_fdv)

                        # Отсекаем перегретые топы — блокируем покупку на вершине:
                        if _c_fdv > _max_sc_fdv:
                            processed_mints[mint] = _now_r + 300
                            continue
                        if _c_age_m > _max_sc_age and not _is_fresh_grad:
                            processed_mints[mint] = _now_r + 600
                            continue

                        # Токен старше 45м без свежего импульса (<4%): скип на 90с
                        if _c_age_m > 45.0 and _c_m5 < 4.0 and not _is_fresh_grad:
                            processed_mints[mint] = _now_r - _cd_r + 90
                            continue

                        # Мертвый токен старше 3 минут с 0 сделок в m5: скип на 90с
                        if _c_age_m >= 3.0 and _c_trades == 0 and _c_vol5 == 0 and not _is_fresh_grad:
                            processed_mints[mint] = _now_r - _cd_r + 90
                            continue

                        # Отсекаем вялые стоячие/еле ползущие токены старше 5м без живого объёма: быстрый переопрос через 20с
                        if _c_age_m >= 5.0 and _c_vol5 < max(1500.0, float(getattr(config, "MIN_VOLUME_M5", 1500.0))) and not _is_fresh_grad:
                            processed_mints[mint] = _now_r - _cd_r + 20
                            continue

                        # Отсекаем флет и еле ползущие токены (m5 < 5%): пропускаем свежие миграции (<=10м), бычьи откаты (GTE7) и сильную аккумуляцию (PASSTHEHAT 402b/88s)
                        _is_bull_dip_sc = (_c_m5 >= -18.0 and _c_vol5 >= 20000.0 and _c_b5 >= max(50, int(_c_s5 * 1.15)) and _c_liq >= 10000.0)
                        _is_accum_sc = (_c_m5 >= 3.0 and _c_vol5 >= 5000.0 and _c_b5 >= max(60, int(_c_s5 * 1.50)) and _c_liq >= 7500.0)
                        _is_early_launch_sc = (_c_age_m <= 10.0 and _c_m5 >= 3.5 and _c_vol5 >= 3500.0 and _c_b5 >= max(25, int(_c_s5 * 1.20)) and _c_liq >= 7500.0)
                        if _c_m5 < 5.0 and _c_age_m > 2.5 and not _is_fresh_grad and not _is_bull_dip_sc and not _is_accum_sc and not _is_early_launch_sc:
                            processed_mints[mint] = _now_r - _cd_r + 20
                            continue

                        # РОКЕТ-ВЕЛОСИТИ РАНЖИРОВАНИЕ С ПРИОРИТЕТОМ СВЕЖЕСТИ (<=15м x2.5, <=45м x1.8):
                        import math
                        _vol_log = math.log10(max(_c_vol5, 100.0))
                        _bs_ratio = (_c_b5 / max(_c_s5, 1)) if _c_s5 > 0 else 1.5
                        _bs_ratio = min(max(_bs_ratio, 0.5), 3.0)
                        _fresh_sc_mult = 6.0 if _c_age_m <= 2.0 else (4.0 if _c_age_m <= 5.0 else (2.2 if _c_age_m <= 15.0 else (0.4 if _c_age_m > 30.0 else 1.0)))
                        _eff_m5_vel = max(_c_m5, 8.0) if _is_bull_dip_sc else _c_m5
                        _rkt_vel = _eff_m5_vel * _vol_log * _bs_ratio * _fresh_sc_mult

                        _is_ultra_early_rkt = (_c_age_m <= 1.8 and _c_liq >= 5000.0 and _c_vol5 >= 350.0 and _c_b5 >= max(5, int(_c_s5 * 1.05)))
                        _score_rank = (2500.0 if _is_ultra_early_rkt else (1000.0 if _is_fresh_grad else 0.0)) + _rkt_vel
                        _ranked_mints.append((_score_rank, mint))
                    else:
                        # Если данных в префетче нет: пускаем только свежие миграции, ручной вотчлист или молодой токен из роддома
                        if mint in _graduated_set:
                            _ranked_mints.append((1000.0, mint))
                        elif mint in getattr(config, "USER_WATCHLIST", []):
                            _ranked_mints.append((500.0, mint))
                        elif mint.endswith("pump") and birth_tracker.get_age(mint) is not None and birth_tracker.get_age(mint) <= 30.0:
                            _ranked_mints.append((10.0, mint))
                        else:
                            # Токен вне текущего окна префетча — быстрый кулдаун 15с вместо вечной блокировки
                            processed_mints[mint] = _now_r - _cd_r + 15

                _ranked_mints.sort(key=lambda x: x[0], reverse=True)
                mints_to_scan = [m for _, m in _ranked_mints]

                # ⚡ Параллельный разбор: отбираем до 15 подходящих кандидатов в окно
                _launch = []
                _now_l = time.time()
                for _m in mints_to_scan:
                    if not _m or _m in tracker.positions:
                        continue
                    if hasattr(tracker, "is_loss_cooldown_active") and tracker.is_loss_cooldown_active(_m, float(getattr(config, "MINT_LOSS_COOLDOWN_SEC", 1800.0))):
                        continue
                    _pre_l = getattr(analyzer, "_prefetch_cache", {}).get(_m, (0, {}))[1]
                    _ca_l = _pre_l.get("pairCreatedAt") or 0 if isinstance(_pre_l, dict) else 0
                    _age_l = (time.time() * 1000 - _ca_l) / 60000.0 if _ca_l else 0.2
                    _is_fresh_l = _m in _graduated_set or _age_l <= 30.0
                    _cd_l = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 8)) if _is_fresh_l else int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 30))
                    if _now_l - processed_mints.get(_m, 0.0) < _cd_l:
                        continue
                    _launch.append(_m)
                    if len(_launch) >= 60:
                        break
                _launch_n = len(_launch)
                _scan_conc = int(getattr(config, "SCAN_CONCURRENCY", 8))
                if _launch and _scan_conc > 1:
                    _scan_win = ScanWindow(analyzer, _launch, _scan_conc)

                # Итерируем СТРОГО по отобранному ТОП-60 окну _launch (а не по 300+ хвосту последовательно!),
                # чтобы цикл сканера всегда укладывался в 8-15 секунд и цены на входе были свежими!
                for mint in (_launch if _launch else mints_to_scan[:30]):
                    if not mint or mint in tracker.positions:
                        continue
                    if hasattr(tracker, "is_loss_cooldown_active") and tracker.is_loss_cooldown_active(mint, float(getattr(config, "MINT_LOSS_COOLDOWN_SEC", 1800.0))):
                        processed_mints[mint] = time.time()
                        continue
                    _pre_m = getattr(analyzer, "_prefetch_cache", {}).get(mint, (0, {}))[1]
                    _ca_m = _pre_m.get("pairCreatedAt") or 0 if isinstance(_pre_m, dict) else 0
                    if _ca_m:
                        _age_m = (time.time() * 1000 - _ca_m) / 60000.0
                    else:
                        _bt_age_m = None
                        try:
                            from birth_tracker import birth_tracker as _bt_c
                            _bt_age_m = _bt_c.get_age(mint)
                        except Exception:
                            pass
                        _age_m = float(_bt_age_m) if _bt_age_m is not None else 0.2
                    _is_fresh_m = mint in _graduated_set or _age_m <= 30.0
                    _cd = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 8)) if _is_fresh_m else int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 30))
                    if time.time() - processed_mints.get(mint, 0.0) < _cd:
                        continue
                    
                    # Держим память в чистоте
                    if len(processed_mints) > 1000:
                        processed_mints.clear()
        
                    _claimed_buy_flight = False
                    try:
                        # Используем умный маршрутизатор (сам выберет XGBoost или Raydium модель)
                        try:
                            _res = await asyncio.wait_for(_scan_win.get(mint), timeout=28.0) if _scan_win is not None else _SCAN_MISSING
                        except asyncio.TimeoutError:
                            _res = _SCAN_MISSING
                        if _res is _SCAN_MISSING:
                            try:
                                is_good = await asyncio.wait_for(analyzer.analyze_token(mint), timeout=25.0)
                            except asyncio.TimeoutError:
                                print(f"⏱️ [SCAN-TIMEOUT] {mint[:8]}: анализ превысил 25с, пропуск")
                                is_good = None
                        else:
                            is_good = _scan_apply(analyzer, _res)
                        _n_analyzed += 1
                        if is_good is not None and mint in _graduated_set:
                            # Метрика time_to_index: миграция → первый успешный разбор DS
                            try:
                                _tti = birth_tracker.graduated_age(mint)
                                if _tti >= 0:
                                    print(f"⏱️ [MIGRATION] {mint[:8]}: time_to_index {_tti:.0f}с (p95>480с → поднять TTL).")
                            except Exception:
                                pass
                        if is_good is None:
                            processed_mints[mint] = time.time() - _cd + 6   # Быстрая перепроверка через 6с (ловим свежий памп)
                            if mint in _graduated_set:
                                # DS ещё не проиндексировал новый пул — вернём в очередь, не теряем.
                                # Но после 3 пустых — это не лаг, а скам/нестандарт: хороним.
                                _n = _grad_none.get(mint, 0) + 1
                                _grad_none[mint] = _n
                                if _n >= 8:
                                    _grad_none.pop(mint, None)
                                    print(f"⚰️ [MIGRATION] {mint[:8]}: DS не видит после 8 попыток (~3.5м) — unindexable, хороню.")
                                else:
                                    try:
                                        birth_tracker.add_graduated(mint)
                                    except Exception:
                                        pass
                            continue
                        if is_good is False:
                            _sig = str(analyzer.get_signal(mint) or "")
                            _sig_l = _sig.lower()
                            _last_p = getattr(analyzer, "_last_analyzed_pair", {}).get(mint) or {}
                            _age_m = 999.0
                            if _last_p.get("pairCreatedAt"):
                                try:
                                    _age_m = max(0.0, (time.time() * 1000.0 - float(_last_p["pairCreatedAt"])) / 60000.0)
                                except Exception:
                                    pass
                            elif mint in _graduated_set:
                                try:
                                    _age_m = max(0.0, float(birth_tracker.graduated_age(mint)) / 60.0)
                                except Exception:
                                    pass

                            _is_young_unl = (_age_m <= 3.5) and any(k in _sig_l for k in (
                                "unlocked-lp", "meteora-unlocked-pool", "meteora-unlocked-waiting",
                                "liq-fdv-trap", "pumpswap-trap-waiting", "rh-too-young", "rh-low-vol5",
                                "rh-too-few-sells", "rh-sells-excess", "rh-low-buys", "unlocked-custom-lp",
                                "waiting"
                            ))

                            _is_terminal = (not _is_young_unl) and any(k in _sig_l for k in (
                                "rugcheck", "scam", "unlocked-lp", "meteora-unlocked-pool", "bundle", "honeypot", 
                                "freeze", "mimic", "dead-token", "мёртвый токен", "sybil", 
                                "delegation", "zombie", "bad-creator", "post-mig-dumped", "liq-fdv-trap",
                                "deadcat", "dead-cat", "ghost-pool", "wash-spike", 
                                "wash-trap", "anon-wash-churn", "mcap-too-large", "price-split"
                            ))
                            if _is_terminal:
                                processed_mints[mint] = time.time() + 3600      # Подтверждённый скам/мертвец: бан на 1 час и навсегда убираем из очереди миграций
                                try:
                                    birth_tracker.remove_graduated(mint)
                                except Exception:
                                    pass
                            elif _is_young_unl:
                                # Молодой незалоченный пул (age <= 3.5m) только стартует: быстрая перепроверка через 5с
                                processed_mints[mint] = time.time() - _cd + 5
                                if mint in _graduated_set:
                                    try:
                                        birth_tracker.add_graduated(mint)
                                    except Exception:
                                        pass
                            elif "pump-curve-too-early" in _sig_l:
                                processed_mints[mint] = time.time() + 45        # Пустой ранний кёрв ($2,037): не долбим каждые 10с
                            elif "ungraduated" in _sig_l or "bonding curve" in _sig_l or "curve" in _sig_l:
                                processed_mints[mint] = time.time() + 15        # Живая кривая: перепроверка через 15с
                            elif "откат" in _sig_l or "вертикаль" in _sig_l or "pullback" in _sig_l:
                                _pullbacks = getattr(analyzer, "_pullback_retries", {})
                                _p_cnt = _pullbacks.get(mint, 0) + 1
                                _pullbacks[mint] = _p_cnt
                                if _p_cnt <= 6:
                                    processed_mints[mint] = time.time() - _cd + 35  # Живой выпускник ждёт отката/импульса: перепроверить через 35с (до 6 раз)
                                else:
                                    processed_mints[mint] = time.time() + 600
                                    try:
                                        birth_tracker.remove_graduated(mint)
                                    except Exception:
                                        pass
                            elif mint in _graduated_set:
                                _last_p_g = getattr(analyzer, "_last_analyzed_pair", {}).get(mint) or {}
                                _last_m5_g = float(((_last_p_g.get("priceChange") or {}).get("m5", 0) or 0))
                                if _last_m5_g <= -70.0:
                                    processed_mints[mint] = time.time() + 300
                                    try:
                                        birth_tracker.remove_graduated(mint)
                                    except Exception:
                                        pass
                                else:
                                    _g_retries = getattr(analyzer, "_grad_retries", {})
                                    _g_cnt = _g_retries.get(mint, 0) + 1
                                    _g_retries[mint] = _g_cnt
                                    if _g_cnt <= 8:
                                        processed_mints[mint] = time.time() - _cd + 20
                                    else:
                                        processed_mints[mint] = time.time() + 120
                                        try:
                                            birth_tracker.remove_graduated(mint)
                                        except Exception:
                                            pass
                            elif any(k in _sig_l for k in ("low-vol5", "m5-too-slow", "no-rocket-ignition")):
                                processed_mints[mint] = time.time() - _cd + 25
                            else:
                                processed_mints[mint] = time.time() + 90
                        else:
                            processed_mints[mint] = time.time()
                        _sc_score_m = float(getattr(analyzer, "rocket_scores", {}).get(mint, getattr(analyzer, "last_score", 0.0)) or 0.0)
                        if not is_good:
                            analyzer.log_scan(mint[:8], mint, "SCANNER",
                                              False, _sc_score_m)

                        if is_good:
                            if not analyzer.try_claim_buy_flight(mint):
                                continue
                            _claimed_buy_flight = True
                            _cached_hit = getattr(analyzer, "_prefetch_cache", {}).get(mint, None)
                            if isinstance(_cached_hit, tuple) and len(_cached_hit) >= 2 and isinstance(_cached_hit[1], dict):
                                _cached_pair = dict(_cached_hit[1])
                            elif isinstance(_cached_hit, dict):
                                _cached_pair = dict(_cached_hit)
                            else:
                                _cached_pair = None
                            _last_an_p = getattr(analyzer, "_last_analyzed_pair", {}).get(mint)
                            _scan_px_sc = float((_last_an_p or _cached_pair or {}).get("priceUsd", 0) or 0)
                            # ОБЯЗАТЕЛЬНЫЙ сброс prefetch-кэша перед финальной проверкой и покупкой!
                            # Параллельно забираем свежий pair_data (fast=True, <=3.5с) + живую цену Jupiter + анти-клон,
                            # чтобы убрать 10-13с последовательных ожиданий после ANALYZER-PASS (кейс VINTEL)!
                            getattr(analyzer, "_prefetch_cache", {}).pop(mint, None)
                            _pre_sym_sc = str(((_last_an_p or _cached_pair or {}).get("baseToken") or {}).get("symbol") or "").strip()
                            _pre_dex_sc = str((_last_an_p or _cached_pair or {}).get("dexId") or "").lower()
                            from jupiter import JupiterAPI as _JupPreSc
                            from grok_jev import grok_jev as _gj_pre_sc

                            async def _sc_jup_px_task():
                                if _pre_dex_sc in ("pump", "pumpfun"):
                                    return 0.0
                                try:
                                    return await _JupPreSc.get_price(mint)
                                except Exception:
                                    return 0.0

                            async def _sc_clone_task():
                                if not _pre_sym_sc or not isinstance(_last_an_p, dict):
                                    return None
                                try:
                                    return await _gj_pre_sc.check_trending_clone(mint, _pre_sym_sc, _last_an_p)
                                except Exception:
                                    return (True, "")

                            _sc_res_td, _jup_chk_pre, _tc_res_pre = await asyncio.gather(
                                analyzer.fetch_token_data(mint, fast=True),
                                _sc_jup_px_task(),
                                _sc_clone_task(),
                                return_exceptions=True,
                            )
                            pair_data = _sc_res_td if isinstance(_sc_res_td, dict) else {}
                            if isinstance(_jup_chk_pre, Exception):
                                _jup_chk_pre = 0.0
                            if isinstance(_tc_res_pre, Exception):
                                _tc_res_pre = None
                            if isinstance(_last_an_p, dict):
                                for _fk in ("_lp_verified", "_unlocked_runner_ok", "_pumpswap_checked", "_pumpswap_canonical", "_pumpswap_lp_burned", "_pumpswap_rpc_unverified"):
                                    if _fk in _last_an_p and _fk not in pair_data:
                                        pair_data[_fk] = _last_an_p[_fk]
                            entry_price = float(pair_data.get("priceUsd", 0) or 0) if pair_data else 0.0
                            if _scan_px_sc > 0 and entry_price > 0:
                                if entry_price < _scan_px_sc * 0.92:
                                    print(f"🚫 [SCANNER-FINAL-RECHECK] {mint[:8]}: обвал цены на {(entry_price/_scan_px_sc - 1)*100:+.1f}% за время аудита — отмена входа!")
                                    continue
                                if entry_price > _scan_px_sc * 1.18:
                                    print(f"🚫 [SCANNER-FINAL-RECHECK] {mint[:8]}: цена улетела на {(entry_price/_scan_px_sc - 1)*100:+.1f}% за время аудита (>+18%, кейс CI +29% -> -23%) — не догоняем хай!")
                                    continue
                            if (not pair_data or entry_price <= 0) and _last_an_p:
                                pair_data = dict(_last_an_p)
                                entry_price = float(pair_data.get("priceUsd", 0) or 0)
                            if (not pair_data or entry_price <= 0) and _cached_pair:
                                pair_data = dict(_cached_pair)
                                entry_price = float(pair_data.get("priceUsd", 0) or 0)
                            if entry_price <= 0 and pair_data:
                                _fdv_calc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                if _fdv_calc > 0:
                                    entry_price = _fdv_calc / 1_000_000_000.0
                                    pair_data["priceUsd"] = entry_price
                            if entry_price <= 0:
                                try:
                                    from jupiter import JupiterAPI
                                    _j_pr = await JupiterAPI.get_price(mint)
                                    if _j_pr and _j_pr > 0:
                                        entry_price = _j_pr
                                        if pair_data:
                                            pair_data["priceUsd"] = _j_pr
                                        elif _cached_pair:
                                            pair_data = dict(_cached_pair)
                                            pair_data["priceUsd"] = _j_pr
                                        else:
                                            pair_data = {"baseToken": {"symbol": "UNKNOWN"}, "pairAddress": mint, "priceUsd": _j_pr}
                                except Exception:
                                    pass
                            actual_symbol = pair_data.get("baseToken", {}).get("symbol", "UNKNOWN") if pair_data else "UNKNOWN"
                            liq_usd = float(((pair_data or {}).get("liquidity", {}) or {}).get("usd", 0) or 0)
                            if liq_usd == 0 and (pair_data or {}).get("dexId") in ("pump", "pumpfun"):
                                _fdv = float(((pair_data or {}).get("fdv", 0) or (pair_data or {}).get("marketCap", 0) or 0))
                                liq_usd = _fdv * 0.40
                            if entry_price > 0:
                                if pair_data.get("_price_divergence"):
                                    _div_r = float(pair_data.get("_price_divergence_ratio", 0.0) or 0.0)
                                    print(f"🚫 [SCANNER-PRICE-SPLIT] {actual_symbol} ({mint[:8]}): рассинхрон пулов {_div_r:.1f}x — блок.")
                                    continue
                                _dex_id = str((pair_data.get("dexId") or "")).lower()
                                _allow_unl_sc = bool(getattr(config, "ALLOW_UNLOCKED_LP_POOLS", True))
                                _sig_cur_sc = str(analyzer.get_signal(mint) or getattr(analyzer, "last_signal", "") or "").upper()
                                _is_rh_cand_sc = bool(
                                    pair_data.get("_rug_hunter_mode")
                                    or ("RUG-HUNTER" in _sig_cur_sc)
                                )
                                if not _is_rh_cand_sc and "meteora" in _dex_id and _dex_id != "meteoradbc" and not pair_data.get("_lp_verified", False):
                                    print(f"🚫 [SCANNER-METEORA-UNBURNED] {actual_symbol} ({mint[:8]}): пул Meteora без сожжённого/залоченного LP — риск Remove LP (кейсы USPR/BUFO/SPACE), отмена!")
                                    continue
                                _is_unl_sc_pool = config.is_position_unlocked_lp(mint, pair_data, bool(pair_data.get("_lp_verified")))
                                if _is_unl_sc_pool:
                                    _ca_sc = float(pair_data.get("pairCreatedAt") or 0)
                                    _age_sc_unl = ((time.time() * 1000.0 - _ca_sc) / 60000.0) if _ca_sc > 0 else 0.1
                                    _tx5_sc_u = (pair_data.get("txns") or {}).get("m5") or {}
                                    _b5_sc_u = int(_tx5_sc_u.get("buys", 0) or 0)
                                    _s5_sc_u = int(_tx5_sc_u.get("sells", 0) or 0)
                                    _vol5_sc_u = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
                                    _fdv_sc_u = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                    _m5_sc_u = ((pair_data.get("priceChange") or {}).get("m5"))
                                    _sc_xgb_sc = float(getattr(analyzer, "rocket_scores", {}).get(mint, getattr(analyzer, "last_score", 0.0)) or 0.0)
                                    _min_unl_xgb = float(getattr(config, "ROCKET_XGB_UNLOCKED_CUSTOM_MIN", 35.0))
                                    if _sc_xgb_sc < _min_unl_xgb:
                                        print(f"🚫 [SCANNER-UNLOCKED-XGB] {actual_symbol} ({mint[:8]}): незалоченный кастомный пул XGB {_sc_xgb_sc:.1f}% < {_min_unl_xgb:.0f}% — отмена (риск мгновенного слива пула)!")
                                        continue
                                    if not config.is_safe_unlocked_custom_runner(
                                        liq_usd=liq_usd,
                                        age_min=_age_sc_unl,
                                        vol5_usd=_vol5_sc_u,
                                        buys5=_b5_sc_u,
                                        sells5=_s5_sc_u,
                                        fdv_usd=_fdv_sc_u,
                                        m5_pct=(float(_m5_sc_u) if _m5_sc_u is not None else None),
                                    ):
                                        print(f"🚫 [SCANNER-UNLOCKED-GATE] {actual_symbol} ({mint[:8]}): незалоченный пул не прошёл проверку liq/fdv/оборота (age {_age_sc_unl:.2f}м, liq ${liq_usd:,.0f}, fdv ${_fdv_sc_u:,.0f}, vol5 ${_vol5_sc_u:,.0f}, {_b5_sc_u}b/{_s5_sc_u}s) — отмена!")
                                        continue
                                if not config.is_allowed_solana_quote(pair_data):
                                    _bad_q_sc = str((pair_data.get("quoteToken") or {}).get("symbol") or "?")
                                    print(f"🚫 [SCANNER-BAD-QUOTE] {actual_symbol} ({mint[:8]}): пара к {_bad_q_sc} (не SOL/стейблкоин, кейсы JETSKI/FOILED) — отмена!")
                                    continue
                                if config.is_permissionless_liq_fdv_trap(pair_data):
                                    print(f"🚫 [SCANNER-LIQ-TRAP] {actual_symbol} ({mint[:8]}): seeded trap pool (liq/fdv drain risk) — отмена!")
                                    continue
                                if config.is_unmigrated_curve_token(mint, pair_data):
                                    _fdv_m_sc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                    _min_c_sc = float(getattr(config, "MIN_PUMP_CURVE_MCAP_USD", 7500.0))
                                    _max_c_sc = float(getattr(config, "MAX_PUMP_CURVE_MCAP_USD", 42000.0))
                                    if not getattr(config, "ALLOW_UNGRADUATED_PUMPFUN", False):
                                        print(f"🚫 [SCANNER-RAW-CURVE] {actual_symbol} ({mint[:8]}): сырая кривая bonding curve без миграции на DEX — отмена!")
                                        continue
                                    if 0 < _fdv_m_sc < _min_c_sc:
                                        print(f"🚫 [SCANNER-CURVE-FLASH-RUG] {actual_symbol} ({mint[:8]}): FDV ${_fdv_m_sc:,.0f} < ${_min_c_sc:,.0f} (пустой кёрв, flash-rug) — отмена!")
                                        continue
                                    if _fdv_m_sc > _max_c_sc:
                                        print(f"🚫 [SCANNER-CURVE-CLIFF] {actual_symbol} ({mint[:8]}): FDV ${_fdv_m_sc:,.0f} > ${_max_c_sc:,.0f} (обрыв перед миграцией) — отмена!")
                                        continue
                                _min_grad_sc = float(getattr(config, "MIN_GRADUATED_LIQ_USD", 5000.0))
                                if _dex_id not in ("pump", "pumpfun") and liq_usd < _min_grad_sc:
                                    print(f"🚫 [SCANNER-LOW-LIQ] {actual_symbol} ({mint[:8]}): ликвидность DEX ${liq_usd:,.0f} < ${_min_grad_sc:,.0f} — отмена!")
                                    continue
                                # Не-launchpad токены (не pump/moon/bonk/BAGS) проверяются далее через RugCheck LP-lock/burn и аудит рисков!
                                _pc_live = pair_data.get("priceChange") or {}
                                _m5_live = float(_pc_live.get("m5", 0) or 0)
                                _m1_live = float(_pc_live.get("m1", 0) or 0)
                                _h1_live = float(_pc_live.get("h1", 0) or 0)
                                _pk_drop_live = float(pair_data.get("_drop_from_peak_5m", 0.0) or 0.0)
                                _lq_drop_live = float(pair_data.get("_liq_drop_from_peak", 0.0) or 0.0)
                                _vol5_live = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))  # FIX: объявлено до первого использования (был UnboundLocalError при m5 > MAX_ENTRY_M5_PCT)

                                # Блок: токен уже на полу — h1 сильно отрицательный при возрасте > 5 мин
                                if _h1_live < -60.0 and _age_m > 5.0:
                                    print(f"🚫 [SCANNER-FLOOR-TOKEN] {actual_symbol} ({mint[:8]}): h1 {_h1_live:.0f}% — токен уже на полу, входить нельзя!")
                                    continue
                                # Блок: токен созревает со 2-й свечи (карантин блока 0)
                                _min_sc_age = float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.05))
                                if _age_m < _min_sc_age:
                                    print(f"⏳ [SCANNER-WARMUP] {actual_symbol} ({mint[:8]}): пул {_age_m:.1f}м < {_min_sc_age:.1f}м — ждём первых сделок.")
                                    processed_mints[mint] = time.time() - _cd + 4
                                    continue

                                # Повторная проверка живых метрик после сброса prefetch-кэша (единый классификатор пропускает откат покупателей и V-развороты)
                                _b5_live = int(((pair_data.get("txns") or {}).get("m5") or {}).get("buys", 0) or 0)
                                _s5_live = int(((pair_data.get("txns") or {}).get("m5") or {}).get("sells", 0) or 0)
                                _b_h1_live = int(((pair_data.get("txns") or {}).get("h1") or {}).get("buys", 0) or 0)
                                _s_h1_live = int(((pair_data.get("txns") or {}).get("h1") or {}).get("sells", 0) or 0)
                                _h6_live = _pc_live.get("h6")
                                _fdv_live_sc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                _is_curve_live = config.is_unmigrated_curve_token(mint, pair_data)
                                _mom_live_ok, _mom_live_why = config.evaluate_entry_momentum(
                                    m5=_m5_live, m1=_m1_live, h1=_h1_live,
                                    h6=(float(_h6_live) if _h6_live is not None else None),
                                    age_min=_age_m, vol5_usd=_vol5_live, liq_usd=liq_usd,
                                    buys5=_b5_live, sells5=_s5_live,
                                    min_h1=float(getattr(config, "JEV_MIN_H1_PCT", -12.0)),
                                    min_h6=float(getattr(config, "JEV_MIN_H6_PCT", -20.0)),
                                    fdv_usd=_fdv_live_sc,
                                    is_curve=_is_curve_live,
                                    buys_h1=_b_h1_live if (_b_h1_live or _s_h1_live) else None,
                                    sells_h1=_s_h1_live if (_b_h1_live or _s_h1_live) else None,
                                )
                                _min_sc_m1 = float(getattr(config, "JEV_MIN_M1_ENTRY_FLOOR_PCT", 0.0))
                                _px_held_audit = bool(_scan_px_sc <= 0 or entry_price >= _scan_px_sc * 0.95)
                                _hard_mom_block = any(
                                    k in str(_mom_live_why or "").lower()
                                    for k in ("cabal-staircase", "fake-bump", "h1-exhausted", "h6-exhausted", "m1-vertical-top", "m5-parabolic-top", "mcap-too-large", "too-old", "bleeding-h1")
                                )
                                # Если монета уже прошла ANALYZER-PASS и 8 слоёв аудита, цена за время проверки удержалась (>= -5%),
                                # покупатели не проигрывают (b5 >= s5) и 1м свеча не падает — не отменяем вход из-за мелкого сдвига 5м окна DexScreener (кейс PASSTHEHAT ML 59.8%!)
                                _is_minor_m5_wiggle = (
                                    not _hard_mom_block
                                    and _px_held_audit
                                    and _m5_live >= -8.0
                                    and _m1_live >= -2.5
                                    and _b5_live >= max(20, _s5_live)
                                    and _vol5_live >= 4000.0
                                )
                                if (not _mom_live_ok and not _is_minor_m5_wiggle) or _m1_live < _min_sc_m1:
                                    print(f"🚫 [SCANNER-LIVE-REVERSAL] {actual_symbol} ({mint[:8]}): на живом графике импульс сломан ({_mom_live_why}, m5 {_m5_live:+.1f}%, m1 {_m1_live:+.1f}% < {_min_sc_m1:+.1f}%) — отмена покупки!")
                                    continue
                                _is_live_m1_blast = (_m1_live >= 1.5 and _b5_live >= max(8, _s5_live) and _vol5_live >= 1500.0)
                                _m5_live_floor = -8.0 if _is_minor_m5_wiggle else (0.0 if _is_live_m1_blast else 0.8)
                                if _m5_live < _m5_live_floor:
                                    print(f"🚫 [SCANNER-LIVE-FALLING] {actual_symbol} ({mint[:8]}): живой m5 {_m5_live:+.1f}% < +{_m5_live_floor:.1f}% — падающий нож перед входом, отмена покупки!")
                                    continue
                                _m1_dump_floor = min(-4.5, _min_sc_m1)
                                if _m1_live < _m1_dump_floor:
                                    print(f"🚫 [SCANNER-LIVE-M1-DUMP] {actual_symbol} ({mint[:8]}): живой m1 {_m1_live:+.1f}% < {_m1_dump_floor:+.1f}% — резкий сброс за 1м, отмена покупки!")
                                    continue
                                _max_m1_cfg_live = float(getattr(config, "MAX_ENTRY_M1_PCT", 22.0))
                                if _m1_live > _max_m1_cfg_live:
                                    _b5_sc = int((pair_data.get("txns") or {}).get("m5", {}).get("buys", 0) or 0)
                                    _s5_sc = int((pair_data.get("txns") or {}).get("m5", {}).get("sells", 0) or 0)
                                    if not (not _is_curve_live and liq_usd >= 10000.0 and _b5_sc >= max(40, int(_s5_sc * 1.20)) and _vol5_live >= 12000.0 and _m5_live >= 25.0 and _m1_live <= 28.0):
                                        print(f"🚫 [SCANNER-M1-SPIKE] {actual_symbol} ({mint[:8]}): живой m1 +{_m1_live:.1f}% > +{_max_m1_cfg_live:.1f}% — вершина 1м свечи (кейс MAGA), отмена!")
                                        continue
                                _fdv_sc_live = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                _max_m5_cfg = (300.0 if (_m1_live >= 2.0 and _s5_live < 80 and _b5_live >= max(30, int(_s5_live * 1.80))) else 165.0) if _is_curve_live else float(getattr(config, "MAX_ENTRY_M5_PCT", 175.0))
                                if _m5_live > _max_m5_cfg:
                                    _b5_sc = int((pair_data.get("txns") or {}).get("m5", {}).get("buys", 0) or 0)
                                    _s5_sc = int((pair_data.get("txns") or {}).get("m5", {}).get("sells", 0) or 0)
                                    _is_fresh_climb_live = (not _is_curve_live and _age_m <= 5.0 and 0.0 < _fdv_sc_live <= 55000.0 and liq_usd >= 7500.0 and _vol5_live >= 5000.0 and _b5_sc >= max(25, int(_s5_sc * 1.15)) and _m5_live <= 450.0)
                                    _is_mega_mig_live = (not _is_curve_live and _age_m <= 4.0 and _vol5_live >= 30000.0 and liq_usd >= 12000.0 and _b5_sc >= max(80, int(_s5_sc * 1.15)) and _m1_live >= 2.0 and _m5_live <= 450.0)
                                    _is_fresh_grad_live = (not _is_curve_live and _age_m <= 5.0 and _vol5_live >= 20000.0 and liq_usd >= 12000.0 and _b5_sc >= max(30, int(_s5_sc * 1.15)) and _m5_live <= 300.0)
                                    _is_breakout = (not _is_curve_live and _vol5_live >= 15000.0 and liq_usd >= 12000.0 and _b5_sc >= int(_s5_sc * 1.2) and _m5_live <= 240.0)
                                    if not (_is_fresh_climb_live or _is_mega_mig_live or _is_fresh_grad_live or _is_breakout):
                                        print(f"🚫 [SCANNER-FOMO-M5] {actual_symbol} ({mint[:8]}): живой m5 +{_m5_live:.1f}% > +{_max_m5_cfg:.0f}% — вершина свечи, отмена!")
                                        continue
                                _info_sc_d = pair_data.get("info") or {}
                                _links_sc = len(_info_sc_d.get("socials") or []) + len(_info_sc_d.get("websites") or [])
                                _soc_unk_sc = bool(pair_data.get("_socials_unknown", False))
                                _b5_sc = int((pair_data.get("txns") or {}).get("m5", {}).get("buys", 0) or 0)
                                _s5_sc = int((pair_data.get("txns") or {}).get("m5", {}).get("sells", 0) or 0)
                                if getattr(config, "REQUIRE_SOCIALS_ENABLED", False) and _links_sc == 0 and not _soc_unk_sc and _age_m < float(getattr(config, "REQUIRE_SOCIALS_MIN_AGE_MIN", 9999.0)):
                                    _dex_sc_low = str(pair_data.get("dexId", "")).lower()
                                    _is_lp_or_pump = mint.endswith("pump") or mint.endswith("moon") or _dex_sc_low in ("pump", "pumpfun", "pumpswap")
                                    _is_stealth_sc = (
                                        _is_lp_or_pump
                                        or (_vol5_live >= 3500.0 and _b5_sc >= 20 and _b5_sc >= int(_s5_sc * 1.10))
                                        or (liq_usd >= 15000.0 and _b5_sc >= 25 and _b5_sc >= _s5_sc)
                                    )
                                    if not _is_stealth_sc:
                                        print(f"🚫 [SCANNER-NO-SOC] {actual_symbol} ({mint[:8]}): 0 соцсетей без органики — отмена!")
                                        continue
                                _max_h1_live = config.get_max_entry_h1_pct(
                                    _age_m, _vol5_live, _b5_sc, _s5_sc, liq_usd, _m5_live,
                                    fdv_usd=_fdv_sc_live, is_curve=_is_curve_live,
                                )
                                if _h1_live > _max_h1_live:
                                    print(f"🚫 [SCANNER-LIVE-EXHAUSTED] {actual_symbol} ({mint[:8]}): живой h1 {_h1_live:+.0f}% > +{_max_h1_live:.0f}% — перегретая вершина, отмена!")
                                    continue
                                # СТАЛО (в main.py строки 506-524):
                                # ЖЁСТКИЙ фильтр: не входим, если ликва уже просела от пика (признак вывода девом)
                                if _pk_drop_live <= -15.0 or _lq_drop_live <= -15.0:
                                    print(f"🚫 [SCANNER-POST-PEAK] {actual_symbol} ({mint[:8]}): токен слился от пика (цена {_pk_drop_live:+.1f}%, ликва {_lq_drop_live:+.1f}%) — отмена!")
                                    continue

                                _max_age_live = float(getattr(config, "SOLANA_MAX_AGE_MIN", 240.0))
                                _max_fdv_live = float(getattr(config, "JEV_MAX_MCAP_USD", 850000.0))
                                _fdv_sc_live = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                _vol5_live = float(((pair_data.get("volume") or {}).get("m5", 0) or 0))
                                if _fdv_sc_live > _max_fdv_live:
                                    print(f"🚫 [SCANNER-LARGE-CAP] {actual_symbol} ({mint[:8]}): FDV ${_fdv_sc_live:,.0f} > ${_max_fdv_live:,.0f} — нет запаса хода 2x+ (покупка вершины исключена).")
                                    continue
                                _is_active_breakout_live = config.is_established_breakout_runner(
                                    age_min=_age_m, liq_usd=liq_usd, vol5_usd=_vol5_live, m5_pct=_m5_live,
                                    buys5=_b5_sc, sells5=_s5_sc, fdv_usd=_fdv_sc_live, is_curve=_is_curve_live,
                                )
                                if _age_m > _max_age_live and not _is_active_breakout_live:
                                    print(f"🚫 [SCANNER-OLD-ZOMBIE] {actual_symbol} ({mint[:8]}): возраст {_age_m:.0f}м > {_max_age_live:.0f}м — старый пул, отмена!")
                                    continue

                                _req_soc_age = float(getattr(config, "REQUIRE_SOCIALS_MIN_AGE_MIN", 25.0))
                                _tx5_sc = (pair_data.get("txns") or {}).get("m5", {}) or {}
                                _b5_bump = int(_tx5_sc.get("buys", 0) or 0)
                                _s5_bump = int(_tx5_sc.get("sells", 0) or 0)
                                _tot_tx5_sc = _b5_bump + _s5_bump
                                _avg_tx5_sc = (_vol5_live / _tot_tx5_sc) if _tot_tx5_sc > 0 else 0.0
                                if getattr(config, "REQUIRE_SOCIALS_ENABLED", False) and _age_m >= _req_soc_age and _links_sc == 0 and not _soc_unk_sc:
                                    _vl_sc = _vol5_live / max(liq_usd, 1.0)
                                    _is_lp_mint_sc = config.is_launchpad_mint(mint) or _dex_id in ("pump", "pumpfun")
                                    _is_organic_stealth = (
                                        (_is_lp_mint_sc or liq_usd >= 5000.0 or _b5_bump >= 20)
                                        and _vol5_live >= 1200.0 and _b5_bump >= 12 and _b5_bump >= _s5_bump * 1.02
                                        and (liq_usd >= 5000.0 or _is_lp_mint_sc) and _vl_sc <= 8.5
                                    )
                                    _is_viral_stealth = (
                                        _vol5_live >= 12000.0 and _b5_bump >= 60 and _b5_bump >= int(_s5_bump * 1.05)
                                    )
                                    if not (_is_organic_stealth or _is_viral_stealth):
                                        print(f"🚫 [SCANNER-NO-SOCIALS] {actual_symbol} ({mint[:8]}): 0 соцсетей в зрелом возрасте {_age_m:.0f}м (>={_req_soc_age:.0f}м) — отмена!")
                                        continue
                                    else:
                                        _st_tag = "SCANNER-VIRAL-STEALTH" if _is_viral_stealth else "SCANNER-ORGANIC-STEALTH"
                                        print(f"💎 [{_st_tag}] {actual_symbol} ({mint[:8]}): 0 соцсетей, но подтвержден органический прорыв — пропуск в анализ.")

                                # Защита от микро bump-ботов: не блокируем реальный розничный поток при покупках >= продаж
                                _is_retail_flow_sc = (_b5_bump >= _s5_bump and _vol5_live >= 2000.0)
                                if _tot_tx5_sc >= 150 and _avg_tx5_sc < 3.5 and not _is_retail_flow_sc:
                                    print(f"🚫 [SCANNER-WASH-TRAP] {actual_symbol} ({mint[:8]}): накрутка микро-ботом ({_tot_tx5_sc} txns, avg ${_avg_tx5_sc:.2f} < $3.5) — отмена!")
                                    continue
                                _is_deep_launchpad_runner = (
                                    (config.is_launchpad_mint(mint) or _dex_id in ("pump", "pumpfun"))
                                    and liq_usd >= 12000.0
                                    and _vol5_live >= 8000.0
                                    and _m5_live >= 3.0
                                    and _b5_bump >= _s5_bump * 1.05
                                )
                                if _tot_tx5_sc >= 200 and _avg_tx5_sc < 4.0 and _links_sc == 0 and not _is_deep_launchpad_runner and not _is_retail_flow_sc:
                                    print(f"🚫 [SCANNER-WASH-TRAP] {actual_symbol} ({mint[:8]}): накрутка микро-ботом ({_tot_tx5_sc} txns, avg ${_avg_tx5_sc:.2f} < $4) — отмена!")
                                    continue

                                # Блок кастомных не-launchpad незалоченных контрактов на PumpSwap при пуле < $10k (отключается при ALLOW_UNLOCKED_LP_POOLS=True)
                                if not bool(getattr(config, "ALLOW_UNLOCKED_LP_POOLS", False)) and _dex_id == "pumpswap" and not config.is_launchpad_mint(mint) and not pair_data.get("_lp_verified", False) and not pair_data.get("_pumpswap_lp_burned", False) and liq_usd < 10000.0:
                                    print(f"🚫 [SCANNER-FAKE-PUMPSWAP] {actual_symbol} ({mint[:8]}): кастомный незалоченный контракт на PumpSwap с микро-пулом (${liq_usd:,.0f} < $10k) — отмена!")
                                    continue

                                # Защита от миграционного дампа Pump.fun
                                _c_ms_sc = pair_data.get("pairCreatedAt") or 0
                                _age_sc = ((time.time() * 1000 - _c_ms_sc) / 60000.0) if _c_ms_sc else _age_m
                                if mint.endswith("pump") and _dex_id in ("pumpswap", "raydium", "meteora", "orca"):
                                    _has_launch_surge = (_vol5_live >= 300.0 and _b5_bump >= _s5_bump and _b5_bump >= 3 and _m5_live >= 0.5)
                                    _min_mig_age_sc = float(getattr(config, "SOLANA_MIN_AGE_MIN", 0.15))
                                    if _age_sc < _min_mig_age_sc and not _has_launch_surge:
                                        processed_mints[mint] = time.time() - _cd + 10
                                        try:
                                            from birth_tracker import birth_tracker as _bt_sc
                                            _bt_sc.add_to_waiting_room(mint)
                                        except Exception:
                                            pass
                                        print(f"⏳ [SCANNER-MIGRATION] {actual_symbol} ({mint[:8]}): пул {_age_sc:.1f}м < {_min_mig_age_sc:.2f}м без сделок — повтор через 10с.")
                                        continue
                                    _fdv_sc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                    from sol_price import get_sol_price_sync
                                    _sol_p_sc = float(get_sol_price_sync() or 75.0)
                                    if _sol_p_sc <= 0: _sol_p_sc = 75.0
                                    _dump_floor_sc = float(getattr(config, "PUMP_DUMP_FLOOR_SOL", 50.0)) * _sol_p_sc
                                    if 0 < _fdv_sc < _dump_floor_sc and not (_age_sc < 5.0 and _vol5_live >= 1000.0 and _m5_live >= 1.5 and _b5_bump >= _s5_bump):
                                        print(f"🚫 [SCANNER-MIGRATION] {actual_symbol} ({mint[:8]}): FDV ${_fdv_sc:,.0f} < ${_dump_floor_sc:,.0f} — слит ниже уровня миграции.")
                                        continue
                                    _min_mig_liq_sc = float(getattr(config, "MIN_GRADUATED_LIQ_USD", 3000.0))
                                    if liq_usd < _min_mig_liq_sc:
                                        print(f"🚫 [SCANNER-MIGRATION] {actual_symbol} ({mint[:8]}): ликвидность ${liq_usd:,.0f} < ${_min_mig_liq_sc:,.0f} — микро-пул.")
                                        continue

                                # Живая проверка цены Jupiter для ЛЮБЫХ токенов на AMM (переиспользуем параллельный запрос _jup_chk_pre — 0мс задержки!)
                                if _dex_id not in ("pump", "pumpfun"):
                                    from jupiter import JupiterAPI
                                    _jup_chk = _jup_chk_pre if (_jup_chk_pre and _jup_chk_pre > 0) else (await JupiterAPI.get_price(mint))
                                    if _jup_chk and _jup_chk > 0 and entry_price > 0:
                                        _live_dump_thr = 0.88 if (_age_sc and _age_sc < 3.0) else 0.93
                                        if _jup_chk < entry_price * _live_dump_thr:
                                            _pct_down = (1.0 - _jup_chk / entry_price) * 100
                                            analyzer._deny(mint, f"scanner-live-dump >{100-_live_dump_thr*100:.0f}%", f"🚫 [LIVE-DUMP] {actual_symbol} ({mint[:8]}): живая цена Jupiter (${_jup_chk:.8f}) уже на {_pct_down:.1f}% ниже DexScreener (${entry_price:.8f}) [>{100-_live_dump_thr*100:.0f}%] — токен обваливается прямо сейчас, блок входа!")
                                            continue
                                        _chk_up_r = _jup_chk / entry_price
                                        if _chk_up_r > 1.45:
                                            print(f"🚫 [SCANNER-PRICE-MISMATCH] {actual_symbol} ({mint[:8]}): Jupiter (${_jup_chk:.8f}) vs DS (${entry_price:.8f}) опережение {_chk_up_r:.2f}x (>1.45x) — блок от вертикального входа.")
                                            continue
                                        entry_price = _jup_chk
                                # RKT-вето дженерик-входов Solana (лотереи/VIP-билеты exempt).
                                _ssig = analyzer.get_signal(mint)
                                _sveto = analyzer.rocket_veto(mint, pair_data, _ssig)
                                if _sveto:
                                    analyzer._deny(mint, f"scanner-veto: {_sveto[:20]}", f"🚫 [SCANNER] {actual_symbol}: {_sveto}")
                                    analyzer.log_scan(actual_symbol, mint, "SCANNER", False,
                                                      getattr(analyzer, "last_score", 0.0))
                                    await asyncio.sleep(1.5)
                                    continue

                                # === Analyzer уже выполнил ROCKET-XGB S1+S2 и 8-слойный ончейн-аудит (RPC + RugCheck + L7 Insiders + L8 Delegations + GoPlus).
                                # Здесь выполняем только 0ms анти-клон (или переиспользуем _tc_res_pre) и расчёт точки входа / стопа под фитиль! ===
                                from grok_jev import grok_jev
                                _cc_ok, _cc_why = grok_jev.check_copycat_runner(mint, actual_symbol, pair_data)
                                if not _cc_ok:
                                    print(f"🚫 [SCANNER-COPYCAT] {actual_symbol} ({mint[:8]}): {_cc_why} — отказ входа.")
                                    continue
                                if isinstance(_tc_res_pre, tuple) and len(_tc_res_pre) == 2 and actual_symbol == _pre_sym_sc:
                                    _tc_ok, _tc_why = _tc_res_pre
                                else:
                                    _tc_ok, _tc_why = await grok_jev.check_trending_clone(mint, actual_symbol, pair_data)
                                if not _tc_ok:
                                    print(f"🚫 [SCANNER-CLONE] {actual_symbol} ({mint[:8]}): {_tc_why} — отказ входа.")
                                    continue

                                _flush_ok, _wick_pct, _wick_stop_price, _flush_msg = grok_jev.check_flush_and_wick(pair_data, entry_price)
                                if not _flush_ok:
                                    processed_mints[mint] = time.time() - _cd + 25
                                    print(f"⏳ [JEV-FILLS] {actual_symbol} ({mint[:8]}): {_flush_msg}")
                                    continue
                                else:
                                    print(f"🎯 [JEV-FILLS] {actual_symbol} ({mint[:8]}): {_flush_msg}")

                                if getattr(config, "DYNAMIC_REINVEST_ENABLED", True):
                                    position_size = tracker.calculate_dynamic_position_size(pair_data, chain="solana")
                                    if position_size <= 0:
                                        print(f"🚫 [SCANNER] {actual_symbol}: недостаточно свободного кэша для входа.")
                                        await asyncio.sleep(1.0)
                                        continue
                                    _is_lot = False
                                    _is_vipsig = False
                                    fixed = 0.0
                                else:
                                    fixed = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                                    if fixed > 0:
                                        position_size = fixed
                                        _is_lot = False
                                        _is_vipsig = False
                                        try:
                                            _meme = await analyzer.meme_tag(mint, actual_symbol, pair_data, "solana")
                                            if _meme:
                                                analyzer.signals[mint] = analyzer.get_signal(mint) + _meme
                                        except Exception:
                                            pass
                                    else:
                                        capital = tracker.get_total_capital()
                                        base_position = capital * (config.REINVEST_PERCENT / 100.0)
                                        max_allowed_by_pool = liq_usd * 0.01 if liq_usd > 0 else 99999.0  
                                        position_size = max(5.0, min(base_position, max_allowed_by_pool, 100.0))
                                    
                                        _lot_m5 = ((pair_data.get("priceChange") or {}).get("m5", 0) or 0)
                                        _is_lot = _lot_m5 >= getattr(config, "LOTTERY_MIN_M5_PCT", 1.0) * 100
                                        
                                        # Если это настоящая VIP-ракета (огромный объем), входим на полный сайз
                                        _txns_m5 = pair_data.get("txns", {}).get("m5", {})
                                        _buys = _txns_m5.get("buys", 0)
                                        _vol = pair_data.get("volume", {}).get("m5", 0)
                                        _is_vip = _buys >= 50 and _vol >= 30000
                                        
                                        if _is_lot and not _is_vip:
                                            position_size *= getattr(config, "LOTTERY_SIZE_MULT", 0.25)

                                        # Conviction-тиринг и ROCKET-mult: только для динамического сайзинга (если TRADE_AMOUNT_USD не задан)
                                        if not _is_lot and not _is_vip:
                                            position_size *= analyzer.conviction_size_mult(pair_data)
                                            try:
                                                analyzer._rkt_tag(mint, pair_data)
                                                _rm = analyzer.rocket_size_mult(mint)
                                                if _rm != 1.0:
                                                    print(f"🚀 SCANNER {actual_symbol}: ROCKET-mult x{_rm}")
                                                position_size *= _rm
                                                _meme = await analyzer.meme_tag(mint, actual_symbol, pair_data, "solana")
                                                if _meme:
                                                    analyzer.signals[mint] = analyzer.get_signal(mint) + _meme
                                            except Exception:
                                                pass
                                        position_size = min(position_size, 100.0)

                                        # VIP-ШИП = РАЗДАЧА (доказано метками: UNPEG/FIBONACCI/BackCat/Goblin).
                                        _is_vipsig = "VIP-" in analyzer.get_signal(mint)
                                        if _is_vipsig:
                                            if not getattr(config, "VIP_ENTRIES_ENABLED", False):
                                                print(f"🚫 [VIP-BLOCKED] {actual_symbol}: VIP входы отключены в config.py.")
                                                continue
                                            _vip_cap = float(getattr(config, "TG_VIP_SIZE_USD", 5.0))
                                            position_size = min(position_size, _vip_cap)

                                _min_ticket = 1.0 if (_is_lot or _is_vipsig or fixed > 0) else float(getattr(config, "BASE_TRADE_AMOUNT_USD", 2.0))
                                if position_size < _min_ticket:
                                    print(f"🚫 Отказ (Сайз): Размер входа (${position_size:.2f}) меньше минимального билета (${_min_ticket:.2f}).")
                                    continue

                                # Защита от переразгона: в рынке не больше 60% капитала
                                _deployed = sum(p.amount_usd for p in tracker.get_open_positions().values())
                                _cap = tracker.get_total_capital() * getattr(config, "MAX_DEPLOYED_PCT", 0.60)
                                if _deployed + position_size > _cap:
                                    print(f"🚫 Отказ (Exposure): занято ${_deployed:.0f} + ${position_size:.0f} > лимит ${_cap:.0f}.")
                                    continue

                                _pc = pair_data.get("priceChange") or {}
                                _tx5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
                                _final_sc_conf = float(getattr(analyzer, "rocket_scores", {}).get(mint, getattr(analyzer, "last_score", 0.0)) or 0.0)

                                # PRE-FLIGHT PARALLEL PIPELINE: sellability + ончейн/Order-Flow + GoPlus + PumpSwap + RugWallet + FCR выполняются ОДНОВРЕМЕННО (~3-5с вместо ~14с)
                                _t_pf0 = time.time()
                                _dex_lower_sc = str(pair_data.get("dexId") or "").lower()
                                _fcr_feat = {}
                                _of_meta = {}

                                try:
                                    from jupiter import JupiterAPI
                                    import market_data as _md_pf
                                    import goplus as _gp_pf

                                    async def _pf_sellable():
                                        try:
                                            return await JupiterAPI.is_token_sellable(mint)
                                        except Exception:
                                            return True, "sell-check-err"

                                    async def _pf_orderflow():
                                        try:
                                            return await _md_pf.verify_entry_onchain_and_orderflow(
                                                mint, actual_symbol, pair_data, entry_price
                                            )
                                        except Exception:
                                            return True, entry_price, "", {}

                                    async def _pf_goplus():
                                        try:
                                            return await _gp_pf.check_solana(mint)
                                        except Exception:
                                            return True, ""

                                    async def _pf_pumpswap():
                                        if _dex_lower_sc != "pumpswap":
                                            return None
                                        try:
                                            return await _gp_pf.verify_pumpswap_pool_onchain(
                                                mint,
                                                pair_address=str(pair_data.get("pairAddress") or ""),
                                                pair_data=pair_data,
                                            )
                                        except Exception:
                                            return None

                                    async def _pf_rugwallet():
                                        if not getattr(config, "RUG_WALLET_AUDIT_ENABLED", True):
                                            return None
                                        try:
                                            return await quick_rug_check(mint)
                                        except Exception as _e:
                                            return _e

                                    async def _pf_fcr():
                                        if not getattr(config, "FCR_ENABLED", True):
                                            return None
                                        try:
                                            from first_candle_rug_guard import pre_entry_rug_check
                                            return await pre_entry_rug_check(mint, pair_data=pair_data, symbol=actual_symbol)
                                        except Exception as _e:
                                            return _e

                                    _r_sell, _r_of, _r_gp, _r_ps, _r_rw, _r_fcr = await asyncio.gather(
                                        _pf_sellable(),
                                        _pf_orderflow(),
                                        _pf_goplus(),
                                        _pf_pumpswap(),
                                        _pf_rugwallet(),
                                        _pf_fcr(),
                                    )

                                    if isinstance(_r_sell, tuple):
                                        _j_ok, _j_why = _r_sell
                                        _w_low = str(_j_why or "").lower()
                                        if not _j_ok and any(k in _w_low for k in ("no routes", "cannot sell", "honeypot", "freeze", "0 sol returned")):
                                            print(f"🚫 [SCANNER PRE-FLIGHT BLOCKED] {actual_symbol} ({mint[:8]}): токен непродаваем на DEX ({_j_why}) — предотвращен вход!")
                                            continue

                                    if isinstance(_r_of, tuple) and len(_r_of) == 4:
                                        _of_ok, _of_px, _of_why, _of_meta = _r_of
                                        if not _of_ok:
                                            _rh_mode_tmp = str(pair_data.get("_rug_hunter_mode") or "").upper().strip()
                                            _is_rh_cand_tmp = bool(_rh_mode_tmp or ("RUG-HUNTER" in str(analyzer.get_signal(mint) or "").upper()))
                                            if _is_rh_cand_tmp and "serial-rugger" in str(_of_why):
                                                print(f"ℹ️ [SCANNER-RH-SERIAL-ALLOW] {actual_symbol} ({mint[:8]}): серийный дев допущен под Rug-Hunter (ончейн-тайминг)")
                                            else:
                                                print(f"🚫 [SCANNER ONCHAIN/FLOW] {actual_symbol} ({mint[:8]}): {_of_why} — вход заблокирован!")
                                                continue
                                        if _of_px and _of_px > 0:
                                            entry_price = _of_px
                                            if _wick_stop_price and _wick_pct:
                                                _wick_stop_price = entry_price * (1.0 + _wick_pct)

                                    if isinstance(_r_gp, tuple):
                                        _g_ok, _g_why = _r_gp
                                        _gw_low = str(_g_why or "").lower()
                                        if not _g_ok and any(k in _gw_low for k in ("honeypot", "cannot_sell", "freeze", "freezable", "closable", "balance_mutable", "frozen", "non_transferable", "transfer_hook", "default_account")):
                                            print(f"🚫 [SCANNER PRE-FLIGHT GOPLUS] {actual_symbol} ({mint[:8]}): {_g_why} — вход заблокирован!")
                                            continue

                                    _ca_sc_pre = float(pair_data.get("pairCreatedAt") or 0)
                                    _age_sc_pre = ((time.time() * 1000.0 - _ca_sc_pre) / 60000.0) if _ca_sc_pre > 0 else 0.0
                                    _rh_mode_sc = str(pair_data.get("_rug_hunter_mode") or "").upper().strip()
                                    _rh_sc_max_age = float(getattr(config, "RUG_HUNTER_UNLOCKED_MAX_AGE_MIN", 3.20)) + 0.5
                                    if _rh_mode_sc and _age_sc_pre > _rh_sc_max_age:
                                        _rh_mode_sc = ""
                                        pair_data.pop("_rug_hunter_mode", None)
                                        pair_data.pop("_unlocked_runner_ok", None)
                                    _is_rh_sc_pf = bool(_rh_mode_sc) or ("RUG-HUNTER" in str(analyzer.get_signal(mint) or "").upper())
                                    _is_rh_unl_sc = bool(_is_rh_sc_pf and _rh_mode_sc in ("UNLOCKED-LP", "SEEDED-LP") and getattr(config, "RUG_HUNTER_ENABLED", False) and getattr(config, "RUG_HUNTER_UNLOCKED_LP_ENABLED", False))
                                    if _is_rh_sc_pf:
                                        import rug_hunter as _rh_pf_sl
                                        if not _rh_pf_sl.has_rug_hunter_slot(tracker):
                                            print(f"⏳ [SCANNER-RH-SLOTS] {actual_symbol} ({mint[:8]}): лимит слотов Rug-Hunter заполнен — отмена!")
                                            continue

                                    # PRE-FLIGHT PUMPSWAP ON-CHAIN GUARD: защита от permissionless CreatePool дрейнов (включая .pump/.bonk/.BAGS)
                                    if _dex_lower_sc == "pumpswap" and isinstance(_r_ps, tuple):
                                        _ps_m_ok, _ps_m_canon, _ps_m_why = _r_ps
                                        _is_rh_seeded_sc = bool(_is_rh_unl_sc and getattr(config, "RUG_HUNTER_SEEDED_LP_ENABLED", True))
                                        if (not _ps_m_ok and not (_is_rh_unl_sc and "createpool-unburned-lp" in str(_ps_m_why or "").lower())) or (config.is_pumpswap_liq_fdv_trap(pair_data) and not _is_rh_seeded_sc):
                                            print(f"🚫 [SCANNER PRE-FLIGHT PUMPSWAP] {actual_symbol} ({mint[:8]}): {_ps_m_why} — вход заблокирован!")
                                            continue
                                        _ps_rpc_safe_m = bool(pair_data.get("_pumpswap_rpc_unverified")) and mint.lower().endswith("pump")
                                        if not _ps_m_canon and not bool(pair_data.get("_pumpswap_lp_burned")) and not _ps_rpc_safe_m and not _is_rh_unl_sc:
                                            _ca_ps = float(pair_data.get("pairCreatedAt") or 0)
                                            _age_ps = ((time.time() * 1000.0 - _ca_ps) / 60000.0) if _ca_ps > 0 else 0.1
                                            _fdv_ps = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                            _m5_ps = _pc.get("m5")
                                            if not config.is_safe_unlocked_custom_runner(
                                                liq_usd=liq_usd,
                                                age_min=_age_ps,
                                                vol5_usd=float(((pair_data.get("volume") or {}).get("m5", 0) or 0)),
                                                buys5=int(_tx5.get("buys", 0) or 0),
                                                sells5=int(_tx5.get("sells", 0) or 0),
                                                fdv_usd=_fdv_ps,
                                                m5_pct=(float(_m5_ps) if _m5_ps is not None else None),
                                                pair_data=pair_data,
                                            ):
                                                print(f"🚫 [SCANNER PRE-FLIGHT PUMPSWAP-CREATEPOOL] {actual_symbol} ({mint[:8]}): кастомный незалоченный пул ({_ps_m_why}) не прошёл фильтр liq/fdv/оборота — блок!")
                                                continue

                                    # PRE-FLIGHT GUARD ДЛЯ ВСЕХ НЕЗАЛОЧЕННЫХ ПУЛОВ (Meteora DYN2, PumpSwap CreatePool, кастомный Raydium без сожжённого LP)
                                    _is_native_pump_sc = mint.lower().endswith("pump") and _dex_lower_sc in ("", "pumpfun", "pump", "pumpswap")
                                    _is_lp_safe_sc = bool(pair_data.get("_lp_verified")) or bool(pair_data.get("_pumpswap_canonical")) or bool(pair_data.get("_pumpswap_lp_burned"))
                                    
                                    # СТРОГАЯ ЗАЩИТА: незалоченный пул БЕЗ активного режима Rug-Hunter КАТЕГОРИЧЕСКИ ЗАПРЕЩЁН к покупке в SCANNER!
                                    # Исключает утечку незалоченных пулов в регулярный $3 ордер без защитных эвакуаций (кейс Cyberleek -$3.02).
                                    if not _is_native_pump_sc and not _is_lp_safe_sc and not _is_rh_unl_sc:
                                        print(f"🚫 [SCANNER PRE-FLIGHT LP-SAFETY] {actual_symbol} ({mint[:8]}): незалоченный пул {_dex_lower_sc} без активного режима Rug-Hunter — вход в основную стратегию строго запрещён!")
                                        continue
                                    if not _is_native_pump_sc and not _is_lp_safe_sc and not _is_rh_unl_sc:
                                        _ca_sc = float(pair_data.get("pairCreatedAt") or 0)
                                        _age_sc = ((time.time() * 1000.0 - _ca_sc) / 60000.0) if _ca_sc > 0 else 0.1
                                        _fdv_sc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                        _m5_sc = _pc.get("m5")
                                        _info_sc = pair_data.get("info") or {}
                                        _has_soc_sc = bool((_info_sc.get("socials") or []) or (_info_sc.get("websites") or []))
                                        if not config.is_safe_unlocked_custom_runner(
                                            liq_usd=liq_usd,
                                            age_min=_age_sc,
                                            vol5_usd=float(((pair_data.get("volume") or {}).get("m5", 0) or 0)),
                                            buys5=int(_tx5.get("buys", 0) or 0),
                                            sells5=int(_tx5.get("sells", 0) or 0),
                                            has_socials=_has_soc_sc,
                                            is_trap=config.is_pumpswap_liq_fdv_trap(pair_data),
                                            fdv_usd=_fdv_sc,
                                            m5_pct=(float(_m5_sc) if _m5_sc is not None else None),
                                            pair_data=pair_data,
                                        ):
                                            print(f"🚫 [SCANNER PRE-FLIGHT UNLOCKED-POOL] {actual_symbol} ({mint[:8]}): незалоченный пул {_dex_lower_sc} (age {_age_sc:.1f}м, liq ${liq_usd:,.0f}, soc={_has_soc_sc}) не прошёл аудит анти-рага — блок!")
                                            continue

                                    # PRE-FLIGHT RUG WALLET AUDIT: анализ кошельков дева, authorities, холдеров, бандлов
                                    if isinstance(_r_rw, Exception):
                                        print(f"⚠️ [SCANNER PRE-FLIGHT RUG-WALLET] {actual_symbol} ({mint[:8]}): audit error {type(_r_rw).__name__} — вход разрешён (fail-open).")
                                    elif isinstance(_r_rw, tuple):
                                        _rw_safe, _rw_why = _r_rw
                                        if not _rw_safe:
                                            _why_low = str(_rw_why or "").lower()
                                            _has_hard_auth_rug = any(k in _why_low for k in ("mint_authority_enabled", "freeze_authority_enabled", "honeypot"))
                                            _only_rpc_errors = all(k in _why_low for k in ("no-mint-account", "no-holder-data")) or "rpc" in _why_low or "timeout" in _why_low
                                            if _only_rpc_errors and not _has_hard_auth_rug and not any(k in _why_low for k in ("top1_pct_high", "sybil", "fresh bot")):
                                                print(f"⚠️ [SCANNER PRE-FLIGHT RUG-WALLET] {actual_symbol} ({mint[:8]}): RPC 429/timeout ({_rw_why}) — вход разрешён (fail-open).")
                                            elif _is_rh_sc_pf and not _has_hard_auth_rug:
                                                print(f"🎯 [SCANNER-RH-WALLET-PASS] {actual_symbol} ({mint[:8]}): {_rw_why} — контракт чист, ведём в режиме RUG-HUNTER с лайв-монитором.")
                                            else:
                                                print(f"🚫 [SCANNER PRE-FLIGHT RUG-WALLET] {actual_symbol} ({mint[:8]}): {_rw_why} — вход заблокирован аудитом кошельков!")
                                                continue

                                    # PRE-FLIGHT FIRST-CANDLE RUG GUARD
                                    if isinstance(_r_fcr, Exception):
                                        print(f"⚠️ [SCANNER PRE-FLIGHT FCR] {actual_symbol} ({mint[:8]}): error {type(_r_fcr).__name__} — вход разрешён (fail-open).")
                                    elif isinstance(_r_fcr, dict):
                                        from first_candle_rug_guard import compact_summary
                                        _fcr_compact = compact_summary(_r_fcr)
                                        if _r_fcr.get("verdict") == "BLOCK":
                                            print(f"🚫 [SCANNER PRE-FLIGHT FCR] {actual_symbol} ({mint[:8]}): {_r_fcr.get('report', '')} — вход заблокирован первоочередно по мгновенному рагу!")
                                            continue
                                        _fcr_score_sc = float((_fcr_compact or {}).get("fcr_score", 0.0) or _r_fcr.get("score", 0.0) or 0.0)
                                        _fcr_rsn_sc = str((_fcr_compact or {}).get("fcr_reasons", "") or "").lower()
                                        if _is_curve_live and "дистрибуция" in _fcr_rsn_sc:
                                            print(f"🚫 [SCANNER FCR-CURVE-DIST] {actual_symbol} ({mint[:8]}): дистрибуция на сырой кривой (кейсы AFTERGTA6/PPV) — отмена входа!")
                                            continue
                                        if _r_fcr.get("verdict") == "WARN":
                                            _fcr_w_n = int((_fcr_compact or {}).get("fcr_cluster_wallets", 0) or 0)
                                            _fcr_m5 = float((pair_data.get("priceChange") or {}).get("m5", 0) or 0)
                                            _fcr_h1 = float((pair_data.get("priceChange") or {}).get("h1", 0) or 0)
                                            if _fcr_score_sc >= 0.55:
                                                print(f"🚫 [SCANNER FCR-HIGH-SCORE] {actual_symbol} ({mint[:8]}): FCR score {_fcr_score_sc:.2f} >= 0.55 (кейс BNC 0.721) — отмена входа!")
                                                continue
                                            if _fcr_h1 < -15.0:
                                                print(f"🚫 [SCANNER FCR-RED-H1] {actual_symbol} ({mint[:8]}): FCR WARN на падающем часовом тренде h1 {_fcr_h1:+.1f}% (кейсы DACI/CASHPARROT) — отмена входа!")
                                                continue
                                            _is_dist_fcr_sc = config.is_distributed_safe_runner(
                                                liq_usd, _age_m, _vol5_live, _b5_live, _s5_live, is_lp_safe=True
                                            )
                                            if _fcr_w_n >= 5 and not _is_dist_fcr_sc and not _is_rh_sc_pf:
                                                print(f"🚫 [SCANNER FCR-FARM-BLOCK] {actual_symbol} ({mint[:8]}): ферма из {_fcr_w_n} кошельков одного источника (кейс GTA6 -14%) — отмена входа!")
                                                continue
                                            if _fcr_w_n >= 3 and _fcr_m5 > 120.0 and not _is_dist_fcr_sc and not _is_rh_sc_pf:
                                                print(f"🚫 [SCANNER FCR-INSIDER-BLOCK] {actual_symbol} ({mint[:8]}): инсайдерская сеть {_fcr_w_n} кошельков на свече m5 {_fcr_m5:+.1f}% — отмена входа (кейс CHUD -50%)!")
                                                continue
                                            print(f"⚠️ [SCANNER PRE-FLIGHT FCR] {actual_symbol} ({mint[:8]}): {_r_fcr.get('report', '')}")
                                        _fcr_feat = dict(_fcr_compact or {})

                                    # POST-FCR LIVE SANITY CHECK: если аудит занял > 1с, перепроверяем актуальную цену/резерв перед открытием сделки
                                    if (time.time() - _t_pf0) > 1.0 and entry_price > 0:
                                        _re_px = 0.0
                                        if _dex_lower_sc in ("pump", "pumpfun"):
                                            _cs2 = await _md_pf.get_live_pump_curve_state(mint)
                                            if _cs2 and not _cs2.get("complete"):
                                                if float(_cs2.get("real_sol", 0) or 0) < 1.8:
                                                    print(f"🚫 [POST-FCR CURVE-EMPTY] {actual_symbol} ({mint[:8]}): за время аудита кёрв слился ниже 1.8 SOL — отмена!")
                                                    continue
                                                _re_px = float(_cs2.get("price_usd", 0) or 0)
                                        elif _dex_lower_sc == "pumpswap":
                                            _ps2 = await _md_pf.get_live_pumpswap_pool_state(mint, str(pair_data.get("pairAddress") or ""))
                                            if _ps2:
                                                if float(_ps2.get("liq_usd", 0) or 0) < float(getattr(config, "MIN_GRADUATED_LIQ_USD", 3000.0)) * 0.5:
                                                    print(f"🚫 [POST-FCR PUMPSWAP-DRAIN] {actual_symbol} ({mint[:8]}): за время аудита пул обсох (${float(_ps2.get('liq_usd', 0) or 0):,.0f}) — отмена!")
                                                    continue
                                                _re_px = float(_ps2.get("price_usd", 0) or 0)
                                        else:
                                            _re_px = float(await JupiterAPI.get_price(mint) or 0.0)
                                        _pf_dump_floor = 0.88 if (_dex_lower_sc == "pumpswap" and mint.lower().endswith("pump")) else 0.92
                                        if _re_px > 0 and _re_px < entry_price * _pf_dump_floor:
                                            print(f"🚫 [POST-FCR DUMP] {actual_symbol} ({mint[:8]}): за время аудита цена упала на {(1.0 - _re_px / entry_price) * 100:.1f}% — отмена!")
                                            continue
                                        if _re_px > 0 and _re_px <= entry_price * 1.40:
                                            entry_price = _re_px
                                            if _wick_stop_price and _wick_pct:
                                                _wick_stop_price = entry_price * (1.0 + _wick_pct)
                                except Exception as _pf_err:
                                    print(f"⚠️ [SCANNER PRE-FLIGHT] {actual_symbol} ({mint[:8]}): {type(_pf_err).__name__}")

                                _of_feat = {"dex": _dex_lower_sc}
                                if isinstance(_of_meta, dict):
                                    if _of_meta.get("wash_ratio"):
                                        _of_feat["wash_ratio"] = _of_meta["wash_ratio"]
                                    if _of_meta.get("smart_buyers"):
                                        _of_feat["smart_tape_n"] = len(_of_meta["smart_buyers"])
                                        print(f"🧠 [SMART-MONEY-TAPE] {actual_symbol} ({mint[:8]}): зафиксирован вход {len(_of_meta['smart_buyers'])} топ-кошельков в живой ленте!")
                                    if _of_meta.get("onchain_source"):
                                        _of_feat["onchain_source"] = _of_meta["onchain_source"]

                                _sig_sc_fin = analyzer.get_signal(mint)
                                _rh_mode_buy = str(pair_data.get("_rug_hunter_mode") or "").strip()
                                if _rh_mode_buy and "RUG-HUNTER" not in str(_sig_sc_fin or "").upper():
                                    import rug_hunter as _rh_sb
                                    _sig_sc_fin = _rh_sb.format_rug_hunter_source(_rh_mode_buy, _final_sc_conf, pair_data.get("_rug_hunter_creator_profile"))
                                _is_rh_buy_sc = bool(_rh_mode_buy) or ("RUG-HUNTER" in str(_sig_sc_fin or "").upper())

                                # Финальный предохранитель: незалоченный пул НЕ МОЖЕТ быть куплен как обычный SCANNER!
                                if not _is_native_pump_sc and not _is_lp_safe_sc and not _is_rh_buy_sc:
                                    print(f"🚫 [SCANNER-SAFETY] {actual_symbol} ({mint[:8]}): попытка купить незалоченный пул как обычный SCANNER — вход заблокирован!")
                                    continue

                                if _is_rh_buy_sc:
                                    _rh_mult = float(getattr(config, "RUG_HUNTER_SIZE_MULT", 0.65))
                                    if "SEEDED" in str(_rh_mode_buy or "").upper():
                                        _rh_mult = min(_rh_mult, float(getattr(config, "RUG_HUNTER_SEEDED_SIZE_MULT", 0.40)))
                                    position_size = max(1.0, position_size * _rh_mult)
                                    _src_sc_final = _sig_sc_fin
                                else:
                                    _src_sc_final = f"SCANNER:{_sig_sc_fin}"

                                _bought = tracker.add_position(actual_symbol, mint, entry_price, position_size, is_mature=True,
                                                     ml_features={**analyzer.pack_features(pair_data), **_fcr_feat, **_of_feat},
                                                     ml_confidence=_final_sc_conf,
                                                     source=_src_sc_final,
                                                     pair_address=pair_data.get("pairAddress", ""),
                                                     stop_under_wick=_wick_stop_price,
                                                     wick_stop_loss_pct=_wick_pct,
                                                     initial_liquidity_usd=liq_usd,
                                                     pair_data=pair_data)
                                if not _bought:
                                    print(f"🚫 [SCANNER-FIREWALL] {actual_symbol} ({mint[:8]}): покупка отклонена финальным фильтром tracker.")
                                    continue
                                analyzer.log_scan(actual_symbol, mint, "SCANNER", True,
                                                  _final_sc_conf,
                                                  liq_usd, _pc.get("m5", 0) or 0,
                                                  _tx5.get("buys", 0) or 0, _tx5.get("sells", 0) or 0)
                                print(f"🚀 [BUY-EXECUTED] {actual_symbol} ({mint[:8]}) @ ${entry_price:.8f} [${position_size:.0f}] | ML {_final_sc_conf:.1f}%")
                                # Инициализируем мониторинг кошельков для новой позиции
                                try:
                                    init_position_wallet_state(mint, time.time())
                                except Exception:
                                    pass
                                grok_jev.FUNNEL_STATS["filled"] += 1
                                print(grok_jev.get_funnel_summary())
                                _entered = True
                                break # Ждем следующего цикла после покупки
                            else:
                                print(f"⚠️ Ошибка: Не удалось получить цену для {mint} (entry_price=0). Возможно, Rate Limit (429).")
                        
                        # Микро-пауза для yield event loop (было 1.5с, что создавало задержку 2.5 мин на цикл)
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        print(f"⚠️ Ошибка анализа {mint[:8]}: {type(e).__name__}: {e}. Сканирую дальше.")
                    finally:
                        if _claimed_buy_flight:
                            analyzer.release_buy_flight(mint)
            else:
                # Диагностика "тишины": сканер вообще не работает, пока вход закрыт
                _now_i = time.time()
                if _now_i - _last_idle_log >= 60:
                    _last_idle_log = _now_i
                    print(f"⏸ [SCANNER] вход закрыт: открыто {open_count}/{config.MAX_CONCURRENT_POSITIONS}, "
                          f"trading_allowed={tracker.is_trading_allowed()} (лимит позиций / кэш / streak-пауза) — "
                          f"сканирование приостановлено до освобождения слота.")
        except Exception as e:
            print(f"Ошибка в цикле сканера: {e}")
        finally:
            if _scan_win is not None:
                await _scan_win.close()  # отменяем недоработанное (после входа/ошибки)
        if _n_analyzed:
            print(f"⏱ [SCANNER] цикл {time.time() - _cycle_t0:.1f}с: в очереди {_launch_n}, разобрано {_n_analyzed}"
                  f"{', ВХОД' if _entered else ''} (параллель x{int(getattr(config, 'SCAN_CONCURRENCY', 4))}).")
        await asyncio.sleep(float(getattr(config, "SCAN_AFTER_ENTRY_SLEEP_SEC", 15) if _entered
                                  else getattr(config, "SCAN_LOOP_SLEEP_SEC", 8)))

from copytrader import CopyTrader
import os


def parse_signal_line(raw: str):
    """Разбор строки очереди: sol:<mint>[@канал] | evm:[chain:]<addr>[@канал] |
    голый mint/0x... (канала нет). Возвращает (kind, mint, channel).
    kind: 'sol' | 'evm' | 'evm:<chain>'. Тег @канал ставит tg_preview
    (видно в дашборде как TG-SIGNAL:канал:...)."""
    raw = (raw or "").strip()
    channel = ""
    if "@" in raw:
        raw, _, channel = raw.rpartition("@")
        raw, channel = raw.strip(), channel.strip()
    if raw.startswith("evm:"):
        rest = raw[4:].strip()
        if ":" in rest:
            _chn, mint = rest.split(":", 1)
            return f"evm:{_chn.strip().lower()}", mint.strip(), channel
        return "evm", rest, channel
    if raw.startswith("sol:"):
        return "sol", raw[4:].strip(), channel
    if raw.startswith("0x"):
        return "evm", raw, channel
    return "sol", raw, channel


async def fomo_signal_loop(analyzer, tracker):
    if not getattr(config, "USE_FOMO_SIGNALS", False):
        print("📲 FOMO-сигналы ВЫКЛ (USE_FOMO_SIGNALS=False): внешние пампы не покупаем.")
        return
    print("📲 Запуск обработчика сигналов FOMO...")
    while True:
        try:
            if os.path.exists('fomo_signals.txt'):
                with open('fomo_signals.txt', 'r') as f:
                    mints = f.read().splitlines()
                
                if mints:
                    # Удаляем файл после прочтения (не оставляем 0-байтный пустой файл)
                    try:
                        os.remove('fomo_signals.txt')
                    except OSError:
                        pass
                    # Бёрст-контроль и дедупликация: очередь могла копиться пока петля спала —
                    # очищаем дубликаты, сохраняя самые свежие сигналы
                    _maxpass = int(getattr(config, "MAX_FOMO_BUYS_PER_PASS", 3) or 3)
                    _seen_fomo = set()
                    _deduped_fomo = []
                    for m in reversed(mints):
                        m_clean = str(m or "").strip()
                        if m_clean and m_clean not in _seen_fomo:
                            _seen_fomo.add(m_clean)
                            _deduped_fomo.append(m_clean)
                    mints = list(reversed(_deduped_fomo))[-25:]
                    _buys = 0

                    for raw in mints:
                        if _evm_killed(tracker):
                            print("🛑 FOMO KILL-SWITCH: дневной лимит убытка исчерпан, внешний сигнал отклонён.")
                            continue
                        kind, mint, _src_ch = parse_signal_line(raw)
                        if getattr(config, "SOLANA_ONLY", True):
                            if kind != "sol" or mint.startswith("0x"):
                                print(f"🚫 [SOLANA-ONLY] Внешний не-Solana сигнал {mint[:10]} отклонён.")
                                continue
                        if not mint or mint in tracker.positions:
                            continue
                        _tag = f"TG:{_src_ch} " if _src_ch else ""
                        print(f"🚨 ПРИНЯТ ВНЕШНИЙ СИГНАЛ ({_tag}FOMO): {mint}")
                        if kind == "evm" or kind.startswith("evm:"):
                            # EVM: сеть либо указана (evm:robinhood:0x...), либо перебором
                            import evm_data
                            found = None
                            if ":" in kind:
                                _want = kind.split(":", 1)[1]
                                try:
                                    _td = await evm_data.get_token_data(mint, _want)
                                except Exception:
                                    _td = {}
                                if _td and float(_td.get("priceUsd", 0) or 0) > 0:
                                    found = (_want, _td)
                            if not found:
                                    for _ch in ("base", "bsc", "robinhood", "ethereum"):
                                        try:
                                            _td = await evm_data.get_token_data(mint, _ch)
                                        except Exception:
                                            _td = {}
                                        if _td and float(_td.get("priceUsd", 0) or 0) > 0:
                                            found = (_ch, _td)
                                            break
                            if not found:
                                print(f"📲 TG EVM {mint[:10]}: ни в одной сети не найден.")
                                continue
                            _ch, _td = found
                            if _ch == "ethereum" and not getattr(config, "ETHEREUM_ENTRIES_ENABLED", False):
                                # L1-входы выкл: газ $2-6 съедает скальп (SEND/AP брались именно здесь).
                                print(f"🚫 TG EVM {mint[:10]}: сеть ethereum, входы выкл — скип.")
                                continue
                            ok = await analyzer.analyze_robinhood_token(mint, _ch)
                            if ok is True and mint not in tracker.positions:
                                # Тот же комплект гейтов, что у сканера: RKT-вето +
                                # GoPlus/honeypot.is (иначе TG-ветка их обходила).
                                _esig = analyzer.get_signal(mint)
                                _eveto = analyzer.rocket_veto(mint, _td, _esig)
                                if _eveto:
                                    print(f"🚫 TG EVM {mint[:10]}: {_eveto}")
                                    continue
                                try:
                                    import goplus as _tgp
                                    _v_ok, _v_why = await _tgp.verify_evm_token(mint, _ch, _td)
                                    if not _v_ok:
                                        print(f"🚫 TG EVM {mint[:10]}: {_v_why} — блок.")
                                        continue
                                except Exception as _tge:
                                    if _ch == "bsc":
                                        print(f"🚫 TG BSC {mint[:10]}: ошибка антискама ({type(_tge).__name__}) — блок.")
                                        continue
                                price = float(_td.get("priceUsd", 0))
                                sym = ((_td.get("baseToken") or {}).get("symbol", "TG") or "TG")
                                cap = tracker.get_total_capital()
                                _fixed_sz = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                                size = _fixed_sz if _fixed_sz > 0 else (max(4.0, min(100.0, cap * (config.REINVEST_PERCENT / 100.0))) if cap > 0 else 4.0)
                                _tg_cap = float(getattr(config, "TG_MAX_SIZE_USD", 5.0) or 5.0)
                                size = min(size, _tg_cap)
                                if "VIP-" in str(_esig):
                                    size = min(size, float(getattr(config, "TG_VIP_SIZE_USD", 5.0) or 5.0))
                                if _ch == "robinhood":
                                    size = min(size, float(getattr(config, "ROB_MAX_SIZE_USD", 15.0) or 15.0))
                                if _ch == "bsc":
                                    _bsc_cap = float(getattr(config, "BSC_MAX_SIZE_USD", 15.0) or 15.0)
                                    size = min(size, _bsc_cap)
                                _tg_src = f"TG-SIGNAL:{_src_ch}:{_esig}" if _src_ch else f"TG-SIGNAL:{_esig}"
                                tracker.add_position(sym, mint, price, size, is_mature=True,
                                                     source=_tg_src, chain=_ch,
                                                     pair_address=_td.get("pairAddress", ""))
                                print(f"📲 TG-SIGNAL BUY {sym} [{_ch}] @ ${price}")
                                _buys += 1
                                if _buys >= _maxpass:
                                    break
                            continue
                        # Проверяем скам-фильтрами перед покупкой
                        is_good = await analyzer.analyze_token(mint)

                        if is_good:
                            pair_data = await analyzer.fetch_token_data(mint)
                            entry_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0
                            actual_symbol = pair_data.get("baseToken", {}).get("symbol", "FOMO") if pair_data else "FOMO"

                            if entry_price > 0:
                                from jupiter import JupiterAPI
                                _jup_chk = await JupiterAPI.get_price(mint)
                                if _jup_chk and _jup_chk > 0:
                                    if _jup_chk < entry_price * 0.90:
                                        print(f"🚫 [TG-LIVE-DUMP] {actual_symbol} ({mint[:8]}): живая цена Jupiter (${_jup_chk:.8f}) уже на {(1.0 - _jup_chk / entry_price) * 100:.1f}% ниже DexScreener (${entry_price:.8f}) [>10%] — обвал в моменте, блок!")
                                        continue
                                    if max(_jup_chk, entry_price) / min(_jup_chk, entry_price) > 1.25:
                                        continue
                                    entry_price = _jup_chk
                                _ssig = analyzer.get_signal(mint)
                                _sveto = analyzer.rocket_veto(mint, pair_data, _ssig)
                                if _sveto:
                                    print(f"🚫 TG SOL {mint[:10]}: {_sveto}")
                                    continue
                                capital = tracker.get_total_capital()
                                _fixed_sz = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                                position_size = _fixed_sz if _fixed_sz > 0 else max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0)))
                                _tg_cap = float(getattr(config, "TG_MAX_SIZE_USD", 5.0) or 5.0)
                                position_size = min(position_size, _tg_cap)
                                if "VIP-" in str(_ssig):
                                    position_size = min(position_size, float(getattr(config, "TG_VIP_SIZE_USD", 5.0) or 5.0))
                                _tg_src = f"TG-SIGNAL:{_src_ch}:{_ssig}" if _src_ch else f"TG-SIGNAL:{_ssig}"
                                tracker.add_position(actual_symbol, mint, entry_price, position_size,
                                                     source=_tg_src,
                                                     pair_address=(pair_data.get("pairAddress", "") if pair_data else ""))
                                _buys += 1
                                if _buys >= _maxpass:
                                    break
        except Exception as e:
            print(f"Ошибка в fomo_signal_loop: {e}")
        await asyncio.sleep(1) # Проверяем файл каждую секунду для мгновенной реакции

async def growth_loop(analyzer, tracker):
    """🌱 GROWTH MODE: тренд зрелых капов, пока нет ракет.
    Вход в откат H1-тренда, выходы медленные: стоп -10%, трейлинг +8%/12%, стагнант 2ч."""
    growth_enabled = getattr(config, "GROWTH_ENABLED", False)
    if not growth_enabled:
        mine_check = {m: p for m, p in tracker.get_open_positions().items()
                      if str(getattr(p, "source", "")).startswith("GROWTH")}
        if not mine_check:
            print("🌱 Growth Loop отключён (новых входов нет).")
            return
    interval = getattr(config, "GROWTH_INTERVAL", 300)
    while True:
        try:
            import time as _t
            _killed = _evm_killed(tracker)
            if _killed:
                print("🛑 GROWTH KILL-SWITCH: входы на паузе 5мин (выходы работают).")
            mine = {m: p for m, p in tracker.get_open_positions().items()
                    if str(getattr(p, "source", "")).startswith("GROWTH")}
            # --- трекинг ---
            if mine:
                from jupiter import JupiterAPI
                px = await JupiterAPI.get_prices(list(mine.keys()))
                for mint, pos in list(mine.items()):
                    cur = px.get(mint, 0) or pos.current_price_usd or pos.entry_price_usd
                    # Фантом цены (кейс RAY: пик +1624120% с битого тика повалил
                    # трейлинг). x50 за тик — не пик, а битые данные.
                    try:
                        if pos.entry_price_usd > 0 and cur / pos.entry_price_usd > 50:
                            print(f"⚠️ GROWTH phantom {mint[:8]}: x{cur / pos.entry_price_usd:.0f} за тик — игнор.")
                            pos.price_checked_at = _t.time()
                            continue
                    except Exception:
                        pass
                    if cur > pos.max_price_usd:
                        pos.max_price_usd = cur
                    prev = pos.current_price_usd
                    pos.current_price_usd = cur
                    if not pos.entry_price_usd:
                        continue
                    pnl = (cur - pos.entry_price_usd) / pos.entry_price_usd
                    maxp = (pos.max_price_usd - pos.entry_price_usd) / pos.entry_price_usd
                    pos.current_pnl_usd = pos.amount_usd * pnl
                    held = (_t.time() - pos.entry_time) / 60
                    reason = None
                    prev_ts = getattr(pos, "price_checked_at", 0.0)
                    _g_floor = None
                    if maxp >= 0.50:
                        _g_floor = 0.30
                    elif maxp >= 0.25:
                        _g_floor = 0.12
                    elif maxp >= 0.15:
                        _g_floor = 0.05

                    if prev_ts and (_t.time() - prev_ts) < 300 and prev > 0 and cur <= prev * 0.80:
                        reason = f"GROWTH Crash ({(1 - cur / prev) * 100:.0f}%)"
                    elif _g_floor is not None and pnl <= _g_floor:
                        reason = f"GROWTH Profit Floor (+{_g_floor * 100:.0f}% locked after peak +{maxp * 100:.0f}%)"
                    elif maxp >= getattr(config, "GROWTH_TRAIL_ACT", 0.08) and \
                            (pos.max_price_usd - cur) / pos.max_price_usd >= getattr(config, "GROWTH_TRAIL_DIST", 0.12):
                        reason = f"GROWTH Trailing (peak +{maxp * 100:.0f}%)"
                    elif pnl <= getattr(config, "GROWTH_STOP_PCT", -0.10):
                        reason = f"GROWTH Stop ({pnl * 100:.1f}%)"
                    elif held >= getattr(config, "GROWTH_STAGNANT_MIN", 120) and pnl < 0.05:
                        reason = f"GROWTH Stagnant ({held:.0f}m)"
                    pos.price_checked_at = _t.time()
                    pos.price_updated_at = _t.time()
                    if reason:
                        tracker.close_position(mint, cur, reason)
                tracker.save_portfolio()
            # --- скан вселенной: ядро вотчлиста + динамический топ Raydium по ликве ---
            if growth_enabled and not _killed and len(mine) < getattr(config, "GROWTH_MAX_POS", 3) and \
                    len(tracker.get_open_positions()) < config.MAX_CONCURRENT_POSITIONS:
                import market_data as _md
                universe = await _md.get_growth_universe()
                watch = list(dict.fromkeys(list(getattr(config, "GROWTH_WATCHLIST", [])) + universe))
                for mint in watch:
                    if mint in tracker.positions or len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                        continue
                    try:
                        ok = await analyzer.analyze_growth_token(mint)
                    except Exception as e:
                        print(f"🌱 GROWTH analyze err: {type(e).__name__} {e}")
                        continue
                    if ok is None:
                        continue
                    analyzer.log_scan(mint[:6], mint, "GROWTH",
                                      ok, getattr(analyzer, "last_score", 0.0))
                    if ok is True:
                        from jupiter import JupiterAPI
                        price = await JupiterAPI.get_price(mint)
                        if price > 0:
                            td = await analyzer.fetch_token_data(mint)
                            sym = ((td.get("baseToken") or {}).get("symbol", mint[:4]) if td else mint[:4]) or mint[:4]
                            if getattr(config, "DYNAMIC_REINVEST_ENABLED", True):
                                _gw_sz = tracker.calculate_dynamic_position_size(td or {}, chain="solana")
                                if _gw_sz <= 0:
                                    continue
                            else:
                                _gw_sz = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or getattr(config, "GROWTH_SIZE_USD", 15.0))
                            tracker.add_position(sym, mint, price, _gw_sz,
                                                 is_mature=True,
                                                 ml_features=analyzer.pack_features(td) if td else {},
                                                 ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                                 source=f"GROWTH:{analyzer.get_signal(mint)}",
                                                 pair_address=(td.get("pairAddress", "") if td else ""))
                            if mint in tracker.positions:
                                tracker.positions[mint].price_updated_at = _t.time()
                                tracker.save_portfolio()
                            print(f"🌱 GROWTH BUY {sym} @ ${price}")
                    await asyncio.sleep(2)
        except Exception as e:
            print(f"Ошибка в growth_loop: {e}")
        await asyncio.sleep(interval)


async def rugpull_feeder_loop():
    print("🧹 Запуск автоматического сборщика скам-рагпулов (раз в 6 часов)...")
    # Ждем 10 секунд перед первым запуском, чтобы не грузить систему на старте
    await asyncio.sleep(10)
    while True:
        try:
            import rugpull_feeder
            await asyncio.to_thread(rugpull_feeder.feed_rugs_and_retrain)
        except Exception as e:
            print(f"Ошибка в rugpull_feeder: {e}")
        try:
            import importlib.util
            if importlib.util.find_spec("train_rocket"):
                import train_rocket
                await asyncio.to_thread(train_rocket.retrain_rocket_quiet)
        except Exception as e:
            print(f"Ошибка в rocket-retrain: {e}")
        await asyncio.sleep(6 * 60 * 60)  # Спим 6 часов

def _evm_killed(tracker) -> bool:
    """Kill-switch только для ВХОДОВ. Выходы обязаны работать всегда."""
    import time as _t
    day_start = _t.time() - (_t.time() % 86400)
    day_pnl = sum(getattr(p, "pnl_usd", 0) or 0 for p in tracker.positions.values()
                  if getattr(p, "status", "") == "closed" and getattr(p, "exit_time", 0) and p.exit_time >= day_start)
    return bool(getattr(config, "KILL_SWITCH_ENABLED", True) and day_pnl <= -config.MAX_DAILY_LOSS_USD)


_evm_missing_fetch_ts: dict = {}

async def _evm_track_once(tracker, chain: str, tag: str, emoji: str):
    """Один проход трекинга позиций сети. Вызывается каждые ~2.5 сек отдельным таском
    при открытых позициях, поэтому стопы срабатывают мгновенно без проскальзывания."""
    import time as _t
    import evm_data
    mine = {m: p for m, p in tracker.get_open_positions().items()
            if getattr(p, "chain", "") == chain}
    if not mine:
        return
    # 0. Прямые цены с ноды (батч getReserves, мимо лимитов DS/GT). DS — только добор.
    px = {}
    if getattr(config, "EVM_DIRECT_ENABLED", True):
        try:
            import evm_pool as _ep
            _rpcs = getattr(config, "EVM_DIRECT_RPC", None) or None
            if _rpcs:
                _ep.RPCS.update({k: v for k, v in _rpcs.items() if v})
            px = await _ep.get_direct_prices(list(mine.keys()), chain) or {}
            if px:
                _direct_ok = sum(1 for m in mine if (m in px or m.lower() in px or m.upper() in px))
                print(f"{emoji} {tag} node-direct: {_direct_ok}/{len(mine)} цен с ноды")
        except Exception as e:
            print(f"{emoji} {tag} node-direct err: {e}")
            px = {}
    try:
        _need = [m for m in mine if not (px.get(m, 0) or px.get(m.lower(), 0) or px.get(m.upper(), 0))]
        if _need:
            _pairs = [getattr(mine[m], "pair_address", "") for m in _need if getattr(mine[m], "pair_address", "")]
            _ds = await evm_data.get_bulk_prices(_need, chain, pair_addresses=_pairs)
            for _k, _v in (_ds or {}).items():
                px.setdefault(_k, _v)  # нода первична, DS — добор
    except Exception as e:
        print(f"{emoji} {tag} bulk price err: {e}")
    # Добор слепых: кого нет в балке (свежие/тонкие пулы) — поштучно с кулдауном 15с,
    # иначе цены встают на entry, но при частых вызовах мы не спамим запросы и избегаем 429.
    def _px_get(_m: str) -> float:
        # case-insensitive: DS отдаёт checksum, трекер может хранить lower или pairAddress
        pos = mine.get(_m)
        pair = getattr(pos, "pair_address", "") if pos else ""
        return float(
            px.get(_m, 0) or px.get(_m.lower(), 0) or px.get(_m.upper(), 0) or
            (px.get(pair, 0) if pair else 0) or
            (px.get(pair.lower(), 0) if pair else 0) or
            (px.get(pair.upper(), 0) if pair else 0) or 0
        )
    _missing = [m for m in mine if not _px_get(m)]
    _now = _t.time()
    for _mm in _missing[:12]:
        if _now - _evm_missing_fetch_ts.get(_mm, 0) < 15.0:
            continue
        _evm_missing_fetch_ts[_mm] = _now
        try:
            _td1 = await evm_data.get_token_data(_mm, chain)
            _px1 = float((_td1.get("priceUsd", 0) or 0)) if _td1 else 0
            if _px1 > 0:
                px[_mm] = _px1
                px[_mm.lower()] = _px1
                px[_mm.upper()] = _px1
                pos_mm = mine.get(_mm)
                if pos_mm and not getattr(pos_mm, "pair_address", "") and _td1.get("pairAddress"):
                    pos_mm.pair_address = _td1["pairAddress"]
        except Exception:
            continue
    try:
        for mint, pos in list(mine.items()):
            try:
                _live_px = _px_get(mint)
                cur = _live_px or pos.current_price_usd or pos.entry_price_usd
                # Счётчик слепоты: ни балк, ни добор цены не дали (cur упал на entry/current).
                # ~5 мин (с расчётом на текущий интервал) без живой цены = выходим, слот не маринуем.
                _live = bool(_live_px)
                if _live:
                    pos.price_updated_at = _t.time()
                pos.price_stale_n = 0 if _live else int(getattr(pos, "price_stale_n", 0) or 0) + 1
                _max_stale_cycles = int(300 / max(float(getattr(config, "EVM_TRACK_INTERVAL", 2.5)), 1.0))
                if pos.price_stale_n >= _max_stale_cycles:
                    tracker.close_position(mint, pos.current_price_usd or pos.entry_price_usd,
                                           f"{tag} Stale Price (5 мин без цены)")
                    continue
                # Фантом node-direct (неверные decimals/пул): x50 за тик = битые
                # данные, не пик. Пик не двигаем, cur откатываем на прошлый.
                try:
                    if pos.entry_price_usd > 0 and cur / pos.entry_price_usd > 50:
                        print(f"⚠️ {tag} phantom {mint[:10]}: x{cur/pos.entry_price_usd:.0f} — игнор тика.")
                        pos.price_checked_at = _t.time()
                        continue
                except Exception:
                    pass
                if cur > pos.max_price_usd:
                    pos.max_price_usd = cur
                    pos.peak_time = _t.time()
                # Просадка раннера: минимум с входа + макс. DD (анализ выживших)
                try:
                    _prev_min = float(getattr(pos, "min_price_usd", 0) or 0)
                    if not _prev_min or cur < _prev_min:
                        pos.min_price_usd = cur
                    if pos.entry_price_usd:
                        _dd = (cur - pos.entry_price_usd) / pos.entry_price_usd
                        if _dd < float(getattr(pos, "max_dd_pct", 0) or 0):
                            pos.max_dd_pct = _dd
                except Exception:
                    pass
                prev = pos.current_price_usd
                pos.current_price_usd = cur
                if not pos.entry_price_usd:
                    continue
                pnl = (cur - pos.entry_price_usd) / pos.entry_price_usd
                maxp = (pos.max_price_usd - pos.entry_price_usd) / pos.entry_price_usd
                pos.current_pnl_usd = pos.amount_usd * pnl
                held = (_t.time() - pos.entry_time) / 60
                
                # --- АУДИТ БЕЗОПАСНОСТИ ОТКРЫТОЙ ПОЗИЦИИ (HONEYPOT / V4 LP RUG GUARD) ---
                # Если токен оказался Honeypot или создатель выдернул ликвидность из V4 PoolManager (кейс 1 / VC / FEE / Speed):
                # токен физически непродаваем на блокчейне! Списываем как честный Honeypot (-100% / $0.0).
                _last_sec_chk = getattr(pos, "last_security_check_ts", 0.0)
                _need_tp_audit = (
                    (maxp >= float(getattr(config, "TP1_TRIGGER_PCT", 1.10)) and not getattr(pos, "tp1_done", False))
                    or (maxp >= float(getattr(config, "TP2_TRIGGER_PCT", 2.50)) and not getattr(pos, "tp2_done", False))
                    or (maxp >= getattr(config, "MOONBAG_TRIGGER_PCT", 2.00) and not getattr(pos, "is_moonbag", False))
                    or (held >= 30 and (_t.time() - (getattr(pos, "peak_time", 0.0) or pos.entry_time)) >= 1500 and pnl > 0.10)
                )
                if (_t.time() - _last_sec_chk) > 60 or (_need_tp_audit and (_t.time() - _last_sec_chk) > 15):
                    pos.last_security_check_ts = _t.time()
                    try:
                        import goplus as _gp
                        # 1. Живые данные пула: передаём полный pair_data, чтобы не выбивать ложный $0 LP Rug
                        _live_td = await evm_data.get_token_data(mint, chain)
                        _live_liq = float(((_live_td or {}).get("liquidity") or {}).get("usd", 0) or 0)
                        _tx5 = ((_live_td or {}).get("txns") or {}).get("m5", {}) or {}
                        _live_txs = int(_tx5.get("buys", 0) or 0) + int(_tx5.get("sells", 0) or 0)
                        
                        _oc_ok, _oc_why = await _gp.check_onchain_evm_security(
                            mint, chain, _live_td or {"pairAddress": getattr(pos, "pair_address", "")}, is_open_pos=True
                        )
                        if not _oc_ok:
                            # Если это реальный LP Rug (пул опустошён) или бэкдор/заморозка — списываем немедленно, даже если DexScreener залип!
                            _is_hard_drain = any(k in _oc_why for k in ("LP Rug", "опустошён", "бэкдор", "заморозка", "пауза", "блэклист"))
                            if _is_hard_drain:
                                print(f"🚨 [{tag}-ONCHAIN-RUG] {pos.symbol} ({mint[:10]}): {_oc_why} — подтверждён ончейн вывод ликвидности/бэкдор, мгновенное списание.")
                                tracker.close_position(mint, 0.0, f"{tag} On-Chain Rug ({_oc_why})")
                                continue
                            elif _live_liq >= 1500.0 and _live_txs >= 2 and cur > 0:
                                print(f"⚠️ [{tag}] Предупреждение ончейна: {_oc_why}, но токен активно торгуется (ликва ${_live_liq:.0f}, сделок 5м: {_live_txs}) — ложное списание предотвращено.")
                            else:
                                print(f"🚨 [{tag}-V4-RUG/HONEYPOT] {pos.symbol} ({mint[:10]}): {_oc_why} — пул осушен или контракт заблокирован, списание в -100%.")
                                tracker.close_position(mint, 0.0, f"{tag} Honeypot / V4 LP Rug ({_oc_why})")
                                continue
                        # 2. Свежая проверка GoPlus в обход 1-часового кеша (force_refresh=True)
                        _g_ok, _g_why = await _gp.check_token(mint, chain, force_refresh=True)
                        _w_low = str(_g_why or "").lower()
                        if not _g_ok and any(k in _w_low for k in (
                            "honeypot", "cannot_sell", "creator_pct", "owner_pct", "blacklist", "cooldown",
                            "tax", "pause", "transfer_pausable", "freeze", "freezable", "frozen",
                            "non_transferable", "transfer_hook", "default_account", "closable",
                            "balance_mutable", "whitelist", "anti_whale", "slippage", "closed_source", "заморозка"
                        )):
                            print(f"🚨 [{tag}-HONEYPOT] {pos.symbol} ({mint[:10]}): {_g_why} — токен непродаваем/заморожен, списание в -100%.")
                            tracker.close_position(mint, 0.0, f"{tag} Honeypot / Unsellable ({_g_why})")
                            continue
                    except Exception:
                        pass

                reason = None
                prev_ts = getattr(pos, "price_checked_at", 0.0)
                _mbr = float(getattr(config, "MOONBAG_TRIGGER_PCT", 2.50))
                _mb_sell = float(getattr(config, "MOONBAG_SELL_FRAC", 0.30))
                # RUNNER MODE: параметры, при которых SHCAT +1126%/+627%,
                # 中国人能飞 +152% держались ~5ч и фильтры их НЕ выбили:
                # в плюсе время не режем, стопы от входа OFF, только трейлинг от пика.
                _runner_thr = float(getattr(config, "EVM_RUNNER_MAXP", 0.50))
                _is_runner = maxp >= _runner_thr
                _is_moon = bool(getattr(pos, "is_moonbag", False))
                
                # 1. ГАРАНТИРОВАННЫЙ НЕСНИЖАЕМЫЙ ПОРОГ ПРИБЫЛИ (PROFIT FLOOR LOCK)
                _floor_pnl = None
                if maxp >= 5.00:       # Гипер-ракета (+500%+): держим 75% прибыли (пик +692% -> пол +519%)
                    _floor_pnl = round(maxp * 0.75, 2)
                elif maxp >= 3.00:     # Мега-ракета (+300%..+500%): держим 72% прибыли (пик +400% -> пол +288%)
                    _floor_pnl = max(2.35, round(maxp * 0.72, 2))
                elif maxp >= 2.00:     # Пик +200%+ (3x) -> гарантируем минимум +150%
                    _floor_pnl = 1.50
                elif maxp >= 1.50:     # Пик +150%+ (2.5x) -> гарантируем минимум +110%
                    _floor_pnl = 1.10
                elif maxp >= 1.00:     # Пик +100%+ (2x) -> гарантируем минимум +70%
                    _floor_pnl = 0.70
                elif maxp >= 0.70:     # Пик +70%+ -> гарантируем минимум +45%
                    _floor_pnl = 0.45
                elif maxp >= 0.50:     # Пик +50%+ -> гарантируем минимум +28%
                    _floor_pnl = 0.28
                elif maxp >= 0.35:     # Пик +35%+ -> гарантируем безубыток +8%
                    _floor_pnl = 0.08

                # 2. СУЖАЮЩИЙСЯ ПАРАБОЛИЧЕСКИЙ ТРЕЙЛИНГ (чем выше пик, тем плотнее зажимаем профит у вершины!)
                if maxp >= 2.50:       # Мега-ракета (+250%+): режем откат уже на 9% от макушки (без dd 36%!)
                    _trail_dist = 0.09
                elif maxp >= 1.50:     # +150%..+250%: 10% от пика
                    _trail_dist = 0.10
                elif maxp >= 0.80 or _is_moon: # +80%..+150%: 12% от пика
                    _trail_dist = float(getattr(config, "TRAILING_RUNNER_DISTANCE_PCT", 0.12))
                else:                  # Диапазон +45%..+80%: 16% воздуха
                    _trail_dist = float(getattr(config, "TRAILING_DISTANCE_PCT", 0.16))
                    
                _crash_drop = float(getattr(config, "EVM_RUNNER_CRASH", 0.25)) if _is_runner else float(getattr(config, "EVM_NORMAL_CRASH", 0.22))
                _dead_min = int(getattr(config, "EVM_DEAD_MIN", 60))
                _stag_min = int(getattr(config, "EVM_STAGNANT_MIN", 45))
                _tp1_evm_thr = float(getattr(config, "TP1_TRIGGER_PCT", 0.50))
                _tp1_sell = float(getattr(config, "TP1_SELL_FRAC", 0.20))
                if getattr(config, "PARTIAL_TP_ENABLED", False) and maxp >= _tp1_evm_thr and not getattr(pos, "tp1_done", False):
                    tracker.partial_close_position(mint, cur, _tp1_sell, f"{tag} Take Profit +{_tp1_evm_thr*100:.0f}% (Tier-1 {int(_tp1_sell*100)}%)")
                    pos.tp1_done = True
                    pos.is_moonbag = True
                    pos.price_checked_at = _t.time()
                    continue
                _tp2_thr = float(getattr(config, "TP2_TRIGGER_PCT", 1.20))
                _tp2_sell = float(getattr(config, "TP2_SELL_FRAC", 0.25))
                if getattr(config, "PARTIAL_TP_ENABLED", False) and maxp >= _tp2_thr and not getattr(pos, "tp2_done", False):
                    tracker.partial_close_position(mint, cur, _tp2_sell, f"{tag} Take Profit +{_tp2_thr*100:.0f}% (2.2x Free Ride)")
                    pos.tp2_done = True
                    pos.price_checked_at = _t.time()
                    continue
                if getattr(config, "PARTIAL_TP_ENABLED", False) and maxp >= _mbr and not getattr(pos, "is_moonbag", False):
                    tracker.partial_close_position(mint, cur, _mb_sell, f"{tag} Take Profit +{_mbr*100:.0f}% (Risk Free)")
                    pos.is_moonbag = True # ИСПРАВЛЕН БАГ (бесконечные продажи)
                    pos.price_checked_at = _t.time()
                    continue

                # 2-тиковое подтверждение резкого провала за 1 тик (>=22% за тик или обвал с пика +35%+ в минус):
                # защищает ракеты (кейс TSI: прокол -33% за 3с перед ростом +400% / 5x на пуле $275k) от 1-секундных теней и побочных пулов.
                # На 1-м тике откатываем pos.current_price_usd = prev, чтобы на 2-м тике (через 3с) сравнение шло с реальной до-прокольной ценой!
                _w_guard = float(getattr(config, "WHALE_DUMP_GUARD_PCT", 0.22))
                _min_guard_drop = min(_w_guard, _crash_drop)
                _sudden_tick_drop = (
                    (prev > 0 and cur <= prev * (1.0 - _min_guard_drop))
                    or (maxp >= 0.35 and pnl <= -0.05)
                )
                if _sudden_tick_drop:
                    pos.crash_confirm_n = int(getattr(pos, "crash_confirm_n", 0) or 0) + 1
                    if pos.crash_confirm_n < 2:
                        pos.current_price_usd = prev
                        if pos.entry_price_usd:
                            pos.current_pnl_usd = pos.amount_usd * ((prev - pos.entry_price_usd) / pos.entry_price_usd)
                        print(f"⚠️ [{tag}-TICK-VERIFY] {pos.symbol} ({mint[:10]}): резкий прокол за 1 тик ({(1 - cur / prev)*100 if prev > 0 else 0:.0f}% -> {pnl*100:+.1f}%) — ждём 3-й тик подтверждения (3с).")
                        continue
                else:
                    pos.crash_confirm_n = 0

                if not reason and prev_ts and (_t.time() - prev_ts) < 20 and prev > 0 and cur <= prev * (1 - _w_guard) and maxp >= 0.35:
                    reason = f"{tag} Whale Dump Guard ({(1 - cur / prev) * 100:.0f}% за {_t.time() - prev_ts:.0f}с, 3 тика)"
                elif not reason and prev_ts and (_t.time() - prev_ts) < 60 and prev > 0 and cur <= prev * (1 - _crash_drop):
                    reason = f"{tag} Crash Guard ({(1 - cur / prev) * 100:.0f}% за {_t.time() - prev_ts:.0f}с, 3 тика)"
                elif _floor_pnl is not None and pnl <= _floor_pnl:
                    if pnl >= 0:
                        reason = f"{tag} Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{maxp*100:.0f}%)"
                    else:
                        reason = f"{tag} Crash Dump after peak (+{maxp*100:.0f}% -> {pnl*100:.0f}%)"
                elif maxp >= getattr(config, "TRAILING_ACTIVATION_PCT", 0.45) and \
                        (pos.max_price_usd - cur) / pos.max_price_usd >= _trail_dist:
                    reason = f"{tag} Trailing (peak +{maxp * 100:.0f}%, dd {(1 - cur / pos.max_price_usd) * 100:.0f}%)"
                elif not reason and maxp >= 0.35 and (_t.time() - (getattr(pos, "peak_time", 0.0) or pos.entry_time)) >= 1500 and pnl > 0.05 and ((pos.max_price_usd - cur) / pos.max_price_usd) >= 0.08:
                    _pk_age = (_t.time() - (getattr(pos, "peak_time", 0.0) or pos.entry_time)) / 60
                    reason = f"{tag} Runner Stagnation (+{pnl*100:.1f}% locked after peak +{maxp*100:.0f}%, {_pk_age:.0f}м без ATH)"
                elif not reason and held >= 30 and (_t.time() - (getattr(pos, "peak_time", 0.0) or pos.entry_time)) >= 1500:
                    if pnl > 0.10:
                        reason = f"{tag} Inactive Pool TP (+{pnl*100:.1f}%, {held:.0f}м в рынке)"
                    elif pnl < -0.05:
                        reason = f"{tag} Inactive Pool Cut ({pnl*100:.1f}%, {held:.0f}м)"
                elif held >= 1.5 and pnl <= -0.30:
                    reason = f"{tag} Emergency Cap ({pnl * 100:.1f}%)"
                elif held >= 6.0 and maxp >= 0.20 and pnl <= -0.10:
                    # Мягкий стоп только после 6+ минут (не режем стартовые свечи в первые минуты):
                    reason = f"{tag} Soft Guard (был пик +{maxp * 100:.0f}%, слив до {pnl * 100:.1f}%)"
                elif held >= 1.0 and maxp >= float(getattr(config, "BREAKEVEN_PCT", 0.10)) and pnl <= 0.015:
                    reason = f"{tag} Breakeven (был пик +{maxp * 100:.0f}%)"
                elif held < float(getattr(config, "INFANT_WINDOW_MIN", 1.0)) and pnl <= float(getattr(config, "INFANT_DUMP_PCT", -0.42)):
                    if held >= 0.75 or pnl <= -0.65:
                        reason = f"{tag} Infant Dump ({pnl * 100:.1f}%)"
                elif not _is_runner and maxp < 0.015 and pnl <= -0.16 and held >= 7.0:
                    reason = f"{tag} Zero-Momentum Cut ({held:.0f}m, {pnl * 100:.1f}%)"
                elif not _is_runner and maxp < 0.02 and pnl <= -0.22 and held >= 8.0:
                    reason = f"{tag} Fail Cut ({held:.0f}m, {pnl * 100:.1f}%)"
                # JEV: СТОП ПОД ФИТИЛЬ ("The stop lives under the wick")
                elif not reason and held >= 0.5 and (
                    (getattr(pos, "stop_under_wick", 0.0) > 0 and cur <= getattr(pos, "stop_under_wick", 0.0)) or
                    (getattr(pos, "wick_stop_loss_pct", 0.0) < 0 and pnl <= getattr(pos, "wick_stop_loss_pct", 0.0))
                ):
                    _ws = getattr(pos, "stop_under_wick", 0.0)
                    reason = f"{tag} JEV Wick Stop ({pnl * 100:.1f}%)"
                elif not _is_runner and held >= 1.5 and pnl <= config.STOP_LOSS_PCT:
                    reason = f"{tag} Stop ({pnl * 100:.1f}%)"
                elif (not _is_runner and maxp < 0.05 and pnl < -0.05
                        and held >= float(getattr(config, "STAGNANT_LOSS_MIN", 15))):
                    # PRO Bleed-cut: ни намёка на жизнь — не мариновать до Dead 60м
                    # (memestock гнил 61м до -14.5%). Раннеры (maxp 5%+) не трогаем.
                    reason = f"{tag} Bleed ({held:.0f}m, {pnl * 100:.1f}%)"
                elif not _is_runner and held >= _dead_min and pnl < -0.05:
                    reason = f"{tag} Dead ({held:.0f}m)"
                elif not _is_runner and held >= _stag_min and abs(pnl) < 0.05 and maxp < 0.05:
                    # ЖИВОЙ ФЛЕТ НЕ РЕЖЕМ: консолидация перед выстрелом выглядит как ~0%.
                    # Есть сделки за 5 мин = пул жив: +5 мин грейса (макс 2). Нет сделок = труп.
                    # Ошибка API = пропуск цикла (Dead на 15м всё равно подстрахует).
                    _graces = getattr(pos, "stagnant_graces", 0) or 0
                    try:
                        _td = await evm_data.get_token_data(mint, chain)
                        _tx5 = ((_td.get("txns") or {}).get("m5", {}) or {}) if _td else {}
                        _alive = ((_tx5.get("buys", 0) or 0) + (_tx5.get("sells", 0) or 0)) >= 3
                    except Exception:
                        pos.price_checked_at = _t.time()
                        continue
                    if _alive and _graces < 2:
                        pos.stagnant_graces = _graces + 1
                        pos.entry_time = _t.time() - 4 * 60
                        print(f"⏳ {emoji} {tag} {mint[:10]}: живой флет ({pnl*100:.1f}%), грейс {pos.stagnant_graces}/2 — ждём выстрел.")
                        pos.price_checked_at = _t.time()
                        continue
                    reason = f"{tag} Stagnant ({held:.0f}m)"
                # DEAD POOL GUARD (ловит застрявшие/замороженные пулы вроде BRML/P2L/JEET: 0 сделок, цена застыла)
                # Не убивает прибыльные сделки (GRASS кейс: +1.26% закрыло из-за временной 7.5с задержки API)
                if not reason and held >= 3.0:
                    try:
                        _td_pool = await evm_data.get_token_data(mint, chain)
                        if _td_pool:
                            _tx5_p = (_td_pool.get("txns") or {}).get("m5", {}) or {}
                            _txh1_p = (_td_pool.get("txns") or {}).get("h1", {}) or {}
                            _b5_p = int(_tx5_p.get("buys", 0) or 0)
                            _s5_p = int(_tx5_p.get("sells", 0) or 0)
                            _bh1_p = int(_txh1_p.get("buys", 0) or 0)
                            _sh1_p = int(_txh1_p.get("sells", 0) or 0)
                            if (_bh1_p + _sh1_p) == 0 or ((_b5_p + _s5_p) == 0 and float(((_td_pool.get("volume") or {}).get("m5", 0)) or 0) <= 0.01):
                                _z_cyc = int(getattr(pos, "zero_vol_cycles", 0) or 0) + 1
                                pos.zero_vol_cycles = _z_cyc
                                if _z_cyc >= 8:  # ~20 сек подтвержденной тишины в пуле
                                    # Проверяем ончейн перед тем как засчитывать "TP": если ликва выведена девом — это раг (-100%), а не прибыль!
                                    _oc_chk_ok, _oc_chk_why = await _gp.check_onchain_evm_security(mint, chain, _td_pool, is_open_pos=True)
                                    if not _oc_chk_ok:
                                        reason = f"{tag} Dead Pool Rug ({_oc_chk_why})"
                                    elif pnl > 0.02:
                                        reason = f"{tag} Stagnant Pool TP (+{pnl*100:.1f}%, 0 txns in pool)"
                                    else:
                                        reason = f"{tag} Dead Pool (0 txns in pool, frozen)"
                            else:
                                pos.zero_vol_cycles = 0
                    except Exception:
                        pass
                pos.price_checked_at = _t.time()
                if reason:
                    _exit_px = 0.0 if any(k in reason.upper() for k in ("HONEYPOT", "RUG", "SCAM", "CANNOT SELL", "UNSELLABLE", "NO LIQUIDITY", "DRAIN", "DEAD", "ZERO-RUG", "PULL")) else cur
                    tracker.close_position(mint, _exit_px, reason)
            except Exception as _e:
                # Одна битая позиция не должна ронять весь трек
                # (иначе все цены встают на $0.00 как в баге price_stale_n).
                print(f"{emoji} {tag} pos err {str(mint)[:10]}: {_e}")
                continue
    finally:
        try:
            tracker.save_portfolio()
        except Exception:
            pass


async def _evm_chain_loop(analyzer, tracker, chain: str):
    """Обобщённая EVM-петля: быстрый трек (12с) + скан (25с) отдельными тасками.
    Kill-switch стопает только входы - выходы идут всегда (баг: раньше час без управления)."""
    import evm_data
    tag = (evm_data.CHAINS.get(chain) or {}).get("tag", chain.upper())
    emoji = {"robinhood": "🟣", "base": "🟦", "bsc": "🟨", "ethereum": "⬜"}.get(chain, "🔵")
    print(f"{emoji} {tag} Loop запущен: мемы сети {chain}!")
    processed = {}
    _iv_key = {"base": "BASE_SCAN_INTERVAL", "bsc": "BSC_SCAN_INTERVAL", "ethereum": "ETHEREUM_SCAN_INTERVAL"}.get(chain, "ROBINHOOD_SCAN_INTERVAL")
    interval = getattr(config, _iv_key, 25)
    _recooldown = getattr(config, "EVM_RESCAN_COOLDOWN", 300)

    async def track_task():
        while True:
            try:
                await _evm_track_once(tracker, chain, tag, emoji)
            except Exception as e:
                print(f"{emoji} {tag} track err: {e}")
            # Адаптивный интервал: при открытых сделках в сети — 2.5с (быстрый стоп),
            # если позиций нет — 6с (экономия лимитов API)
            has_open = any(getattr(p, "chain", "") == chain for p in tracker.get_open_positions().values())
            _iv = float(getattr(config, "EVM_TRACK_INTERVAL", 2.5)) if has_open else float(getattr(config, "EVM_IDLE_TRACK_INTERVAL", 6.0))
            await asyncio.sleep(_iv)

    async def scan_task():
        while True:
            try:
                if chain == "ethereum" and not getattr(config, "ETHEREUM_ENTRIES_ENABLED", False):
                    # L1: только ведём существующие (KLIK), новых не открываем — газ съедает.
                    await asyncio.sleep(interval)
                    continue
                if chain == "bsc" and not getattr(config, "BSC_ENABLED", False):
                    # BSC: только ведём существующие открытые сделки, новых не открываем
                    await asyncio.sleep(interval)
                    continue
                if chain == "robinhood" and not getattr(config, "ROBINHOOD_ENTRIES_ENABLED", False):
                    # Robinhood: отключены входы из-за 100% V4 рагпуллов дева
                    await asyncio.sleep(interval)
                    continue
                if _evm_killed(tracker):
                    print(f"🛑 {tag} KILL-SWITCH: входы на паузе 5мин (выходы работают).")
                    await asyncio.sleep(int(getattr(config, "KILL_SWITCH_PAUSE", 300)))
                    continue
                # --- Скан новых: WSS-свежатина + свежие пулы GT ПЕРВЫЕ, потом бусты/профили/тренды/топ/поиск ---
                if tracker.can_open_new_position(config.MAX_CONCURRENT_POSITIONS):
                    import time as _t
                    import evm_wss as _evm_wss
                    try:
                        _wss_list = _evm_wss.drain_fresh(chain) or []
                    except Exception:
                        _wss_list = []
                    _wss_fresh = set(_wss_list)
                    boosted  = await evm_data.fetch_boosted_tokens(chain)
                    profiles = await evm_data.fetch_profile_tokens(chain)
                    trending = await evm_data.get_trending_pools_gt(chain)
                    fresh    = await evm_data.get_new_pools_gt(chain)
                    top_vol  = await evm_data.get_top_volume_pools_gt(chain)
                    search   = await evm_data.fetch_dex_search_tokens(chain)
                    _user = [w for w in (getattr(config, "USER_WATCHLIST", []) or [])
                             if w and w.startswith("0x")]
                    _cap = int(getattr(config, "EVM_MAX_MINTS", 120))
                    
                    # Round-robin честное объединение источников (свежие пулы, бусты, WSS не голодают за трендами)
                    _sources = [_user, _wss_list[-25:], fresh, boosted, trending, top_vol, profiles, search]
                    _interleaved = []
                    _max_len = max((len(s) for s in _sources), default=0)
                    for _i in range(_max_len):
                        for _s in _sources:
                            if _i < len(_s):
                                _interleaved.append(_s[_i])
                    
                    # Полная нормализация и удаление дубликатов (lowercase для EVM адресов)
                    _all_candidates = []
                    _seen_candidates = set()
                    for a in _interleaved:
                        if not a:
                            continue
                        a_norm = a.strip().lower() if a.strip().startswith("0x") else a.strip()
                        if a_norm not in _seen_candidates:
                            _seen_candidates.add(a_norm)
                            _all_candidates.append(a_norm)

                    # Фильтруем кулдаун ДО среза _cap, чтобы остывающие токены не вытесняли активные кандидаты!
                    _now_scan = _t.time()
                    if len(processed) > 1500:
                        processed.clear()
                    _EVM_STATIC_SKIP = {
                        "0x4200000000000000000000000000000000000006", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb", "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",
                        "0x940181a94a35a4569e4529a3cdfb74e38fd98631", "0x4ed4e862860bed51a9570b96d89af5e1b0efefed",
                        "0x532f27101965dd16442e59d40670faf5ebb142e4", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
                        "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b", "0xa4a2e2ca3fbfe21aed83471d28b6f65a233c6e00",
                        "0x3e12b9d6a4d12cd9b4a6d613872d0eb32f68b380", "0xef5997c2cf2f6c138196f8a6203afc335206b3c1",
                        "0x6985884c4392d348587b19cb9eaaf157f13271cd", "0x9eadbe35f3ee3bf3e28180070c429298a1b02f93",
                        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "0x55d398326f99059f775485246999027b3197955c",
                        "0xe9e7cea3dedca5984780bafc599bd69add087d56", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
                        "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
                    }
                    mints = []
                    open_pos_addrs = {str(m).strip().lower() for m in tracker.positions.keys()}
                    for addr in _all_candidates:
                        if not addr or addr in open_pos_addrs or addr.lower() in _EVM_STATIC_SKIP:
                            continue
                        _prev = tracker.last_loss_pct(addr)
                        if _prev is not None and _prev <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                            continue
                        # Отсекаем монеты старше 48 часов из кэша EVM, если у них нет активного пробоя (m5 < 3%)
                        _cached_data = getattr(evm_data, "_EVM_PREFETCH_CACHE", {}).get((chain.lower(), addr.lower()), (0, {}))[1]
                        if _cached_data:
                            _c_at = _cached_data.get("pairCreatedAt", 0)
                            _c_m5 = float((_cached_data.get("priceChange") or {}).get("m5", 0) or 0)
                            if _c_at and (_t.time() * 1000 - _c_at) > 172800000 and _c_m5 < 3.0:  # старше 48 часов без импульса
                                continue

                        _is_wss = addr in _wss_fresh
                        _cd = 20 if _is_wss else _recooldown
                        if _now_scan - processed.get(addr, 0.0) < _cd:
                            continue
                            
                        mints.append(addr)
                        if len(mints) >= _cap:
                            break

                    if mints:
                        try:
                            await evm_data.prefetch_token_data(mints, chain)
                        except Exception as _pe:
                            print(f"{emoji} {tag} prefetch err: {_pe}")

                    for addr in mints:
                        _is_wss = addr in _wss_fresh
                        _cached_data = getattr(evm_data, "_EVM_PREFETCH_CACHE", {}).get((chain.lower(), addr.lower()), (0, {}))[1]
                        if _cached_data:
                            _c_at = _cached_data.get("pairCreatedAt", 0)
                            _c_age_h = (_t.time() * 1000 - _c_at) / 3600000.0 if _c_at else 999.0
                            _c_m5 = float((_cached_data.get("priceChange") or {}).get("m5", 0) or 0)
                            if _c_at and _c_age_h > 48.0 and _c_m5 < 3.0:
                                processed[addr] = _t.time() + 1800  # Старый токен без импульса: пауза 30 мин
                                continue
                            _c_liq = float((_cached_data.get("liquidity") or {}).get("usd", 0) or 0)
                            _c_q_amt = float((_cached_data.get("liquidity") or {}).get("quote", 0) or 0)
                            _c_q_sym = str((_cached_data.get("quoteToken") or {}).get("symbol", "")).upper()

                            # Быстрый фильтр пустых/фантомных пулов без реальной ликвидности (<$2k или <0.15 ETH / 0.5 BNB)
                            _is_empty = False
                            if 0 < _c_liq < 2000.0:
                                _is_empty = True
                            elif chain in ("base", "robinhood", "ethereum") and ("ETH" in _c_q_sym or "WETH" in _c_q_sym) and 0 < _c_q_amt < 0.15:
                                _is_empty = True
                            elif chain == "bsc" and ("BNB" in _c_q_sym or "WBNB" in _c_q_sym) and 0 < _c_q_amt < 0.5:
                                _is_empty = True

                            if _is_empty:
                                processed[addr] = _t.time() + 3600  # Пустышка без ликвидности: скип на 1 час
                                continue

                        try:
                            ok = await analyzer.analyze_robinhood_token(addr, chain)
                        except Exception as e:
                            print(f"{emoji} {tag} analyze err {addr[:10]}: {type(e).__name__} {e}")
                            continue
                        if ok is None:
                            _lq_tmp = float((getattr(analyzer, "_eval_ctx", {}).get(addr, {}) or {}).get("liq", 0.0) or 0.0)
                            if _lq_tmp >= 5000.0:
                                processed[addr] = _t.time() - _recooldown + 20
                            else:
                                processed[addr] = _t.time() + 300
                            continue
                        _sig = str(analyzer.get_signal(addr) or "")
                        if ok is False:
                            if "micro-liq" in _sig:
                                processed[addr] = _t.time() - _recooldown + 20  # Дев может залить ликвидность через 20с
                            elif "age" in _sig:
                                processed[addr] = _t.time() - _recooldown + 20  # Ждем созревания 20с
                            elif "m1-green" in _sig or "вершина" in _sig or "scout-m1" in _sig:
                                processed[addr] = _t.time() - _recooldown + 20  # Ждем отката 1м импульса 20с
                            elif "flat" in _sig or "m5-no-pressure" in _sig or "quiet" in _sig or "dead-flat" in _sig:
                                _lq_tmp = float((getattr(analyzer, "_eval_ctx", {}).get(addr, {}) or {}).get("liq", 0.0) or 0.0)
                                if _lq_tmp > 350000.0 or "dex-giant" in _sig:
                                    processed[addr] = _t.time() - _recooldown + 300  # Мамонты / стейблы: пауза 5 минут
                                elif _lq_tmp >= 2000:
                                    processed[addr] = _t.time() - _recooldown + 25   # Живой мем-пул перепроверить через 25с
                                else:
                                    processed[addr] = _t.time() - _recooldown + 60
                            elif "antiscam-" in _sig or "honeypot" in _sig or "ghost-pool" in _sig:
                                processed[addr] = _t.time() - _recooldown + 600      # Подтверждённый скам: пауза 10 минут, не тратить квоту
                            elif "rkt-low" in _sig or "scout-low-rkt" in _sig:
                                processed[addr] = _t.time() - _recooldown + 45
                            else:
                                processed[addr] = _t.time() - _recooldown + 25       # Перепроверка через 25с (например, score подрастёт)
                        else:
                            processed[addr] = _t.time()
                        analyzer.log_scan(addr[:8], addr, tag,
                                          ok, getattr(analyzer, "last_score", 0.0))
                        if ok is True and tracker.can_open_new_position(config.MAX_CONCURRENT_POSITIONS):
                            td = await evm_data.get_token_data(addr, chain)
                            price = float(td.get("priceUsd", 0) or 0) if td else 0
                            sym = ((td.get("baseToken") or {}).get("symbol", tag) if td else tag) or tag
                            if price > 0:
                                _pc = (td.get("priceChange") or {}) if td else {}
                                _tx5 = ((td.get("txns") or {}).get("m5", {}) or {}) if td else {}
                                analyzer.log_scan(sym, addr, tag, True,
                                                  getattr(analyzer, "last_score", 0.0),
                                                  (td.get("liquidity") or {}).get("usd", 0) or 0,
                                                  _pc.get("m5", 0) or 0,
                                                  _tx5.get("buys", 0) or 0, _tx5.get("sells", 0) or 0)
                                _sig = analyzer.get_signal(addr)
                                _veto = analyzer.rocket_veto(addr, td, _sig)
                                if _veto:
                                    print(f"🚫 [{tag}] {sym}: {_veto}")
                                    analyzer.log_scan(sym, addr, tag, False,
                                                      getattr(analyzer, "last_score", 0.0))
                                    await asyncio.sleep(1.0)
                                    continue
                                if getattr(config, "DYNAMIC_REINVEST_ENABLED", True):
                                    size = tracker.calculate_dynamic_position_size(td, chain=chain)
                                    if size <= 0:
                                        print(f"🚫 [{tag}] {sym}: нулевой сайз / нет свободного кэша.")
                                        await asyncio.sleep(1.0)
                                        continue
                                else:
                                    fixed = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                                    if fixed > 0:
                                        size = fixed  # Строго заданный ордер пользователя ($15)
                                    else:
                                        cap = tracker.get_total_capital()
                                        size = max(4.0, min(100.0, cap * (config.REINVEST_PERCENT / 100.0))) if cap > 0 else 4.0
                                        if "LOTTERY" in _sig or "SCOUT" in _sig:
                                            _scout_cap = float(getattr(config, "SCOUT_SIZE_USD", getattr(config, "LOTTERY_SIZE_USD", 15.0)) or 15.0)
                                            size = min(size, _scout_cap)
                                        else:
                                            size *= analyzer.conviction_size_mult(td)
                                            if "DIPBUY" not in _sig:
                                                try:
                                                    _rm = analyzer.rocket_size_mult(addr)
                                                    size *= _rm
                                                except Exception:
                                                    pass
                                            size = min(size, 100.0)
                                            if chain == "robinhood":
                                                size = min(size, float(getattr(config, "ROB_MAX_SIZE_USD", 25.0) or 25.0))
                                            if chain == "bsc":
                                                _bsc_cap = float(getattr(config, "BSC_MAX_SIZE_USD", 25.0) or 25.0)
                                                size = min(size, _bsc_cap)
                                                _rkt_s = analyzer.rocket_scores.get(addr, 0.0)
                                                if _rkt_s < 0.65:
                                                    _w_sz = float(getattr(config, "BSC_WEAK_SIZE_USD", 15.0))
                                                    size = min(size, _w_sz)
                                if chain == "ethereum":
                                    # L1-газ $2-6 за круг: микро-сайз $4-6 гарантирует минус.
                                    # Меньше минимума — скип входа (а не враньё в плюс).
                                    _eth_min = float(getattr(config, "ETHEREUM_MIN_SIZE_USD", 15.0))
                                    if size < _eth_min:
                                        print(f"🚫 {tag} {sym}: сайз ${size:.2f} < min ${_eth_min:.0f} (L1-газ) — скип.")
                                        await asyncio.sleep(1.0)
                                        continue
                                # GOPLUS + HONEYPOT.IS + ON-CHAIN 3-Layer Security Gate
                                try:
                                    import goplus as _gp
                                    _v_ok, _v_why = await _gp.verify_evm_token(addr, chain, td)
                                    if not _v_ok:
                                        print(f"🚫 [{tag}] {sym}: {_v_why} — блок входа.")
                                        await asyncio.sleep(1.0)
                                        continue
                                except Exception as _ve:
                                    if chain in ("bsc", "robinhood", "base"):
                                        print(f"🚫 [{tag}] {sym}: ошибка антискама ({type(_ve).__name__}) — блок входа ({tag}).")
                                        await asyncio.sleep(1.0)
                                        continue

                                # === GROK BOT x JEV (Base) ===
                                from grok_jev import grok_jev
                                _jev_hard_ok, _jev_hard_why = grok_jev.hard_gate(addr, td, chain)
                                if not _jev_hard_ok:
                                    print(f"🚫 [JEV-HARD-GATE] {sym} ({addr[:8]}): {_jev_hard_why} — отказ входа.")
                                    await asyncio.sleep(1.0)
                                    continue
                                _jev_soft_ok, _jev_data, _jev_soft_why = await grok_jev.jev_vet(addr, sym, td, chain)
                                if not _jev_soft_ok:
                                    print(f"🚫 [JEV-AI-GATE] {sym} ({addr[:8]}): {_jev_soft_why} — отказ входа.")
                                    await asyncio.sleep(1.0)
                                    continue
                                _flush_ok, _wick_pct, _wick_stop_price, _flush_msg = grok_jev.check_flush_and_wick(td, price)
                                if not _flush_ok:
                                    print(f"⏳ [JEV-FILLS] {sym} ({addr[:8]}): {_flush_msg}")
                                    await asyncio.sleep(1.0)
                                    continue
                                else:
                                    print(f"🎯 [JEV-FILLS] {sym} ({addr[:8]}): {_flush_msg}")

                                tracker.add_position(sym, addr, price, size, is_mature=True,
                                                 ml_features=analyzer.pack_features(td),
                                                 ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                                 source=(f"{tag}:JEV:{_sig}"),
                                                 chain=chain,
                                                 pair_address=td.get("pairAddress", ""),
                                                 stop_under_wick=_wick_stop_price,
                                                 wick_stop_loss_pct=_wick_pct,
                                                 initial_liquidity_usd=float(((td or {}).get("liquidity", {}) or {}).get("usd", 0) or 0),
                                                 pair_data=td)
                                grok_jev.FUNNEL_STATS["filled"] += 1
                                print(grok_jev.get_funnel_summary())
                                print(f"{emoji} {tag} BUY {sym} {addr[:10]} @ ${price}")
                            await asyncio.sleep(1.0)
                        if len(processed) > 1000:
                            processed.clear()
            except Exception as e:
                print(f"Ошибка в {tag}_loop: {e}")
            await asyncio.sleep(interval)

    await asyncio.gather(track_task(), scan_task())


async def robinhood_loop(analyzer, tracker):
    has_open_rh = any(getattr(p, "chain", "") == "robinhood" for p in tracker.get_open_positions().values())
    if (getattr(config, "SOLANA_ONLY", True) or not getattr(config, "ROBINHOOD_ENABLED", False)) and not has_open_rh:
        print("🟣 Robinhood отключён (SOLANA_ONLY=True / ROBINHOOD_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "robinhood")


async def base_loop(analyzer, tracker):
    has_open_base = any(getattr(p, "chain", "") == "base" for p in tracker.get_open_positions().values())
    if (getattr(config, "SOLANA_ONLY", True) or not getattr(config, "BASE_ENABLED", False)) and not has_open_base:
        print("🟦 Base отключён (SOLANA_ONLY=True / BASE_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "base")


async def bsc_loop(analyzer, tracker):
    has_open_bsc = any(getattr(p, "chain", "") == "bsc" for p in tracker.get_open_positions().values())
    if (getattr(config, "SOLANA_ONLY", True) or not getattr(config, "BSC_ENABLED", False)) and not has_open_bsc:
        print("🟨 BSC отключён (SOLANA_ONLY=True / BSC_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "bsc")


async def ethereum_loop(analyzer, tracker):
    has_open_eth = any(getattr(p, "chain", "") == "ethereum" for p in tracker.get_open_positions().values())
    if (getattr(config, "SOLANA_ONLY", True) or not getattr(config, "ETHEREUM_ENABLED", False)) and not has_open_eth:
        print("⬜ Ethereum отключён (SOLANA_ONLY=True / ETHEREUM_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "ethereum")

async def async_main():
    from pump_fun_sniper import PumpFunSniper

    # Карта покрытия радаров: видно, какие парсеры в эфире, а какие спят
    # (нет бесплатного ключа / выкл флагом / забанены). Без этого слепые
    # зоны выглядят как "ракет нет", хотя их просто никто не смотрит.
    try:
        import radar_coverage as _rc
        _rc.print_coverage()
    except Exception as _e:
        print(f"⚠️ coverage report err: {_e}")

    # Метка РЕЖИМА: какие пороги крутят этот деплой. Сделки до/после деплоя
    # сравнивать нельзя — метка в логе делит эры (тихая неделя начинается тут).
    try:
        import time as _tt
        print(f"🏷️ РЕЖИМ { _tt.strftime('%Y-%m-%d %H:%M UTC', _tt.gmtime())}: "
              f"STOP {config.STOP_LOSS_PCT} TRAIL {config.TRAILING_ACTIVATION_PCT}/{config.TRAILING_DISTANCE_PCT} "
              f"MOONBAG {config.MOONBAG_TRIGGER_PCT} LIQ {getattr(config, 'MIN_LIQUIDITY', '?')} "
              f"CURVE_MIN_LIQ ${getattr(config, 'CURVE_MIN_LIQ_USD', 0):.0f} "
              f"UNGRAD {getattr(config, 'ALLOW_UNGRADUATED_PUMPFUN', '?')} "
              f"SHADOW_LIM {getattr(config, 'SHADOW_WATCH_LIMIT', '?')} "
              f"VETO {getattr(config, 'ROCKET_VETO_MIN', '?')}@{getattr(config, 'ROCKET_VETO_ENABLED', '?')} "
              f"TG_MAX {getattr(config, 'TG_MAX_SIZE_USD', '?')}/VIP {getattr(config, 'TG_VIP_SIZE_USD', '?')} "
              f"ROB_MAX {getattr(config, 'ROB_MAX_SIZE_USD', '?')} "
              f"FOMO {getattr(config, 'USE_FOMO_SIGNALS', '?')} COPY {getattr(config, 'USE_COPYTRADE', '?')} "
              f"STREAM {getattr(config, 'SNIPER_ENTRIES_ENABLED', '?')}")
    except Exception as _e:
        print(f"⚠️ regime line err: {_e}")

    try:
        from grok_jev import grok_jev, grok_bot_jev_loop
        _ai_provider = "Groq (llama-3.3-70b)" if grok_jev.groq_key else ("xAI (grok-beta)" if grok_jev.xai_key else ("Gemini" if grok_jev.gemini_key else "Local-JEV Algorithmic"))
        print(f"🤖 GROK BOT x JEV ENGINE: АКТИВЕН (AI Provider: {_ai_provider})")
        print(f"🎯 СЕТИ В РАБОТЕ: ТОЛЬКО SOLANA И BASE (100% концентрация капитала и лимитов позиций)")
    except Exception as _jeve:
        print(f"⚠️ jev banner err: {_jeve}")

    # Keep-Alive задача, чтобы Render не засыпал (работает в фоне)
    async def keep_alive():
        import aiohttp, os
        port = int(os.environ.get("PORT", 10000))
        url = os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}")
        print(f"🔄 Keep-Alive URL: {url}")
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(600)  # Каждые 10 минут
                try:
                    async with session.get(url) as resp:
                        print(f"💓 Keep-Alive Ping: {resp.status}")
                except Exception as e:
                    pass
    asyncio.create_task(keep_alive())

    from trade_logger import trade_logger
    from shadow_tracker import shadow_tracker
    from birdeye_scanner import birdeye_loop
    from jup_discovery import jup_loop
    from sol_price import get_sol_price
    from evm_wss import evm_wss_loop
    try:
        from fomo_api import fomo_api_loop
    except Exception as e:
        print(f"⚠️ fomo_api недоступен ({e}) - пропускаю")
        async def fomo_api_loop(*_a, **_k):
            return
    try:
        from tg_listener import tg_listener_loop
    except Exception as e:
        print(f"⚠️ tg_listener недоступен ({e}) - пропускаю (есть tg_preview без ключей)")
        async def tg_listener_loop(*_a, **_k):
            return
    from tg_preview import tg_preview_loop
    from smart_money_hunter import smart_money_hunter_loop
    try:
        from gmgn_bridge import gmgn_bridge_loop
    except Exception as e:
        print(f"⚠️ gmgn_bridge недоступен ({e}) - пропускаю (нужен playwright+chromium)")
        async def gmgn_bridge_loop(*_a, **_k):
            return
    
    # Получаем актуальную цену SOL при старте
    await get_sol_price()
    
    analyzer = Analyzer()
    tracker = PaperTracker()
    copy_trader = CopyTrader(tracker, analyzer)
    sniper = PumpFunSniper(tracker, copy_trader=copy_trader)
    
    async def sol_price_updater():
        """Обновляет цену SOL каждые 5 минут"""
        while True:
            await get_sol_price()
            await asyncio.sleep(300)
    
    async def _supervised(coro_fn, name: str):
        while True:
            try:
                print(f"🟢 [SYSTEM-LOOP] Старт {name}...", flush=True)
                await coro_fn()
                print(f"ℹ️ [SYSTEM-LOOP] {name} штатно завершён.", flush=True)
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"🚨 [LOOP-CRASH] Ошибка в {name}: {e}. Авто-перезапуск через 5с...", flush=True)
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)
            except BaseException as be:
                print(f"🚨 [LOOP-FATAL] Критический сбой в {name}: {be}. Авто-перезапуск через 5с...", flush=True)
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

    # Важно: PumpPortal WSS разрешает ТОЛЬКО ОДНО соединение с одного IP.
    # Второй сокет (early_scout_loop в shadow-режиме) перебивал подписки subscribeTokenTrade у PumpFunSniper!
    _core_loops = [
        _supervised(lambda: position_manager_loop(analyzer, tracker), "position_manager_loop"),
        _supervised(lambda: position_security_auditor_loop(analyzer, tracker), "position_security_auditor_loop"),
        _supervised(lambda: scanner_loop(analyzer, tracker), "scanner_loop"),
        _supervised(lambda: onchain_migration_feeder_loop(analyzer), "onchain_migration_feeder"),
        _supervised(lambda: discovery_feeder_loop(analyzer), "discovery_feeder_loop"),
        _supervised(lambda: copy_trader.listen(), "copy_trader"),
        _supervised(lambda: fomo_signal_loop(analyzer, tracker), "fomo_signal_loop"),
        _supervised(lambda: tg_listener_loop(analyzer, tracker), "tg_listener_loop"),
        _supervised(lambda: tg_preview_loop(analyzer, tracker), "tg_preview_loop"),
        _supervised(lambda: fomo_loop(analyzer, tracker), "fomo_loop"),
        _supervised(lambda: birdeye_loop(analyzer, tracker), "birdeye_loop"),
        _supervised(lambda: jup_loop(analyzer, tracker), "jup_loop"),
        _supervised(lambda: fomo_api_loop(analyzer, tracker), "fomo_api_loop"),
        _supervised(lambda: gmgn_bridge_loop(analyzer, tracker), "gmgn_bridge_loop"),
        _supervised(lambda: sniper.connect_and_listen(), "pump_fun_sniper"),
        _supervised(lambda: smart_money_hunter_loop(analyzer, tracker), "smart_money_hunter_loop"),
        _supervised(lambda: grok_bot_jev_loop(tracker, analyzer=analyzer), "grok_bot_jev_loop"),
        _supervised(lambda: trade_logger.post_trade_watcher_loop(), "trade_logger"),
        _supervised(lambda: shadow_tracker.price_watcher_loop(), "shadow_tracker"),
        _supervised(lambda: rugpull_feeder_loop(), "rugpull_feeder_loop"),
        _supervised(lambda: sol_price_updater(), "sol_price_updater"),
    ]
    if not getattr(config, "SOLANA_ONLY", True):
        _core_loops.extend([
            _supervised(lambda: base_loop(analyzer, tracker), "base_loop"),
            _supervised(lambda: evm_wss_loop(analyzer, tracker), "evm_wss_loop"),
        ])
    if getattr(config, "GROWTH_ENABLED", False):
        _core_loops.append(_supervised(lambda: growth_loop(analyzer, tracker), "growth_loop"))

    await asyncio.gather(*_core_loops, return_exceptions=True)

def run_background_bot():
    """Запускает асинхронный цикл в отдельном потоке с вечным авто-перезапуском при любых сбоях"""
    import traceback
    while True:
        loop = None
        try:
            print("🚀 [SYSTEM] Фоновый торговый бот запущен...", flush=True)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(async_main())
        except Exception as e:
            print(f"🚨 [CRITICAL CRASH] Фоновый бот аварийно завершился: {e}. Перезапуск через 3с...", flush=True)
            traceback.print_exc()
        except BaseException as be:
            print(f"🚨 [FATAL CRASH] Критический сбой потока бота: {be}. Перезапуск через 3с...", flush=True)
            traceback.print_exc()
        finally:
            if loop and not loop.is_closed():
                try:
                    loop.close()
                except Exception:
                    pass
        time.sleep(3)

import os
# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===


st.set_page_config(page_title="PhantBot Dashboard", layout="wide")

# Запускаем торгового бота ровно один раз при старте сервера
@st.cache_resource
def start_bot():
    thread = threading.Thread(target=run_background_bot, daemon=True)
    thread.start()
    return thread

# Автозапуск фонового бота: под Streamlit или на сервере Render
try:
    _is_streamlit = st.runtime.exists()
except Exception:
    _is_streamlit = False

if _is_streamlit or __name__ == "__main__" or os.environ.get("RENDER"):
    bot_thread = start_bot()

def chain_badge(chain: str, long: bool = False) -> str:
    """Бейдж сети для дашборда: 🟢 SOL / 🟣 ROB / 🟦 BASE."""
    c = str(chain or "solana")
    if c == "robinhood":
        return "🟣 ROB" if long else "🟣"
    if c == "base":
        return "🟦 BASE" if long else "🟦"
    if c == "bsc":
        return "🟨 BSC" if long else "🟨"
    if c == "ethereum":
        return "⬜ ETH" if long else "⬜"
    return "🟢 SOL" if long else "🟢"


def load_dashboard_portfolio():
    """Портфель для дашборда: слияние Supabase + локальный файл.
    Раньше облако было «главным источником правды»: если апсерт лагал,
    открытые сделки пропадали с экрана (хотя бот их вёл). Теперь ни один
    источник не может спрятать сделку — merge_states берёт объединение."""
    cloud, local = {}, {}
    try:
        url = getattr(config, 'SUPABASE_URL', None)
        key = getattr(config, 'SUPABASE_KEY', None)
        if url and key:
            from supabase import create_client
            sb = create_client(url, key)
            _snap_key = getattr(config, "PORTFOLIO_SNAPSHOT_MINT", "PORTFOLIO_STATE_V3")
            res = sb.table("trades_pump").select("features").eq("mint", _snap_key).execute()
            if res.data and res.data[0].get("features"):
                cloud = json.loads(res.data[0]["features"]) or {}
    except Exception as e:
        print(f"⚠️ Дашборд: не удалось прочитать портфель из Supabase: {e}")
    try:
        with open(config.PAPER_PORTFOLIO_FILE, 'r') as f:
            local = json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        print(f"⚠️ Дашборд: не удалось прочитать локальный портфель: {e}")
    try:
        from tracker import PaperTracker
        return PaperTracker.merge_states(cloud, local)
    except Exception:
        return cloud or local

# Отрисовка интерфейса
st.title("🚀 PhantBot - Alpha Agent Dashboard")
st.markdown("Панель управления алгоритмическим ботом.")

tab1, tab2 = st.tabs(["📊 Портфель и История", "📡 Радар Рынка (Alpha Scanner)"])

_frag_dec = getattr(st, "fragment", getattr(st, "experimental_fragment", None))
st.session_state.setdefault("auto_refresh_enabled", False)

def _render_tab1_content():
    # Кнопки управления портфелем
    _b_col1, _b_col2 = st.columns([1, 1])
    with _b_col1:
        if st.button("🔄 Обновить портфель", key="btn_refresh_portfolio"):
            st.rerun()
    with _b_col2:
        if st.button("🗑️ Очистить историю (С чистого листа)"):
            try:
                with open(config.PAPER_PORTFOLIO_FILE, 'w') as f:
                    json.dump({}, f)
            except Exception:
                pass
            try:
                url = getattr(config, 'SUPABASE_URL', None)
                key = getattr(config, 'SUPABASE_KEY', None)
                if url and key:
                    from supabase import create_client
                    sb = create_client(url, key)
                    _snap_key = getattr(config, "PORTFOLIO_SNAPSHOT_MINT", "PORTFOLIO_SOLANA_V2")
                    sb.table("trades_pump").upsert({
                        "mint": _snap_key,
                        "features": json.dumps({}),
                        "confidence": 0,
                        "status": "SYSTEM"
                    }).execute()
            except Exception:
                pass
            st.success("✨ Портфель очищен! Начинаем торговлю с чистого листа.")
            st.rerun()
        
    try:
        data = load_dashboard_portfolio()
            
        if data:
            df = pd.DataFrame.from_dict(data, orient='index')
            open_df = df[df['status'] == 'open'].copy()
            closed_df = df[df['status'] == 'closed'].copy()
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("🟢 Открытые позиции")
                if not open_df.empty:
                    # % считаем от цен (иммунно к искажению amount после partials),
                    # кап ±9999%: мусорный entry ~0 давал +586М% как у SOLFROG.
                    def _pct(r):
                        try:
                            e = float(r.get('entry_price_usd') or 0)
                            c = float(r.get('current_price_usd') or 0)
                            if e > 0 and c >= 0:
                                return max(-99.99, min(9999.0, (c / e - 1) * 100))
                        except Exception:
                            pass
                        return 0.0
                    open_df['pnl_%'] = open_df.apply(_pct, axis=1)
                    for index, row in open_df.iterrows():
                        _floating_pnl = float(row.get('current_pnl_usd') or 0.0)
                        _realized_pnl = float(row.get('pnl_usd') or 0.0)
                        _orig_amt = float(row.get('original_amount_usd') or row.get('amount_usd') or 0.0)
                        _cur_amt = float(row.get('amount_usd') or 0.0)
                        _max_pnl_val = float(row.get('max_pnl_pct') or 0.0)

                        # Совокупный PnL (зафиксированный профит от TP1/TP2 + плавающий остаток в рынке)
                        if _realized_pnl != 0.0:
                            pnl_usd = _realized_pnl + _floating_pnl
                            pnl_pct = (pnl_usd / _orig_amt * 100) if _orig_amt > 0 else row['pnl_%']
                        else:
                            pnl_usd = _floating_pnl
                            pnl_pct = row['pnl_%']

                        # Слепая позиция (sBTC-кейс): ни один источник цены минт
                        # не проиндексировал — вместо лживых $0.00 показываем факт.
                        try:
                            _e = float(row.get('entry_price_usd') or 0)
                            _c = float(row.get('current_price_usd') or 0)
                            _mx = float(row.get('max_price_usd') or 0)
                            _upd = float(row.get('price_updated_at') or 0)
                            _fz = float(row.get('first_zero_ts') or 0)
                            _blind = (_e > 0 and _c == _e and _mx == _e
                                      and (not _upd or _fz > 0))
                        except Exception:
                            _blind = False
                        color = "#00C851" if pnl_usd >= 0 else "#FF4444"
                        sign = "+" if pnl_usd > 0 else ""

                        _tp_detail_html = ""
                        if _realized_pnl > 0.005:
                            _fl_color = "#00C851" if _floating_pnl >= 0 else "#FF4444"
                            _fl_sign = "+" if _floating_pnl > 0 else ""
                            _tp_detail_html = (
                                f"<div style='font-size:0.75em; color:#aaa; margin-top:2px;'>"
                                f"Зафиксировано TP: <b style='color:#00C851;'>+${_realized_pnl:.2f}</b> | "
                                f"В рынке: <b style='color:{_fl_color};'>{_fl_sign}${_floating_pnl:.2f}</b>"
                                f"</div>"
                            )

                        _right = (f"<h3 style='margin:0; color: #888;'>⏳ нет цены</h3>"
                                  if _blind else
                                  f"<h3 style='margin:0; color: {color};'>{sign}${pnl_usd:.2f} ({sign}{pnl_pct:.2f}%)</h3>{_tp_detail_html}")

                        _is_mb = bool(row.get('is_moonbag', False) or _cur_amt < (_orig_amt * 0.75) or _realized_pnl > 0.005)
                        _mb_html = " <span style='background:#6f42c1; color:white; padding:2px 7px; border-radius:4px; font-size:0.75em; font-weight:bold;'>🚀 Moonbag (Фиксация TP)</span>" if _is_mb else ""
                        _size_html = f"<div><span style='color:#888;'>Ордер:</span> <b>${_orig_amt:.1f}</b>" + (f" (В рынке: <b>${_cur_amt:.1f}</b>)" if _is_mb else "") + f"{_mb_html}</div>"

                        # Подпись: сколько времени назад обновлялась цена токена
                        try:
                            _last_ts = max(
                                float(row.get('price_checked_at') or 0.0),
                                float(row.get('price_updated_at') or 0.0),
                                float(row.get('wss_price_ts') or 0.0)
                            )
                            if _last_ts > 0:
                                _diff = int(max(0, time.time() - _last_ts))
                                if _diff < 60:
                                    _age_str = f"{_diff} с назад"
                                elif _diff < 3600:
                                    _age_str = f"{_diff // 60}м {_diff % 60}с назад"
                                else:
                                    _age_str = f"{_diff // 3600}ч {(_diff % 3600) // 60}м назад"
                                
                                if _diff <= 15:
                                    _age_color = "#00C851"
                                elif _diff <= 60:
                                    _age_color = "#FFA500"
                                else:
                                    _age_color = "#FF4444"
                                _updated_badge = f"<div style='margin-top: 6px; font-size: 0.8em; color: {_age_color}; font-weight: 500;'>⏱ цена обновлена {_age_str}</div>"
                            else:
                                _updated_badge = "<div style='margin-top: 6px; font-size: 0.8em; color: #888;'>⏱ цена ещё не обновлялась</div>"
                        except Exception:
                            _updated_badge = ""

                        _fcr_v = str((row.get('ml_features') or {}).get('fcr_verdict') or '') if isinstance(row.get('ml_features'), dict) else ''
                        _fcr_badge = f"<div style='margin-top: 6px; font-size: 0.78em;'><span style='color:#888;'>🔍 FCR: </span><b style='color:#00C851;'>{_fcr_v}</b></div>" if _fcr_v else ""

                        _src_open_s = str(row.get('source', '') or '').strip()
                        _is_rh_open = 'RUG-HUNTER' in _src_open_s.upper()
                        _src_open_badge = (
                            f"<span style='background:#FF8F00; color:#111; padding:2px 7px; border-radius:4px; font-weight:bold;'>{_src_open_s}</span>"
                            if _is_rh_open else
                            f"🔖 {_src_open_s if _src_open_s else '—'}"
                        )
                        card_html = (
                            f"<div style='background-color: #1E1E1E; padding: 15px; border-radius: 8px; border-left: 5px solid {color}; margin-bottom: 10px; font-family: sans-serif;'>"
                            f"<div style='display: flex; justify-content: space-between; align-items: center;'>"
                            f"<h3 style='margin:0; color: #FFF;'>{row['symbol'] if str(row['symbol']).strip() else row['mint'][:6] + '...'}</h3>"
                            f"{_right}"
                            f"</div>"
                            f"<div style='margin-top: 6px; font-size: 0.75em; color: #888;'>{_src_open_badge} {chain_badge(row.get('chain', 'solana'), True)}</div>"
                            f"<div style='margin-top: 6px; font-size: 0.85em; color: #DDD;'>{_size_html}</div>"
                            f"{_updated_badge}"
                            f"{_fcr_badge}"
                            f"<div style='display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.85em; color: #BBB;'>"
                            f"<div><span style='color:#888;'>Вход:</span><br>${row['entry_price_usd']:.8f}</div>"
                            f"<div><span style='color:#888;'>Сейчас:</span><br>${row['current_price_usd']:.8f}</div>"
                            f"<div><span style='color:#888;'>Пик:</span><br>${row['max_price_usd']:.8f}</div>"
                            f"</div>"
                            f"</div>"
                        )
                        st.markdown(card_html, unsafe_allow_html=True)
                else:
                    st.info("Нет активных сделок.")
                    
            with col2:
                st.subheader("📓 История сделок")
                if not closed_df.empty:
                    # Фильтр фантомов (NUTFLEX +$1.6M при +14%): stored pnl обязан
                    # биться с ценовым движением, иначе это битые единицы цены.
                    def _is_bad(r):
                        try:
                            e = float(r.get('entry_price_usd') or 0)
                            x = float(r.get('exit_price_usd') or 0)
                            p = float(r.get('pnl_usd') or 0)
                            a = float(r.get('original_amount_usd') or r.get('amount_usd') or 0)
                            if not e or a <= 0 or a > 1000:
                                return abs(p) > 100
                            move = abs(x / e - 1) if e > 0 and x >= 0 else 1.0
                            exp_max = a * (move + 0.05) + 2.0
                            return abs(p) > exp_max * 3 and abs(p) > 50
                        except Exception:
                            return False
                    _bad_mask = closed_df.apply(_is_bad, axis=1)
                    _bad_n = int(_bad_mask.sum())
                    _clean = closed_df[~_bad_mask]
                    total_pnl = _clean['pnl_usd'].sum() if not _clean.empty else 0.0
                    if _bad_n:
                        st.warning(f"⚠️ { _bad_n} бит. сделок исключено из тотала (фантом единиц цены, см. логи Suspicious).")
                    closed_df = _clean
                    st.markdown(f"""
                    <div style='background-color: #2D2D2D; padding: 20px; border-radius: 10px; text-align: center; margin-bottom: 10px;'>
                        <div style='color: #888; font-size: 1.1em; text-transform: uppercase;'>Общий PnL</div>
                        <h1 style='margin: 0; color: {"#00C851" if total_pnl >= 0 else "#FF4444"};'>
                            {"+" if total_pnl >= 0 else ""}${total_pnl:.2f}
                        </h1>
                    </div>
                    """, unsafe_allow_html=True)

                    # Раздельная статистика: Основная стратегия vs 🎯 Rug-Hunter
                    try:
                        _src_col = closed_df['source'].fillna('').astype(str).str.upper() if 'source' in closed_df.columns else pd.Series([''] * len(closed_df), index=closed_df.index)
                        _rh_mask = _src_col.str.contains('RUG-HUNTER', na=False)
                        _main_df = closed_df[~_rh_mask]
                        _rh_df = closed_df[_rh_mask]
                        _m_pnl = float(_main_df['pnl_usd'].sum()) if not _main_df.empty else 0.0
                        _m_n = len(_main_df)
                        _m_wr = (float((_main_df['pnl_usd'] > 0).sum()) / _m_n * 100.0) if _m_n > 0 else 0.0
                        _rh_pnl = float(_rh_df['pnl_usd'].sum()) if not _rh_df.empty else 0.0
                        _rh_n = len(_rh_df)
                        _rh_wr = (float((_rh_df['pnl_usd'] > 0).sum()) / _rh_n * 100.0) if _rh_n > 0 else 0.0
                        st.markdown(f"""
                        <div style='display: flex; gap: 8px; margin-bottom: 14px;'>
                            <div style='flex: 1; background: #222; border-radius: 8px; padding: 10px; border-top: 3px solid #00C851; text-align: center;'>
                                <div style='font-size: 0.75em; color: #AAA;'>🛡️ Основная (чистые)</div>
                                <div style='font-size: 1.1em; font-weight: bold; color: {"#00C851" if _m_pnl >= 0 else "#FF4444"};'>{"+" if _m_pnl >= 0 else ""}${_m_pnl:.2f}</div>
                                <div style='font-size: 0.72em; color: #777;'>{_m_n} сд. | WR {_m_wr:.0f}%</div>
                            </div>
                            <div style='flex: 1; background: #222; border-radius: 8px; padding: 10px; border-top: 3px solid #FF8F00; text-align: center;'>
                                <div style='font-size: 0.75em; color: #FFB300;'>🎯 Rug-Hunter</div>
                                <div style='font-size: 1.1em; font-weight: bold; color: {"#00C851" if _rh_pnl >= 0 else "#FF4444"};'>{"+" if _rh_pnl >= 0 else ""}${_rh_pnl:.2f}</div>
                                <div style='font-size: 0.72em; color: #777;'>{_rh_n} сд. | WR {_rh_wr:.0f}%</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                    except Exception:
                        pass
                    
                    if closed_df.empty:
                        st.info("История пуста (все сделки отфильтрованы как битые).")
                    else:
                        if 'exit_time' in closed_df.columns:
                            closed_df['exit_time_num'] = pd.to_numeric(closed_df['exit_time'], errors='coerce').fillna(0)
                            closed_df = closed_df.sort_values(by='exit_time_num', ascending=False).head(15)
                        else:
                            closed_df = closed_df.tail(15).iloc[::-1] # Показываем 15 последних в обратном порядке
                        # % честный: pnl/original_amount (учитывает partials+комиссии),
                        # фолбэк — от цен. Кап ±9999%.
                        def _cpct(r):
                            try:
                                a0 = float(r.get('original_amount_usd') or r.get('amount_usd') or 0)
                                if a0 >= 0.01:
                                    return max(-99.99, min(9999.0, float(r.get('pnl_usd') or 0) / a0 * 100))
                            except Exception:
                                pass
                            try:
                                e = float(r.get('entry_price_usd') or 0)
                                x = float(r.get('exit_price_usd') or 0)
                                if e > 0 and x >= 0:
                                    return max(-99.99, min(9999.0, (x / e - 1) * 100))
                            except Exception:
                                pass
                            return 0.0
                        closed_df['pnl_%'] = closed_df.apply(_cpct, axis=1)

                        def _held(r):
                            # Сколько сделка прожила: отвечает на «открылась и пропала».
                            try:
                                e = float(r.get('entry_time') or 0)
                                x = float(r.get('exit_time') or 0)
                                if e > 0 and x > e:
                                    s = x - e
                                    if s < 3600:
                                        return "⏱ в рынке %dм" % int(s // 60)
                                    return "⏱ в рынке %.1fч" % (s / 3600)
                            except Exception:
                                pass
                            return ""
                        closed_df['held'] = closed_df.apply(_held, axis=1)

                        for index, row in closed_df.iterrows():
                            p_usd = row['pnl_usd']
                            p_pct = row['pnl_%']
                            c_color = "#00C851" if p_usd >= 0 else "#FF4444"
                            c_sign = "+" if p_usd > 0 else ""

                            _src_cl_s = str(row.get('source', '') or '').strip()
                            _is_rh_cl = 'RUG-HUNTER' in _src_cl_s.upper()
                            _src_cl_html = (
                                f"<span style='background:#FF8F00; color:#111; padding:1px 6px; border-radius:4px; font-weight:bold;'>{_src_cl_s}</span>"
                                if _is_rh_cl else
                                f"🔖 {_src_cl_s if _src_cl_s else '—'}"
                            )
                            closed_card_html = (
                                f"<div style='background-color: #1A1A1A; padding: 10px 15px; border-radius: 6px; border-right: 4px solid {c_color}; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;'>"
                                f"<div>"
                                f"<div style='color: #FFF; font-weight: bold;'>{row['symbol'] if str(row['symbol']).strip() else row.name[:6] + '...'} <span style='font-size: 0.7em; color: #888;'>{chain_badge(row.get('chain', 'solana'))}</span></div>"
                                f"<div style='color: #666; font-size: 0.75em;'>{row.get('exit_reason', 'Closed')} {row.get('held', '')}</div>"
                                f"<div style='color: #555; font-size: 0.7em; margin-top: 2px;'>{_src_cl_html}</div>"
                                f"</div>"
                                f"<div style='text-align: right; color: {c_color}; font-weight: bold;'>"
                                f"{c_sign}${p_usd:.2f} <br> <span style='font-size: 0.8em;'>({c_sign}{p_pct:.2f}%)</span>"
                                f"</div>"
                                f"</div>"
                            )
                            st.markdown(closed_card_html, unsafe_allow_html=True)
                else:
                    st.info("История пуста.")
        else:
            st.info("Файл портфеля пуст.")
    except FileNotFoundError:
        st.info("Бот еще не совершил первую сделку.")

_auto_refresh_on = bool(st.session_state.get("auto_refresh_enabled", False))
if _frag_dec and _auto_refresh_on:
    _render_tab1_content = _frag_dec(run_every="2s")(_render_tab1_content)

with tab1:
    _render_tab1_content()

with tab2:
    st.subheader("🔥 Последние проанализированные токены (Alpha Scanner)")
    st.markdown("Здесь отображаются монеты, которые бот сканирует прямо сейчас, с расчетом рейтинга в стиле **MemeSniper / GMGNAI**.")

    # Метки покупок: какие монеты уже в портфеле (открыты) или были в сделках (закрыты)
    try:
        _pf = data
    except NameError:
        _pf = load_dashboard_portfolio()
    _pf = _pf or {}
    bought_open, bought_closed = set(), set()
    for _k, _v in _pf.items():
        if not isinstance(_v, dict):
            continue
        _m = _v.get("mint", _k)
        if _v.get("status") == "open":
            bought_open.add(_k)
            bought_open.add(_m)
        elif _v.get("status") == "closed":
            bought_closed.add(_k)
            bought_closed.add(_m)
    # Архивные ключи вида mint_old_ts тоже считаем
    def _is_bought(mint: str, pool: set) -> bool:
        if mint in pool:
            return True
        return any(k.startswith(mint + "_old_") or k == mint for k in pool)
    
    try:
        with open("scanned_tokens.json", 'r') as f:
            scanned_data = json.load(f)
            
        if scanned_data:
            for t in scanned_data:
                score = t.get('score', 0)
                color = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
                _mint = t.get('mint', '')
                if _is_bought(_mint, bought_open):
                    _badge = "<div style='margin-top:8px; font-weight:bold; color:#00C851;'>✅ КУПЛЕН — позиция открыта</div>"
                elif _is_bought(_mint, bought_closed):
                    _badge = "<div style='margin-top:8px; font-weight:bold; color:#888;'>⚪ БЫЛА СДЕЛКА — уже закрыта</div>"
                else:
                    _badge = ""
                
                _src_up = str(t.get('source', '')).upper()
                if str(_mint).startswith("0x"):
                    _ch_lbl = "🟨 BSC" if "BSC" in _src_up else ("🟣 ROB" if "ROB" in _src_up else "🟦 BASE")
                    _addr_lbl = f"{_mint[:10]}..."
                else:
                    _ch_lbl = "🟢 SOL"
                    _addr_lbl = f"{_mint[:8]}...{'pump' if str(_mint).endswith('pump') else ''}"
                with st.container():
                    st.markdown(f"""
                    <div style='background-color: #1E1E1E; padding: 15px; border-radius: 10px; border-left: 5px solid {"#00C851" if score >= 70 else "#FF8800"}; margin-bottom: 10px;'>
                        <div style='display: flex; justify-content: space-between;'>
                            <h3 style='margin:0; color: #FFF;'>{t['symbol']} <span style='font-size: 0.6em; color: #888;'>{_ch_lbl} · {_addr_lbl}</span></h3>
                            <h3 style='margin:0; color: {"#00C851" if t.get("m5_change",0) > 0 else "#FF4444"};'>
                                {'+' if t.get("m5_change",0) > 0 else ''}{t.get('m5_change', 0):.1f}%
                            </h3>
                        </div>
                        <div style='display: flex; justify-content: space-between; margin-top: 10px; font-size: 0.9em;'>
                            <div style='color: #888;'>
                                <div style='font-size: 0.7em; text-transform: uppercase;'>Liquidity</div>
                                <div style='color: #DDD;'>${t.get('liquidity', 0):,.0f}</div>
                            </div>
                            <div style='color: #888;'>
                                <div style='font-size: 0.7em; text-transform: uppercase;'>24H Vol</div>
                                <div style='color: #DDD;'>${t.get('vol_24h', 0):,.0f}</div>
                            </div>
                            <div style='color: #888;'>
                                <div style='font-size: 0.7em; text-transform: uppercase;'>Buys/Sells (5m)</div>
                                <div><span style='color:#00C851;'>{t.get('buys', 0)}</span> / <span style='color:#FF4444;'>{t.get('sells', 0)}</span></div>
                            </div>
                        </div>
                        <div style='margin-top: 15px; border-top: 1px solid #333; padding-top: 10px; display: flex; gap: 15px; font-weight: bold; font-size: 0.9em;'>
                            <div style='color: {"#00C851" if score >= 70 else "#FF8800"};'>🧠 {score}</div>
                            <div style='color: {"#00C851" if t.get("safety",0) >= 70 else "#FF8800"};'>🛡️ {t.get('safety', 0)}</div>
                            <div style='color: {"#00C851" if t.get("momentum",0) >= 70 else "#FF8800"};'>⚡ {t.get('momentum', 0)}</div>
                            <div style='color: {"#00C851" if t.get("social",0) >= 70 else "#FF8800"};'>📣 {t.get('social', 0)}</div>
                        </div>
                        <div style='margin-top: 8px; font-size: 0.8em; color: #888;'>🔖 {t.get('source', 'WSS') if str(t.get('source', 'WSS')).strip() else 'WSS'} · {t.get('decision', '') if t.get('decision', '') else ''} {t.get('signal', '') if str(t.get('signal', '')).strip() else ''}</div>
                        {_badge}
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("Бот пока не проанализировал ни одной монеты.")
    except Exception as e:
        st.info("Ожидание данных от сканера...")

# Ручное и опциональное автообновление интерфейса (по умолчанию автообновление ВЫКЛЮЧЕНО)
_ref_col1, _ref_col2 = st.columns([1, 2])
with _ref_col1:
    if st.button("🔄 Обновить сайт", key="btn_manual_refresh_site"):
        st.rerun()
with _ref_col2:
    _auto_ref_toggle = st.checkbox(
        "Включить автообновление (каждые 2 сек)",
        value=_auto_refresh_on,
        key="auto_refresh_checkbox",
    )
    if _auto_ref_toggle != _auto_refresh_on:
        st.session_state["auto_refresh_enabled"] = bool(_auto_ref_toggle)
        st.rerun()
if _auto_refresh_on and not _frag_dec:
    time.sleep(2)
    st.rerun()

if __name__ == "__main__":
    # Если запущен напрямую (python3 main.py), держим процесс активным для терминала/демона
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
