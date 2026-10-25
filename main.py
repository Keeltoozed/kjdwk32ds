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
from tracker import PaperTracker
from fomo_scanner import fomo_loop

# === 1. ФОНОВЫЙ ТОРГОВЫЙ БОТ ===
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
                print(f"🛡️ [HEARTBEAT] Менеджер позиций активен: {_n_open} открытых позиций, баланс ${getattr(tracker, 'cash', 0.0):.2f}", flush=True)
            
            # ОПТИМИЗАЦИЯ СКОРОСТИ: Запрашиваем цены для ВСЕХ позиций ОДНИМ запросом с жестким таймаутом.
            mints_to_fetch = [m for m, p in open_positions.items()
                              if not m.startswith("0x")
                              and getattr(p, "chain", "solana") == "solana"]
            bulk_prices = {}
            if mints_to_fetch:
                try:
                    bulk_prices = await asyncio.wait_for(JupiterAPI.get_prices(mints_to_fetch), timeout=2.0)
                except Exception:
                    bulk_prices = {}
            
            for mint, position in list(open_positions.items()):
                # Robinhood (0x...) ведёт отдельный robinhood_loop со своими ценами DS
                if mint.startswith("0x") or getattr(position, "chain", "solana") != "solana":
                    continue

                # 1. Приоритет №1: Свежий WSS-стрим из PumpPortal (нулевая задержка, миллисекунды)
                # ВАЖНО: WSS pump.fun актуален ТОЛЬКО для немигрировавших токенов на внутренней кривой.
                # Для mature-токенов или токенов с DEX-парой (Raydium/Meteora/Pumpswap) цена ВСЕГДА из DEX/Jupiter/Gecko!
                wss_px = float(getattr(position, "wss_price", 0.0) or 0.0)
                wss_ts = float(getattr(position, "wss_price_ts", 0.0) or 0.0)
                _is_dex_token = getattr(position, "is_mature", False) or bool(getattr(position, "pair_address", ""))
                wss_fresh = (not _is_dex_token) and wss_px > 0.0 and (time.time() - wss_ts) < 8.0

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
                            _td0 = await asyncio.wait_for(_md.get_token_data(mint, max_cache_age=5.0), timeout=1.5)
                            api_price = float((_td0.get("priceUsd", 0) or 0)) if _td0 else 0.0
                        except Exception:
                            pass
                    if api_price > 0.0:
                        current_price = api_price
                    else:
                        # Если оба источника не дали цену в этом тике — держим последнюю известную (до 120 сек)
                        _last_known = float(getattr(position, "current_price_usd", 0.0) or 0.0)
                        _last_upd = float(getattr(position, "price_updated_at", 0.0) or getattr(position, "entry_time", 0.0) or 0.0)
                        if _last_known > 0.0 and (time.time() - _last_upd) < 120:
                            current_price = _last_known
                        else:
                            current_price = 0.0
                
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
                    # Если цена 0.0 больше 45 секунд — это рагпулл / слив ликвидности.
                    # Проверяем продаваемость через Jupiter: если продать нельзя — списание в 0.0$ (-100%).
                    if (time.time() - _fz) > 45:
                        from jupiter import JupiterAPI
                        _sell_ok, _sell_why = await JupiterAPI.is_token_sellable(mint)
                        if not _sell_ok:
                            print(f"🚨 [SOL-ZERO-RUG] {position.symbol} ({mint[:8]}): цена 0.00 и токен непродаваем ({_sell_why}) — списание в 0.0$ (-100%).")
                            tracker.close_position(mint, 0.0, f"Rug Pull / Unsellable ({_sell_why})")
                            continue
                        elif (time.time() - _fz) > 180:
                            tracker.close_position(mint, 0.0, "Rug Pull / No Liquidity")
                            continue
                    continue
                position.first_zero_ts = 0.0
                    
                # Фантом цены (смена источника DS->node, пыль с liq 0): один тик
                # +5000% — не пик, а битые данные. Пик не обновляем, трек не ломаем.
                if position.entry_price_usd > 0 and current_price / position.entry_price_usd > 50:
                    print(f"⚠️ Phantom price {mint[:8]}: x{current_price/position.entry_price_usd:.0f} за тик — игнор.")
                    continue
                if current_price > position.max_price_usd:
                    position.max_price_usd = current_price
                    position.peak_time = time.time()

                pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd
                max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd
                minutes_held = (time.time() - position.entry_time) / 60

                # Сразу обновляем текущие значения для дашборда и хранилища (чтобы continue ниже не морозили данные в UI)
                position.current_price_usd = current_price
                position.current_pnl_usd = position.amount_usd * pnl_pct

                _last_pos_log = getattr(position, "_last_log_ts", 0.0)
                if (_now_sec - _last_pos_log) >= 10.0:
                    position._last_log_ts = _now_sec
                    _pnl_str = f"{pnl_pct*100:+.1f}%" if position.entry_price_usd > 0 else "0%"
                    print(f"🛡️ [TRACK] {position.symbol} ({mint[:8]}): цена ${current_price:.8f} ({_pnl_str}) | пик: ${position.max_price_usd:.8f} | держим {minutes_held:.1f}м", flush=True)

                # --- АУДИТ БЕЗОПАСНОСТИ ОТКРЫТОЙ ПОЗИЦИИ (HONEYPOT / HARD SCAM GUARD) ---
                # 1. Защита от ложного списания в первые 45 секунд: токен только что куплен и прошел пре-аудит
                _last_sol_sec_chk = getattr(position, "last_security_check_ts", 0.0)
                if minutes_held >= 0.75 and (time.time() - _last_sol_sec_chk) > 35:
                    position.last_security_check_ts = time.time()
                    try:
                        from jupiter import JupiterAPI
                        _jup_ok, _jup_why = await JupiterAPI.is_token_sellable(mint)
                        if not _jup_ok:
                            _strikes = getattr(position, "unsellable_strikes", 0) + 1
                            position.unsellable_strikes = _strikes
                            if _strikes >= 2:
                                print(f"🚨 [SOL-SELL-BLOCKED] {position.symbol} ({mint[:10]}): {_jup_why} (подтверждено {_strikes}x) — токен непродаваем/рагпулл, списание в 0.0$ (-100%).")
                                tracker.close_position(mint, 0.0, f"SOL Rug Pull / Unsellable ({_jup_why})")
                                continue
                            else:
                                print(f"⚠️ [SOL-SELL-WARN] {position.symbol} ({mint[:10]}): {_jup_why} (страйк 1/2, проверка через 35с)")
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
                # Infant Dump (как в EVM): слив в первые минуты = дев, не шум.
                # Раги идут -40..-96%, шум импульса -15..-18% переживаем.
                try:
                    _inf_w = float(getattr(config, "INFANT_WINDOW_MIN", 2.0))
                    _inf_p = float(getattr(config, "INFANT_DUMP_PCT", -0.25))
                    if minutes_held < _inf_w and pnl_pct <= _inf_p:
                        tracker.close_position(mint, current_price, f"Infant Dump ({pnl_pct*100:.0f}%)")
                        continue
                except Exception:
                    pass
                
                peak_ts = getattr(position, "peak_time", position.entry_time)
                minutes_since_peak = (time.time() - peak_ts) / 60 if peak_ts else 0

                prev_ts = getattr(position, "price_checked_at", 0.0)
                # 1-тиковый фильтр аномального провала котировки (>45% за 1 тик при наличии прибыли >=15%):
                # защищает от ложного тика побочного пула (кейс MAKERS +213% -> -100.0% на 1 тик),
                # НЕ перезаписывая prev_price и НЕ пуская тик в Early Fade Cut до проверки 2-м тиком!
                if prev_ts > 0 and (time.time() - prev_ts) < 60 and prev_price > 0 \
                        and current_price <= prev_price * 0.78:
                    _cg_n = int(getattr(position, "crash_confirm_n", 0) or 0) + 1
                    position.crash_confirm_n = _cg_n
                    if _cg_n < 3:
                        print(f"⚠️ [SOL-TICK-VERIFY] {position.symbol} ({mint[:8]}): резкий 1-тик {pnl_pct*100:+.1f}% (-{(1 - current_price/prev_price)*100:.0f}%) — ждём 3-й тик подтверждения.")
                        continue
                    # ПЕРЕД ЗАКРЫТИЕМ ПО CRASH GUARD — ДВОЙНАЯ ПРОВЕРКА ЧЕРЕЗ JUPITER / DIRECT DEX:
                    # Защита от кейса NMS (фантомный прокол / сбой котировок при реальном пампе):
                    try:
                        from jupiter import JupiterAPI
                        _live_px_check = await JupiterAPI.get_price(mint)
                        if _live_px_check and _live_px_check > prev_price * 0.78:
                            print(f"🛡️ [CRASH-GUARD-RESCUED] {position.symbol} ({mint[:8]}): ложный краш-тик! Live DEX price ${_live_px_check:.6f} > prev ({(1 - _live_px_check/prev_price)*100:+.1f}%). Позиция спасена.")
                            current_price = _live_px_check
                            position.current_price_usd = _live_px_check
                            position.crash_confirm_n = 0
                            position.price_checked_at = time.time()
                            continue
                    except Exception:
                        pass
                    tracker.close_position(mint, current_price,
                                           f"Crash Guard (-{(1 - current_price/prev_price)*100:.0f}% за {(time.time()-prev_ts):.0f} сек)")
                    continue
                else:
                    position.crash_confirm_n = 0
                position.price_checked_at = time.time()
                
                # === ТАЙМАУТ ПОСЛЕ ИМПУЛЬСА (только для малых пиков +18..+35%, выше +35% ведёт Profit Floor и Trailing!) ===
                if minutes_held >= 6.0 and 0.18 <= max_pnl_pct < 0.35 and pnl_pct <= -0.08:
                    tracker.close_position(mint, current_price, f"Early Fade Cut (был пик +{max_pnl_pct*100:.0f}%, слив до {pnl_pct*100:.1f}%)")
                    continue
                if minutes_held >= 8.0 and 0.20 <= max_pnl_pct < 0.35 and minutes_since_peak >= 7 and pnl_pct <= 0.01:
                    tracker.close_position(mint, current_price, f"Post-Rocket Fade Cut ({minutes_since_peak:.0f}m after peak, pnl {pnl_pct*100:+.1f}%)")
                    continue

                # === STAGNANT EXIT (PRO: время по заслугам) ===
                # Не маринуем мертвые деньги на Solana: ни намёка на импульс за 4-8 мин — кат.
                _hold_min = float(getattr(config, "STAGNANT_HOLD_MIN", 8.0) or 8.0)
                _loss_min = (float(getattr(config, "STAGNANT_LOSS_MIN", 6.0) or 6.0)
                             if max_pnl_pct >= 0.05 else
                             float(getattr(config, "STAGNANT_LOSS_MIN_FAST", 4.0) or 4.0))
                if minutes_held >= _loss_min and pnl_pct <= -0.05 and max_pnl_pct < 0.05:
                    # ЖИВОЙ ФЛЕТ НЕ РЕЖЕМ: консолидация 5-15 мин перед выстрелом выглядит
                    # как -2%. Проверяем сделки за 5 мин: есть buys = пул жив, даём +5 мин
                    # (макс 2 грейса). Нет сделок = труп, режем. Ошибка API = пропуск цикла.
                    _graces = getattr(position, "stagnant_graces", 0)
                    try:
                        _td = await analyzer.fetch_token_data(mint)
                        if not _td:
                            continue
                        _tx5 = ((_td.get("txns") or {}).get("m5", {}) or {})
                        _alive = ((_tx5.get("buys", 0) or 0) + (_tx5.get("sells", 0) or 0)) >= 3
                    except Exception:
                        continue
                    if _alive and _graces < 2:
                        position.stagnant_graces = _graces + 1
                        position.entry_time = time.time() - 2 * 60
                        print(f"⏳ {mint[:8]}: живой флет ({pnl_pct*100:.1f}%), грейс {position.stagnant_graces}/2 — ждём выстрел.")
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
                # Profit lock удален, используется трейлинг-стоп из config.py
                
                # === СКАЛЬП-ТРЕЙЛИНГ (Забираем мелкие плюсы, если включено) ===
                if getattr(config, "SCALP_PROFIT_ENABLED", False) and max_pnl_pct >= 0.10 and max_pnl_pct < getattr(config, "TRAILING_ACTIVATION_PCT", 0.45):
                    drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                    if drop_from_max >= 0.05:
                        tracker.close_position(mint, current_price, f"Scalp Profit (peak +{max_pnl_pct*100:.0f}%)")
                        continue
                
                # Обновляем текущие значения для отображения в интерфейсе
                # (сохраняем один раз за цикл ниже, а не на каждой позиции,
                # чтобы не делать N записей в Supabase каждые 3 секунды)
                position.current_price_usd = current_price
                position.current_pnl_usd = position.amount_usd * pnl_pct
                position.price_checked_at = time.time()
                if current_price > 0:
                    position.price_updated_at = time.time()

                # === DEAD POOL & LIQUIDITY DRAIN GUARD (SOLANA) ===
                # Защита от замороженных/умерших пулов и фантомной прибыли (кейс DCA / NEX AI: ликва $0.02, 0 сделок в m5)
                _now_t = time.time()
                if (_now_t - getattr(position, "last_liq_chk", 0.0)) > 20:
                    position.last_liq_chk = _now_t
                    try:
                        import market_data as _md
                        _td_sol = await asyncio.wait_for(_md.get_token_data(mint, max_cache_age=10.0), timeout=1.5)
                        if _td_sol:
                            _sol_liq = float(((_td_sol.get("liquidity") or {}).get("usd", 0) or 0))
                            _sol_tx5 = (_td_sol.get("txns") or {}).get("m5", {}) or {}
                            _sol_txs = int(_sol_tx5.get("buys", 0) or 0) + int(_sol_tx5.get("sells", 0) or 0)
                            _sol_v5 = float(((_td_sol.get("volume") or {}).get("m5", 0) or 0))
                            _dex_id = str(_td_sol.get("dexId", "")).lower()

                            # 1. Ликвидность выведена/слита (< $800 в любом пуле после 1 минуты)
                            if minutes_held >= 1.0 and _sol_liq < 800:
                                print(f"🚨 [SOL-DEAD-POOL] {position.symbol} ({mint[:8]}): ликвидность ${_sol_liq:.0f} < $800 — пул высушен/умер, списание в 0.0$ (-100%).")
                                tracker.close_position(mint, 0.0, f"SOL Liquidity Drained (${_sol_liq:.0f} liq, pool dead)")
                                continue

                            # 2. Торги замерли: 0 сделок в m5 и объём 0 (застрявшая цена)
                            if minutes_held >= 2.5 and _sol_txs == 0 and _sol_v5 <= 0.01:
                                _z_cyc = int(getattr(position, "zero_vol_cycles", 0) or 0) + 1
                                position.zero_vol_cycles = _z_cyc
                                if _z_cyc >= 2:  # ~40-50 сек подтвержденной тишины в пуле
                                    from jupiter import JupiterAPI
                                    _jup_ok, _jup_why = await JupiterAPI.is_token_sellable(mint)
                                    if not _jup_ok:
                                        print(f"🚨 [SOL-DEAD-RUG] {position.symbol} ({mint[:8]}): 0 сделок и непродаваем ({_jup_why}) — списание в 0.0$ (-100%).")
                                        tracker.close_position(mint, 0.0, f"SOL Dead Pool / Unsellable Rug ({_jup_why})")
                                    else:
                                        tracker.close_position(mint, current_price, f"Dead Pool (0 txns in pool, frozen)")
                                    continue
                            else:
                                position.zero_vol_cycles = 0
                    except Exception as _e:
                        pass
                
                # === ИНТЕГРАЦИЯ МАТЕМАТИКИ ДЛЯ ЗРЕЛЫХ МОНЕТ (SWING TRADING) ===
                _tp1_sol_thr = float(getattr(config, "TP1_TRIGGER_PCT", 0.50))
                _tp1_sell = float(getattr(config, "TP1_SELL_FRAC", 0.20))
                _tp2 = float(getattr(config, "TP2_TRIGGER_PCT", 1.20))
                _tp2_sell = float(getattr(config, "TP2_SELL_FRAC", 0.25))
                _mb = float(getattr(config, "MOONBAG_TRIGGER_PCT", 2.50))
                _mb_sell = float(getattr(config, "MOONBAG_SELL_FRAC", 0.30))
                if getattr(position, "is_mature", False):
                    # TIER-1: частичная фиксация (если включено в конфиге)
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and max_pnl_pct >= _tp1_sol_thr and not getattr(position, "tp1_done", False):
                        tracker.partial_close_position(mint, current_price, _tp1_sell, f"Take Profit +{_tp1_sol_thr*100:.0f}% (Tier-1 {int(_tp1_sell*100)}%)")
                        position.tp1_done = True
                        position.is_moonbag = True
                        continue
                    # TIER-2: частичная фиксация (если включено в конфиге)
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and max_pnl_pct >= _tp2 and not getattr(position, "tp2_done", False):
                        tracker.partial_close_position(mint, current_price, _tp2_sell, f"Take Profit +{_tp2*100:.0f}% (2.2x Free Ride)")
                        position.tp2_done = True
                        continue
                    # MOONBAG: частичная фиксация (если включено в конфиге)
                    if getattr(config, "PARTIAL_TP_ENABLED", False) and max_pnl_pct >= _mb and not getattr(position, "is_moonbag", False):
                        tracker.partial_close_position(mint, current_price, _mb_sell, f"Take Profit +{_mb*100:.0f}% (Risk Free)")
                        position.is_moonbag = True
                        continue
                    from exit_managers import MatureExitManager
                    mature_exit_reason = MatureExitManager.evaluate_exit(position, current_price)
                    if mature_exit_reason:
                        tracker.close_position(mint, current_price, mature_exit_reason)
                    continue # Если это mature монета, скальперская логика ниже к ней не применяется!
                
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
                        
                # === TAKE-PROFIT LADDER (Лестница фиксации прибыли — только если включено) ===
                if getattr(config, "PARTIAL_TP_ENABLED", False) and max_pnl_pct >= _tp1_sol_thr and not getattr(position, "tp1_done", False):
                    tracker.partial_close_position(mint, current_price, _tp1_sell, f"Take Profit +{_tp1_sol_thr*100:.0f}% (Tier-1 {int(_tp1_sell*100)}%)")
                    position.tp1_done = True
                    position.is_moonbag = True
                    continue
                if getattr(config, "PARTIAL_TP_ENABLED", False) and max_pnl_pct >= _tp2 and not getattr(position, "tp2_done", False):
                    tracker.partial_close_position(mint, current_price, _tp2_sell, f"Take Profit +{_tp2*100:.0f}% (2.2x Free Ride)")
                    position.tp2_done = True
                    continue
                if getattr(config, "PARTIAL_TP_ENABLED", False) and max_pnl_pct >= _mb and not getattr(position, "is_moonbag", False):
                    tracker.partial_close_position(mint, current_price, _mb_sell, f"Take Profit +{_mb*100:.0f}% (Risk Free)")
                    position.is_moonbag = True
                    continue

                # 1.5 ГАРАНТИРОВАННЫЙ НЕСНИЖАЕМЫЙ ПОРОГ ПРИБЫЛИ (PROFIT FLOOR LOCK)
                # Включается только от +38% пика, чтобы не выбивать ракету на стартовом ретесте (+19% -> -1% -> +60% как у Chubby)
                _floor_pnl = None
                if max_pnl_pct >= 5.00:       # Гипер-ракета (+500%+): держим 75% прибыли (пик +692% -> пол +519%)
                    _floor_pnl = round(max_pnl_pct * 0.75, 2)
                elif max_pnl_pct >= 3.00:     # Мега-ракета (+300%..+500%): держим 72% прибыли (пик +400% -> пол +288%)
                    _floor_pnl = max(2.35, round(max_pnl_pct * 0.72, 2))
                elif max_pnl_pct >= 2.00:     # Пик +200%+ (3x) -> гарантируем минимум +165%
                    _floor_pnl = 1.65
                elif max_pnl_pct >= 1.50:     # Пик +150%+ (2.5x) -> гарантируем минимум +122%
                    _floor_pnl = 1.22
                elif max_pnl_pct >= 1.00:     # Пик +100%+ (2x) -> гарантируем минимум +80%
                    _floor_pnl = 0.80
                elif max_pnl_pct >= 0.70:     # Пик +70%+ -> гарантируем минимум +55%
                    _floor_pnl = 0.55
                elif max_pnl_pct >= 0.50:     # Пик +50%+ -> гарантируем минимум +32%
                    _floor_pnl = 0.32
                elif max_pnl_pct >= 0.38:     # Пик +38%+ -> гарантируем минимум +15%
                    _floor_pnl = 0.15
                if _floor_pnl is not None and pnl_pct <= _floor_pnl:
                    _fc_s = int(getattr(position, "floor_confirm_n", 0) or 0) + 1
                    position.floor_confirm_n = _fc_s
                    if _fc_s < 2:
                        continue
                    if pnl_pct >= 0:
                        tracker.close_position(mint, current_price, f"Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%)")
                    else:
                        tracker.close_position(mint, current_price, f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.0f}%)")
                    continue
                else:
                    position.floor_confirm_n = 0

                # 2. СУЖАЮЩИЙСЯ ПАРАБОЛИЧЕСКИЙ ТРЕЙЛИНГ-СТОП (Чем выше пик, тем плотнее зажимаем профит у вершины!)
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if max_pnl_pct >= 2.50:
                    trail_distance = 0.09
                elif max_pnl_pct >= 1.50:
                    trail_distance = 0.10
                elif max_pnl_pct >= 0.80 or getattr(position, "is_moonbag", False):
                    trail_distance = float(getattr(config, "TRAILING_RUNNER_DISTANCE_PCT", 0.12))
                else:
                    trail_distance = float(getattr(config, "TRAILING_DISTANCE_PCT", 0.16))
                if max_pnl_pct >= getattr(config, "TRAILING_ACTIVATION_PCT", 0.45):
                    if drop_from_max >= trail_distance:
                        tracker.close_position(mint, current_price, f"Trailing Stop (peak +{max_pnl_pct*100:.0f}%, drop {drop_from_max*100:.0f}%)")
                        continue

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

                # Breakeven / Soft Guard резерв (только после 6 минут, не режем стартовые свечи ретеста):
                if minutes_held >= 6.0 and max_pnl_pct >= 0.20 and pnl_pct <= -0.10:
                    tracker.close_position(mint, current_price, f"Soft Guard (был пик +{max_pnl_pct*100:.0f}%, слив до {pnl_pct*100:.1f}%)")
                    continue
                _be_sol = float(getattr(config, "BREAKEVEN_PCT", 0.28))
                if minutes_held >= 6.0 and max_pnl_pct >= _be_sol and pnl_pct <= 0.01:
                    tracker.close_position(mint, current_price, f"Breakeven (был пик +{max_pnl_pct*100:.0f}%)")
                    continue

                # Zero-Momentum Cut: монета за 3.5+ мин ни разу не дала даже +1.5% и ушла в -10%
                if max_pnl_pct < 0.015 and pnl_pct <= -0.10 and minutes_held >= 3.5:
                    tracker.close_position(mint, current_price, f"Zero-Momentum Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)")
                    continue

                # 3.2 GROK BOT x JEV: СТОП ПОД ФИТИЛЬ ("The stop lives under the wick")
                _wick_stop = getattr(position, "stop_under_wick", 0.0) or 0.0
                _wick_pct = getattr(position, "wick_stop_loss_pct", 0.0) or 0.0
                if minutes_held >= 0.5:
                    if _wick_stop > 0 and current_price <= _wick_stop:
                        tracker.close_position(mint, current_price, f"JEV Wick Stop (${current_price:.6f} <= ${_wick_stop:.6f}, {pnl_pct*100:.1f}%)")
                        continue
                    elif _wick_pct < 0 and pnl_pct <= _wick_pct:
                        tracker.close_position(mint, current_price, f"JEV Wick Stop ({pnl_pct*100:.1f}% <= {_wick_pct*100:.1f}%)")
                        continue
                            
                # 3. АВАРИЙНЫЙ КЭП: больше -30% одну сделку не держим.
                if pnl_pct <= -0.30:
                    tracker.close_position(mint, current_price, f"Emergency Cap ({pnl_pct*100:.0f}%)")
                    continue

                # 3.5 ЖЕСТКИЙ Stop Loss из config.py (после 1.5 мин, чтобы пережить стартовый спред)
                if minutes_held >= 1.5 and pnl_pct <= config.STOP_LOSS_PCT:
                    from jupiter import JupiterAPI
                    _j_ok, _j_why = await JupiterAPI.is_token_sellable(mint)
                    if not _j_ok:
                        print(f"🚨 [SOL-HARD-STOP-RUG] {position.symbol} ({mint[:8]}): слив {pnl_pct*100:.1f}% и токен непродаваем ({_j_why}) — списание в 0.0$ (-100%).")
                        tracker.close_position(mint, 0.0, f"Hard Stop RUG / Unsellable (-100%)")
                    else:
                        tracker.close_position(mint, current_price, f"Hard Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)")
                    continue
                    
                # 4. УМНЫЙ ВЫХОД ПО ВРЕМЕНИ (Stagnant / Dead Coin Cut для Solana)
                # Не маринуем капитал по 25-30 минут! Если за 8 минут монета не стреляет — выходим.
                _dead_hold = float(getattr(config, "STAGNANT_HOLD_MIN", 8.0) or 8.0)
                if minutes_held >= _dead_hold and pnl_pct < -0.04:
                    from jupiter import JupiterAPI
                    _j_ok, _j_why = await JupiterAPI.is_token_sellable(mint)
                    if not _j_ok:
                        print(f"🚨 [SOL-DEAD-RUG] {position.symbol} ({mint[:8]}): 8 мин без пампа и непродаваем ({_j_why}) — списание в 0.0$ (-100%).")
                        tracker.close_position(mint, 0.0, f"Dead Coin RUG / Unsellable (-100%)")
                    else:
                        tracker.close_position(mint, current_price, f"Dead Coin Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)")
                    continue
                    
                # Если монета болтается во флете больше 8 минут - выходим, освобождаем капитал
                if minutes_held >= _dead_hold and pnl_pct < 0.08:
                    from jupiter import JupiterAPI
                    _j_ok, _j_why = await JupiterAPI.is_token_sellable(mint)
                    if not _j_ok:
                        print(f"🚨 [SOL-STAGNANT-RUG] {position.symbol} ({mint[:8]}): флет и непродаваем ({_j_why}) — списание в 0.0$ (-100%).")
                        tracker.close_position(mint, 0.0, f"Stagnant RUG / Unsellable (-100%)")
                    else:
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
        # Балк-цены Jupiter+DS: 1 запрос на все позиции за цикл, квоту не жрёт.
        # 1.0 сек: максимальная скорость трекинга открытых позиций (без лагов на стопах и пиках).
        await asyncio.sleep(1.0)
from birth_tracker import BirthTracker, birth_tracker
from scan_pipeline import ScanWindow, MISSING as _SCAN_MISSING, apply_result as _scan_apply

async def birth_wss_loop(analyzer, tracker):
    import websockets
    import json
    print("👶 Запуск Роддома: сбор базы данных новых токенов для сканера (без авто-покупки на 0-секунде)...")

    async def _fallback_poll_births():
        """Фоновый сбор свежих токенов (страхует при сбоях PumpPortal WSS 502)."""
        from http_client import fetch_json
        while True:
            try:
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
            except Exception:
                pass
            await asyncio.sleep(6)

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
                payload = {"method": "subscribeNewToken"}
                await ws.send(json.dumps(payload))
                async for message in ws:
                    data = json.loads(message)
                    mint = data.get("mint")
                    if mint:
                        # Просто сохраняем монету в базу для scanner_loop (чтобы она "настоялась" 5-20 мин)
                        birth_tracker.add_token(mint)
        except Exception as e:
            # Не спамим при 502, фоновый поллер подхватывает новые токены
            await asyncio.sleep(15)

async def scanner_loop(analyzer, tracker):
    print("🚀 Запуск PhantBot Scanner (Поиск новых монет)...")
    processed_mints = {}
    _grad_none = {}  # mint -> число None от DS (скам/нестандарт: после 3 — хороним, не жрём циклы)
    _latest_cache = (0.0, [])   # (ts, tokens): кэш fetch_latest_tokens
    _last_idle_log = 0.0
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
                print(f"🔎 Сканируем монеты... (Открыто: {open_count}/{config.MAX_CONCURRENT_POSITIONS})")
                
                mints_to_scan = []
                # 1. Органика Solana ПЕРВЫМИ: GeckoTerminal Trending (5m + 1h + new_pools + pump.fun)
                try:
                    import market_data
                    trending_pools = await market_data.get_trending()
                    if trending_pools:
                        mints_to_scan.extend([p.get("tokenAddress") for p in trending_pools if p.get("tokenAddress")])
                except Exception as _e:
                    pass

                # 2. DexScreener бусты и профили (кэш 20 сек)
                _lt_ttl = float(getattr(config, "SCAN_LATEST_TTL_SEC", 20))
                if _latest_cache[0] and time.time() - _latest_cache[0] < _lt_ttl:
                    tokens = _latest_cache[1]
                else:
                    tokens = await analyzer.fetch_latest_tokens()
                    _latest_cache = (time.time(), tokens)
                if tokens:
                    mints_to_scan.extend([p.get("tokenAddress") for p in tokens if p.get("tokenAddress")])

                # 0. Миграции ПЕРВЫМИ: только что вышли из кёрва в Raydium-пул —
                # окно ракеты минуты, DS ещё может не проиндексировать (None → вернём в очередь).
                # Срез 25/цикл: при панике очередь не блочит VIP-разбор надолго.
                graduated_mints = []
                try:
                    graduated_mints = (birth_tracker.drain_graduated() or [])[:25]
                except Exception:
                    pass
                if graduated_mints:
                    print(f"🏁 Разбираю {len(graduated_mints)} свежих миграций ПЕРВЫМИ!")
                    mints_to_scan = graduated_mints + mints_to_scan
                _graduated_set = set(graduated_mints)
                
                # 2. Уличные Токены (Берем молодые ракеты от 5 до 20 минут)
                mature_mints = birth_tracker.get_mature_tokens(2, 30)  # Было 3-25: шире окно молодняка = больше ракет
                if mature_mints:
                    print(f"🎂 Найдено {len(mature_mints)} перспективных монет (возраст 5-20 минут)!")
                    mints_to_scan.extend(mature_mints)

                # 3. Ручной вотчлист юзера (увидел рано на fomo - бот исполняет по своим гейтам)
                for _w in getattr(config, "USER_WATCHLIST", []) or []:
                    if _w and _w not in mints_to_scan:
                        mints_to_scan.append(_w)

                # Удаляем дубликаты, отсекаем уже открытые позиции, порядок сохраняем (миграции остаются первыми)
                _open_mints_set = set(tracker.positions.keys())
                mints_to_scan = list(dict.fromkeys(m.strip() for m in mints_to_scan if m and m.strip() and m.strip() not in _open_mints_set))

                # ⚡ Пакетный префетч: вся очередь за ceil(N/30) запросов к DexScreener
                # вместо N одиночных. analyze_token() дальше читает из кэша без сети.
                try:
                    _pf_list = [m for m in mints_to_scan if m not in tracker.positions
                                and time.time() - processed_mints.get(m, 0.0) >= 25]
                    _pf_n = await analyzer.prefetch_token_data(_pf_list)
                    if _pf_list:
                        print(f"⚡ Префетч: {_pf_n}/{len(_pf_list)} токенов за {(len(_pf_list) + 29) // 30} запрос(а) DS.")
                except Exception as _pfe:
                    print(f"⚠️ prefetch err: {type(_pfe).__name__}")
                
                # ⚡ Сортировка по импульсу и фильтрация мертвых/старых токенов
                _SKIP_ZOMBIE_SYMBOLS = {
                    "RAY", "MSOL", "BSOL", "JITOSOL", "WBTC", "BTC", "WETH", "ETH",
                    "USDC", "USDT", "USDH", "DAI", "SOL", "WSOL", "ORE", "NEET", "GYAT"
                }
                _ranked_mints = []
                for mint in mints_to_scan:
                    if not mint or mint in tracker.positions:
                        continue
                    _c_pre = getattr(analyzer, "_prefetch_cache", {}).get(mint, (0, {}))[1]
                    if _c_pre and isinstance(_c_pre, dict):
                        _base = _c_pre.get("baseToken", {}) or {}
                        _sym = str(_base.get("symbol", "")).upper().strip()
                        if _sym in _SKIP_ZOMBIE_SYMBOLS:
                            processed_mints[mint] = time.time() + 86400
                            continue

                        _c_at = _c_pre.get("pairCreatedAt") or 0
                        _c_age_m = (time.time() * 1000 - _c_at) / 60000.0 if _c_at else 999.0
                        _c_pc = _c_pre.get("priceChange") or {}
                        _c_m5 = float(_c_pc.get("m5", 0) or 0)
                        _c_tx = (_c_pre.get("txns") or {}).get("m5", {}) or {}
                        _c_trades = int(_c_tx.get("buys", 0) or 0) + int(_c_tx.get("sells", 0) or 0)
                        _c_vol5 = float((_c_pre.get("volume") or {}).get("m5", 0) or 0)

                        # Древний токен старше 24ч без пробоя: скип на 24 часа
                        if _c_age_m > 1440.0 and _c_m5 < 12.0:
                            processed_mints[mint] = time.time() + 86400
                            continue

                        # Старый токен (>4ч) без импульса (<6%): скип на 1 час
                        if _c_age_m > 240.0 and _c_m5 < 6.0 and mint not in _graduated_set:
                            processed_mints[mint] = time.time() + 3600
                            continue

                        # Токен старше 1ч со слабым импульсом (<4%): скип на 30 мин (ЛЮБОЙ DEX, включая pump.fun!)
                        if _c_age_m > 60.0 and _c_m5 < 4.0 and mint not in _graduated_set:
                            processed_mints[mint] = time.time() + 1800
                            continue

                        # Мертвый токен старше 3 минут с 0 сделок в m5: скип на 30 мин
                        if _c_age_m >= 3.0 and _c_trades == 0 and _c_vol5 == 0 and mint not in _graduated_set:
                            processed_mints[mint] = time.time() + 1800
                            continue

                        # Слив/флет (m5 < 2.0%) не из миграций: кулдаун 2 мин
                        if _c_m5 < 2.0 and mint not in _graduated_set and _c_age_m > 3.0:
                            processed_mints[mint] = time.time() + 120
                            continue

                        _score_rank = (1000.0 if mint in _graduated_set else 0.0) + _c_m5
                        _ranked_mints.append((_score_rank, mint))
                    else:
                        # Если данных в префетче нет: пускаем только свежие миграции, ручной вотчлист или молодой токен из роддома
                        if mint in _graduated_set:
                            _ranked_mints.append((1000.0, mint))
                        elif mint in getattr(config, "USER_WATCHLIST", []):
                            _ranked_mints.append((50.0, mint))
                        elif mint.endswith("pump") and birth_tracker.get_age(mint) is not None and birth_tracker.get_age(mint) <= 30.0:
                            _ranked_mints.append((10.0, mint))
                        else:
                            # Токен без данных префетча из бустов не спамит очередь одиночными запросами
                            processed_mints[mint] = time.time() + 300

                _ranked_mints.sort(key=lambda x: x[0], reverse=True)
                mints_to_scan = [m for _, m in _ranked_mints]

                # ⚡ Параллельный разбор: отбираем до 15 подходящих кандидатов в окно
                _launch = []
                _now_l = time.time()
                for _m in mints_to_scan:
                    if not _m or _m in tracker.positions:
                        continue
                    _pl_l = tracker.last_loss_pct(_m)
                    if _pl_l is not None and _pl_l <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                        continue
                    _pre_l = getattr(analyzer, "_prefetch_cache", {}).get(_m, (0, {}))[1]
                    _ca_l = _pre_l.get("pairCreatedAt") or 0 if isinstance(_pre_l, dict) else 0
                    _age_l = (time.time() * 1000 - _ca_l) / 60000.0 if _ca_l else 999.0
                    _is_fresh_l = _m in _graduated_set or _age_l <= 30.0
                    _cd_l = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 45)) if _is_fresh_l else int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 180))
                    if _now_l - processed_mints.get(_m, 0.0) < _cd_l:
                        continue
                    _launch.append(_m)
                    if len(_launch) >= 15:
                        break
                _launch_n = len(_launch)
                _scan_conc = int(getattr(config, "SCAN_CONCURRENCY", 4))
                if _launch and _scan_conc > 1:
                    _scan_win = ScanWindow(analyzer, _launch, _scan_conc)

                for mint in mints_to_scan:
                    if not mint or mint in tracker.positions:
                        continue
                    _pl = tracker.last_loss_pct(mint)
                    if _pl is not None and _pl <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                        if time.time() - processed_mints.get(mint, 0.0) >= 86400:
                            print(f"🚫 SCANNER {mint[:8]}: прошлый лосс {_pl*100:.0f}% — второй раз не входим.")
                        processed_mints[mint] = time.time()
                        continue
                    _pre_m = getattr(analyzer, "_prefetch_cache", {}).get(mint, (0, {}))[1]
                    _ca_m = _pre_m.get("pairCreatedAt") or 0 if isinstance(_pre_m, dict) else 0
                    _age_m = (time.time() * 1000 - _ca_m) / 60000.0 if _ca_m else 999.0
                    _is_fresh_m = mint in _graduated_set or _age_m <= 30.0
                    _cd = int(getattr(config, "SOLANA_FRESH_RESCAN_COOLDOWN", 45)) if _is_fresh_m else int(getattr(config, "SOLANA_RESCAN_COOLDOWN", 180))
                    if time.time() - processed_mints.get(mint, 0.0) < _cd:
                        continue
                    
                    # Держим память в чистоте
                    if len(processed_mints) > 1000:
                        processed_mints.clear()
        
                    try:
                        # Используем умный маршрутизатор (сам выберет XGBoost или Raydium модель)
                        _res = await _scan_win.get(mint) if _scan_win is not None else _SCAN_MISSING
                        if _res is _SCAN_MISSING:
                            is_good = await analyzer.analyze_token(mint)
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
                            processed_mints[mint] = time.time() - _cd + 25  # Перепроверить через 25с вместо долгого пропуска
                            if mint in _graduated_set:
                                # DS ещё не проиндексировал новый пул — вернём в очередь, не теряем.
                                # Но после 3 пустых — это не лаг, а скам/нестандарт: хороним.
                                _n = _grad_none.get(mint, 0) + 1
                                _grad_none[mint] = _n
                                if _n >= 3:
                                    _grad_none.pop(mint, None)
                                    print(f"⚰️ [MIGRATION] {mint[:8]}: DS не видит после 3 попыток — unindexable, хороню.")
                                else:
                                    try:
                                        birth_tracker.add_graduated(mint)
                                    except Exception:
                                        pass
                            continue
                        if is_good is False:
                            _sig = str(analyzer.get_signal(mint) or "")
                            if "откат" in _sig or "вертикаль" in _sig or mint in _graduated_set:
                                processed_mints[mint] = time.time() - _cd + 35  # Перепроверить через 35с
                            elif "dead-token" in _sig or "мёртвый токен" in _sig:
                                processed_mints[mint] = time.time() + 3600      # Мертвый токен: скип на 1 час
                            else:
                                processed_mints[mint] = time.time()
                        else:
                            processed_mints[mint] = time.time()
                        analyzer.log_scan(mint[:8], mint, "SCANNER",
                                          is_good, getattr(analyzer, "last_score", 0.0))

                        if is_good:
                            getattr(analyzer, "_prefetch_cache", {}).pop(mint, None)  # цена входа — только живая
                            pair_data = await analyzer.fetch_token_data(mint)
                            entry_price = float(pair_data.get("priceUsd", 0)) if pair_data else 0
                            actual_symbol = pair_data.get("baseToken", {}).get("symbol", "UNKNOWN") if pair_data else "UNKNOWN"
                            liq_usd = float(((pair_data or {}).get("liquidity", {}) or {}).get("usd", 0) or 0)
                            if liq_usd == 0 and (mint.endswith("pump") or (pair_data or {}).get("dexId") in ("pump", "pumpfun")):
                                _fdv = float(((pair_data or {}).get("fdv", 0) or (pair_data or {}).get("marketCap", 0) or 0))
                                liq_usd = _fdv * 0.40
                            if entry_price > 0:
                                if pair_data.get("_price_divergence"):
                                    _div_r = float(pair_data.get("_price_divergence_ratio", 0.0) or 0.0)
                                    print(f"🚫 [SCANNER-PRICE-SPLIT] {actual_symbol} ({mint[:8]}): рассинхрон пулов {_div_r:.1f}x — блок.")
                                    continue
                                _dex_id = str((pair_data.get("dexId") or "")).lower()
                                _pc_live = pair_data.get("priceChange") or {}
                                _m5_live = float(_pc_live.get("m5", 0) or 0)
                                _m1_live = float(_pc_live.get("m1", 0) or 0)
                                _h1_live = float(_pc_live.get("h1", 0) or 0)
                                _pk_drop_live = float(pair_data.get("_drop_from_peak_5m", 0.0) or 0.0)
                                _lq_drop_live = float(pair_data.get("_liq_drop_from_peak", 0.0) or 0.0)

                                # Повторная проверка живых метрик после сброса prefetch-кэша (защита от кейса CHUNKEE: кэш +m5, а живой рынок m5 -1.6%, m1 -8.8%)
                                if _m5_live < 2.5 or _m1_live < -8.0:
                                    print(f"🚫 [SCANNER-LIVE-REVERSAL] {actual_symbol} ({mint[:8]}): на живом графике импульс сломан (m5 {_m5_live:+.1f}%, m1 {_m1_live:+.1f}%) — отмена покупки!")
                                    continue
                                _is_fresh_live = mint.endswith("pump") or _age_m <= 90.0
                                _max_h1_live = 3500.0 if (_is_fresh_live and _m5_live >= 2.5) else 400.0
                                if _h1_live > _max_h1_live:
                                    print(f"🚫 [SCANNER-LIVE-EXHAUSTED] {actual_symbol} ({mint[:8]}): живой h1 {_h1_live:+.0f}% > +{_max_h1_live:.0f}% — перегретая вершина, отмена!")
                                    continue
                                if _pk_drop_live <= -18.0 or _lq_drop_live <= -18.0:
                                    print(f"🚫 [SCANNER-POST-PEAK] {actual_symbol} ({mint[:8]}): токен уже слился от пика (цена {_pk_drop_live:+.1f}%, ликва {_lq_drop_live:+.1f}%) — отмена!")
                                    continue

                                # Блок кастомных не-pump контрактов на PumpSwap (100% разблокированный LP скамера, кейс WHITEWHALE -98.5%)
                                if _dex_id == "pumpswap" and not mint.endswith("pump"):
                                    print(f"🚫 [SCANNER-FAKE-PUMPSWAP] {actual_symbol} ({mint[:8]}): кастомный не-pump контракт на PumpSwap — отмена!")
                                    continue

                                # Защита от миграционного дампа Pump.fun: первые 90с на сброс instant-ботов, затем свободный вход
                                if mint.endswith("pump") and _dex_id in ("pumpswap", "raydium", "meteora", "orca"):
                                    _c_ms_sc = pair_data.get("pairCreatedAt") or 0
                                    _age_sc = ((time.time() * 1000 - _c_ms_sc) / 60000.0) if _c_ms_sc else 0.5
                                    if _age_sc < 1.5:
                                        print(f"⏳ [SCANNER-MIGRATION] {actual_symbol} ({mint[:8]}): AMM-пул создан {_age_sc:.1f}м назад (< 1.5м) — зона сброса снайперов.")
                                        continue
                                    _fdv_sc = float(pair_data.get("fdv", 0) or pair_data.get("marketCap", 0) or 0)
                                    if 0 < _fdv_sc < 30000.0:
                                        print(f"🚫 [SCANNER-MIGRATION] {actual_symbol} ({mint[:8]}): FDV ${_fdv_sc:,.0f} < $30,000 — слит ниже уровня миграции.")
                                        continue
                                    if liq_usd < 10000.0:
                                        print(f"🚫 [SCANNER-MIGRATION] {actual_symbol} ({mint[:8]}): ликвидность ${liq_usd:,.0f} < $10,000 — микро-пул.")
                                        continue

                                # Живая проверка цены Jupiter для ЛЮБЫХ токенов на AMM (включая pump на Raydium/PumpSwap)
                                if _dex_id not in ("pump", "pumpfun"):
                                    from jupiter import JupiterAPI
                                    _jup_chk = await JupiterAPI.get_price(mint)
                                    if _jup_chk and _jup_chk > 0 and entry_price > 0:
                                        if _jup_chk < entry_price * 0.93:
                                            analyzer._deny(mint, "scanner-live-dump >7%", f"🚫 [LIVE-DUMP] {actual_symbol} ({mint[:8]}): живая цена Jupiter (${_jup_chk:.8f}) уже на {(1.0 - _jup_chk / entry_price) * 100:.1f}% ниже DexScreener (${entry_price:.8f}) [>7%] — токен обваливается прямо сейчас, блок входа!")
                                            continue
                                        _chk_r = max(_jup_chk, entry_price) / min(_jup_chk, entry_price)
                                        if _chk_r > 1.25:
                                            print(f"🚫 [SCANNER-PRICE-MISMATCH] {actual_symbol} ({mint[:8]}): Jupiter (${_jup_chk:.8f}) vs DS (${entry_price:.8f}) расхождение {_chk_r:.1f}x — блок.")
                                            continue
                                        entry_price = _jup_chk
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

                                # === GROK BOT x JEV ENGINE: АНТИСКАМ-АУДИТ ➔ HARD GATE ➔ JEV AI VET ➔ FILLS & RISK ===
                                from grok_jev import grok_jev

                                # 0. ПОЛНЫЙ АНТИСКАМ-АУДИТ ПЕРЕД ВХОДОМ (RUGCHECK + DEV + WHALE + LP + JUPITER + SOCIALS)
                                _sec_ok, _sec_why, _h_stats = await grok_jev.full_anti_scam_audit(mint, actual_symbol, "solana", pair_data)
                                if not _sec_ok:
                                    print(f"🚫 [SCANNER-ANTI-SCAM] {actual_symbol} ({mint[:8]}): {_sec_why} — отказ входа.")
                                    continue

                                # 1. HARD GATE: "if it fails here, JEV never sees it"
                                _jev_hard_ok, _jev_hard_why = grok_jev.hard_gate(mint, pair_data, "solana", _h_stats)
                                if not _jev_hard_ok:
                                    print(f"🚫 [JEV-HARD-GATE] {actual_symbol} ({mint[:8]}): {_jev_hard_why} — отказ входа.")
                                    continue

                                # 2. SOFT GATE: JEV AI Evaluation (Groq / xAI Grok / Gemini / Local)
                                _jev_soft_ok, _jev_data, _jev_soft_why = await grok_jev.jev_vet(mint, actual_symbol, pair_data, "solana", _h_stats)
                                if not _jev_soft_ok:
                                    print(f"🚫 [JEV-AI-GATE] {actual_symbol} ({mint[:8]}): {_jev_soft_why} — отказ входа.")
                                    continue

                                # 3. FILLS: "We bid the flush, not the candle" & RISK: "The stop lives under the wick"
                                _flush_ok, _wick_pct, _wick_stop_price, _flush_msg = grok_jev.check_flush_and_wick(pair_data, entry_price)
                                if not _flush_ok:
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
                                else:
                                    fixed = float(getattr(config, "TRADE_AMOUNT_USD", 0.0) or 0.0)
                                    if fixed > 0:
                                        position_size = fixed
                                        _is_lot = False
                                        _is_vipsig = False
                                    else:
                                        capital = tracker.get_total_capital()
                                        base_position = capital * (config.REINVEST_PERCENT / 100.0)
                                        max_allowed_by_pool = liq_usd * 0.01 if liq_usd > 0 else 99999.0  
                                        position_size = max(4.0, min(base_position, max_allowed_by_pool, 100.0))
                                    
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
                                        _vip_cap = float(getattr(config, "TG_VIP_SIZE_USD", 5.0))
                                        position_size = min(position_size, _vip_cap)

                                if position_size < (1.0 if (_is_lot or _is_vipsig) else 4.0):
                                    print(f"🚫 Отказ (Ликвидность): Недостаточно ликвидности (${liq_usd}) для безопасного входа.")
                                    continue

                                # Защита от переразгона: в рынке не больше 60% капитала
                                _deployed = sum(p.amount_usd for p in tracker.get_open_positions().values())
                                _cap = tracker.get_total_capital() * getattr(config, "MAX_DEPLOYED_PCT", 0.60)
                                if _deployed + position_size > _cap:
                                    print(f"🚫 Отказ (Exposure): занято ${_deployed:.0f} + ${position_size:.0f} > лимит ${_cap:.0f}.")
                                    continue

                                _pc = pair_data.get("priceChange") or {}
                                _tx5 = (pair_data.get("txns") or {}).get("m5", {}) or {}
                                analyzer.log_scan(actual_symbol, mint, "SCANNER", True,
                                                  getattr(analyzer, "last_score", 0.0),
                                                  liq_usd, _pc.get("m5", 0) or 0,
                                                  _tx5.get("buys", 0) or 0, _tx5.get("sells", 0) or 0)
                                tracker.add_position(actual_symbol, mint, entry_price, position_size, is_mature=True,
                                                     ml_features=analyzer.pack_features(pair_data),
                                                     ml_confidence=float(getattr(analyzer, "last_score", 0.0)),
                                                     source=f"JEV:{_jev_data.get('source', 'grok')}:{analyzer.get_signal(mint)}",
                                                     pair_address=pair_data.get("pairAddress", ""),
                                                     stop_under_wick=_wick_stop_price,
                                                     wick_stop_loss_pct=_wick_pct)
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
                    # Очищаем файл после прочтения
                    with open('fomo_signals.txt', 'w') as f:
                        f.write('')
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
            rugpull_feeder.feed_rugs_and_retrain()
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
                    (maxp >= 0.25 and not getattr(pos, "tp1_done", False))
                    or (maxp >= float(getattr(config, "TP2_TRIGGER_PCT", 1.00)) and not getattr(pos, "tp2_done", False))
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
                elif held >= 6.0 and maxp >= float(getattr(config, "BREAKEVEN_PCT", 0.28)) and pnl <= 0.01:
                    reason = f"{tag} Breakeven (был пик +{maxp * 100:.0f}%)"
                elif held < float(getattr(config, "INFANT_WINDOW_MIN", 2.0)) and pnl <= float(getattr(config, "INFANT_DUMP_PCT", -0.25)):
                    reason = f"{tag} Infant Dump ({pnl * 100:.1f}%)"
                elif not _is_runner and maxp < 0.015 and pnl <= -0.10 and held >= 3.5:
                    reason = f"{tag} Zero-Momentum Cut ({held:.0f}m, {pnl * 100:.1f}%)"
                elif not _is_runner and maxp < 0.02 and pnl <= -0.18 and held >= 4:
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
                    _exit_px = 0.0 if any(k in reason.upper() for k in ("HONEYPOT", "RUG", "SCAM", "CANNOT SELL", "UNSELLABLE", "NO LIQUIDITY")) else cur
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
                                                 wick_stop_loss_pct=_wick_pct)
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
    if not getattr(config, "ROBINHOOD_ENABLED", False) and not has_open_rh:
        print("🟣 Robinhood отключён в config (ROBINHOOD_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "robinhood")


async def base_loop(analyzer, tracker):
    if not getattr(config, "BASE_ENABLED", True):
        print("🟦 Base отключён в config (BASE_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "base")


async def bsc_loop(analyzer, tracker):
    has_open_bsc = any(getattr(p, "chain", "") == "bsc" for p in tracker.get_open_positions().values())
    if not getattr(config, "BSC_ENABLED", False) and not has_open_bsc:
        print("🟨 BSC отключён в config (BSC_ENABLED=False).")
        return
    await _evm_chain_loop(analyzer, tracker, "bsc")


async def ethereum_loop(analyzer, tracker):
    if not getattr(config, "ETHEREUM_ENABLED", True):
        print("⬜ Ethereum отключён в config (ETHEREUM_ENABLED=False).")
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
    sniper = PumpFunSniper(tracker)
    
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
    await asyncio.gather(
        _supervised(lambda: position_manager_loop(analyzer, tracker), "position_manager_loop"),
        _supervised(lambda: scanner_loop(analyzer, tracker), "scanner_loop"),
        _supervised(lambda: copy_trader.listen(), "copy_trader"),
        _supervised(lambda: fomo_signal_loop(analyzer, tracker), "fomo_signal_loop"),
        _supervised(lambda: tg_listener_loop(analyzer, tracker), "tg_listener_loop"),
        _supervised(lambda: tg_preview_loop(analyzer, tracker), "tg_preview_loop"),
        _supervised(lambda: fomo_loop(analyzer, tracker), "fomo_loop"),
        _supervised(lambda: birdeye_loop(analyzer, tracker), "birdeye_loop"),
        _supervised(lambda: jup_loop(analyzer, tracker), "jup_loop"),
        _supervised(lambda: base_loop(analyzer, tracker), "base_loop"),
        _supervised(lambda: evm_wss_loop(analyzer, tracker), "evm_wss_loop"),
        _supervised(lambda: fomo_api_loop(analyzer, tracker), "fomo_api_loop"),
        _supervised(lambda: gmgn_bridge_loop(analyzer, tracker), "gmgn_bridge_loop"),
        _supervised(lambda: growth_loop(analyzer, tracker), "growth_loop"),
        _supervised(lambda: sniper.connect_and_listen(), "pump_fun_sniper"),
        _supervised(lambda: smart_money_hunter_loop(analyzer, tracker), "smart_money_hunter_loop"),
        _supervised(lambda: grok_bot_jev_loop(tracker, analyzer=analyzer), "grok_bot_jev_loop"),
        _supervised(lambda: trade_logger.post_trade_watcher_loop(), "trade_logger"),
        _supervised(lambda: shadow_tracker.price_watcher_loop(), "shadow_tracker"),
        _supervised(lambda: rugpull_feeder_loop(), "rugpull_feeder_loop"),
        _supervised(lambda: sol_price_updater(), "sol_price_updater"),
        return_exceptions=True
    )

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

with tab1:
    # Кнопка для ручного обновления страницы
    if st.button("🔄 Обновить портфель"):
        pass
        
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
                        pnl_usd = row['current_pnl_usd']
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
                        _right = (f"<h3 style='margin:0; color: #888;'>⏳ нет цены</h3>"
                                  if _blind else
                                  f"<h3 style='margin:0; color: {color};'>{sign}${pnl_usd:.2f} ({sign}{pnl_pct:.2f}%)</h3>")

                        _orig_amt = float(row.get('original_amount_usd') or row.get('amount_usd') or 0.0)
                        _cur_amt = float(row.get('amount_usd') or 0.0)
                        _is_mb = bool(row.get('is_moonbag', False) or row.get('partial_tp1_done', False) or (_cur_amt < _orig_amt * 0.9))
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

                        st.markdown(f"""
                        <div style='background-color: #1E1E1E; padding: 15px; border-radius: 8px; border-left: 5px solid {color}; margin-bottom: 10px; font-family: sans-serif;'>
                            <div style='display: flex; justify-content: space-between; align-items: center;'>
                                <h3 style='margin:0; color: #FFF;'>{row['symbol'] if str(row['symbol']).strip() else row['mint'][:6] + '...'}</h3>
                                {_right}
                            </div>
                            <div style='margin-top: 6px; font-size: 0.75em; color: #888;'>🔖 {row.get('source', '') if str(row.get('source', '')).strip() else '—'} {chain_badge(row.get('chain', 'solana'), True)}</div>
                            <div style='margin-top: 6px; font-size: 0.85em; color: #DDD;'>{_size_html}</div>
                            {_updated_badge}
                            <div style='display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.85em; color: #BBB;'>
                                <div><span style='color:#888;'>Вход:</span><br>${row['entry_price_usd']:.8f}</div>
                                <div><span style='color:#888;'>Сейчас:</span><br>${row['current_price_usd']:.8f}</div>
                                <div><span style='color:#888;'>Пик:</span><br>${row['max_price_usd']:.8f}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
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
                    <div style='background-color: #2D2D2D; padding: 20px; border-radius: 10px; text-align: center; margin-bottom: 15px;'>
                        <div style='color: #888; font-size: 1.1em; text-transform: uppercase;'>Общий PnL</div>
                        <h1 style='margin: 0; color: {"#00C851" if total_pnl >= 0 else "#FF4444"};'>
                            {"+" if total_pnl >= 0 else ""}${total_pnl:.2f}
                        </h1>
                    </div>
                    """, unsafe_allow_html=True)
                    
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

                            st.markdown(f"""
                            <div style='background-color: #1A1A1A; padding: 10px 15px; border-radius: 6px; border-right: 4px solid {c_color}; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;'>
                                <div>
                                    <div style='color: #FFF; font-weight: bold;'>{row['symbol'] if str(row['symbol']).strip() else row.name[:6] + '...'} <span style='font-size: 0.7em; color: #888;'>{chain_badge(row.get('chain', 'solana'))}</span></div>
                                    <div style='color: #666; font-size: 0.75em;'>{row.get('exit_reason', 'Closed')} {row.get('held', '')}</div>
                                    <div style='color: #555; font-size: 0.7em;'>🔖 {row.get('source', '') if str(row.get('source', '')).strip() else '—'}</div>
                                </div>
                                <div style='text-align: right; color: {c_color}; font-weight: bold;'>
                                    {c_sign}${p_usd:.2f} <br> <span style='font-size: 0.8em;'>({c_sign}{p_pct:.2f}%)</span>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                else:
                    st.info("История пуста.")
        else:
            st.info("Файл портфеля пуст.")
    except FileNotFoundError:
        st.info("Бот еще не совершил первую сделку.")

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

# Автообновление интерфейса (по умолчанию выключено по запросу пользователя)
if st.checkbox("Включить автообновление (каждые 5 сек)", value=False):
    time.sleep(5)
    st.rerun()

if __name__ == "__main__":
    # Если запущен напрямую (python3 main.py), держим процесс активным для терминала/демона
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
