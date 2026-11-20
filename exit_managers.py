import time
import config


class MatureExitManager:
    """Exit-логика профессионального трейдера:
    1. Защита от слива (Stop Loss -9% / Emergency Cap -10.5%).
    2. Защита профита (Breakeven Shield: пик >= +14% -> стоп переносится в безубыток +2.0%, в минус уйти НЕЛЬЗЯ).
    3. Фиксация ракет (Full Runner Trailing: пик +300% -> выход на 15% откате от ATH, берем +240% чистыми на $4!).
    4. Параболический сбор (нет новых ATH 4-6 мин на 2x-3x).
    5. Защита от долгого удержания (Max Hold 25-30м).
    """

    @staticmethod
    def evaluate_exit(position, current_price):
        if not hasattr(position, 'entry_price_usd') or position.entry_price_usd <= 0:
            return None
        if not current_price or current_price <= 0:
            return None

        # 🎯 Выделенный сверхбыстрый выход для стратегии RUG-HUNTER (не трогает основную стратегию!)
        try:
            import rug_hunter as _rh_em
            if _rh_em.is_rug_hunter_position(position):
                return _rh_em.evaluate_rug_hunter_exit(position, current_price)
        except Exception:
            pass

        pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd

        if not hasattr(position, 'max_price_usd') or position.max_price_usd <= 0:
            position.max_price_usd = current_price
            position.peak_time = time.time()
        elif current_price > position.max_price_usd:
            position.max_price_usd = current_price
            position.peak_time = time.time()

        max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd else 0.0

        # 🛡️ ЗАЩИТА ОТ СЛИВА ЛИКВИДНОСТИ (спасает от VCASH-типа: пул сдулся, цена пробила стоп в щелёк)
        _pd_liq = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        if isinstance(_pd_liq, dict) and _pd_liq:
            _liq_obj = _pd_liq.get("liquidity")
            if isinstance(_liq_obj, dict) and ("usd" in _liq_obj or "quote" in _liq_obj):
                _cur_l = float((_liq_obj.get("usd", 0) or 0))
                _peak_l = float(getattr(position, "peak_liquidity_usd", 0.0) or getattr(position, "initial_liquidity_usd", 0.0) or 0.0)
                if _cur_l > _peak_l:
                    position.peak_liquidity_usd = _cur_l
                    _peak_l = _cur_l
                # Если ликвидность просела более чем на 45% от пика — мгновенный выход, не ждём стоп-лосс
                if _peak_l >= 2000.0 and _cur_l <= (_peak_l * 0.55):
                    return f"🚨 Liq Drain Emergency ({_cur_l:.0f} USD left from ${_peak_l:.0f}, PnL {pnl_pct*100:+.1f}%)"

        now_ts = time.time()
        et = getattr(position, 'entry_time', now_ts)
        if hasattr(et, 'timestamp'):
            try:
                et = et.timestamp()
            except Exception:
                et = now_ts
        try:
            minutes_held = (now_ts - float(et)) / 60.0
        except Exception:
            minutes_held = 0.0

        _pt = getattr(position, 'peak_time', 0.0) or et
        if hasattr(_pt, 'timestamp'):
            try:
                _pt = _pt.timestamp()
            except Exception:
                _pt = et
        if not _pt or float(_pt) <= 0:
            _pt = et
        try:
            _peak_age_min = (now_ts - float(_pt)) / 60.0
        except Exception:
            _peak_age_min = 0.0

        _is_mb = getattr(position, "is_moonbag", False)
        _min_tp_ticket = float(getattr(config, "PARTIAL_TP_MIN_TICKET_USD", 3.0))
        _pos_val = float(getattr(position, "original_amount_usd", 0.0) or getattr(position, "amount_usd", 0.0) or 0.0)
        _allow_partial_tp = getattr(config, "PARTIAL_TP_ENABLED", True) and (_pos_val >= _min_tp_ticket)

        # =========================================================================
        # 0. МГНОВЕННЫЙ СБРОС ПРИ ДАМПЕ ПУЛА / ВЫВОДЕ ЛИКВИДНОСТИ (0ms Liquidity Shield)
        # =========================================================================
        _pd_chk = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        if isinstance(_pd_chk, dict) and _pd_chk:
            _liq_obj = _pd_chk.get("liquidity")
            if isinstance(_liq_obj, dict) and ("usd" in _liq_obj or "quote" in _liq_obj):
                _cur_l = float((_liq_obj.get("usd", 0) or 0))
                _peak_l = float(getattr(position, "peak_liquidity_usd", 0.0) or getattr(position, "initial_liquidity_usd", 0.0) or 0.0)
                if _cur_l > _peak_l:
                    position.peak_liquidity_usd = _cur_l
                    _peak_l = _cur_l
                _dex_chk = str(_pd_chk.get("dexId") or "").lower()
                _mint_str = str(getattr(position, "mint", "") or "").lower()
                _is_curve = _mint_str.endswith("pump") and _dex_chk in ("", "pump", "pumpfun", "pump-amm")
                # На pump.fun кривой ликвидность в пуле контролируется контрактом, дев не может её снять
                if not _is_curve:
                    # Абсолютная потеря ликвидности (вывод пула девом в 0):
                    if (_peak_l >= 1000.0 and _cur_l <= 200.0) or (_peak_l >= 3000.0 and _cur_l < (_peak_l * 0.20)):
                        return f"Absolute Liquidity Loss (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
                    # Ранний срез вывода ликвидности девом (>45% просадки пула 2 тика подряд):
                    elif _peak_l >= 3000.0 and _cur_l < (_peak_l * 0.55):
                        _drop_s = int(getattr(position, "_liq_drop_streak_em", 0) or 0) + 1
                        position._liq_drop_streak_em = _drop_s
                        if _drop_s >= 2:
                            return f"Early Liquidity Drop Escape (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
                    else:
                        position._liq_drop_streak_em = 0

            # Ранний сброс при агрессивном даме продавцов на первых минутах (<2м):
            if pnl_pct <= -0.12 and minutes_held < 2.0:
                txns_m5 = (_pd_chk.get("txns") or {}).get("m5") or {}
                b5_chk = int(txns_m5.get("buys", 0) or 0)
                s5_chk = int(txns_m5.get("sells", 0) or 0)
                if s5_chk >= 12 and (b5_chk / max(1, s5_chk)) < 0.35:
                    return f"Early Seller Dump Cut ({pnl_pct*100:.1f}%, {b5_chk}b/{s5_chk}s)"

            # ⚡ РАННИЙ СРЕЗ ЗАТУХАЮЩЕГО ИМПУЛЬСА (Early Momentum Failure Cut):
            # Если токен за 60-150 секунд так и не пошёл в рост (max_pnl_pct < +3.5%) и подтверждённо уходит в минус (<= -11.0%),
            # при этом 1м свеча красная или продавцы душат спрос (2 тика подтверждения — защита от 1-тик фитиля):
            if 1.0 <= minutes_held <= 2.5 and pnl_pct <= -0.11 and max_pnl_pct < 0.035:
                _pc_f = _pd_chk.get("priceChange") or {}
                _m1_f = float(_pc_f.get("m1", 0) or 0)
                _tx_f = (_pd_chk.get("txns") or {}).get("m5") or {}
                _b_f = int(_tx_f.get("buys", 0) or 0)
                _s_f = int(_tx_f.get("sells", 0) or 0)
                if _m1_f <= -0.2 or _s_f > _b_f or (_s_f >= 4 and (_b_f / max(1, _s_f)) < 1.15):
                    _emf_c = int(getattr(position, "_em_fail_n", 0) or 0) + 1
                    position._em_fail_n = _emf_c
                    if _emf_c >= 2 or pnl_pct <= -0.16:
                        return f"⚡ Early Momentum Failure Cut ({pnl_pct*100:.1f}%, {minutes_held*60:.0f}с без импульса — срез убытка)"
                else:
                    position._em_fail_n = 0
            else:
                position._em_fail_n = 0

            # СБРОС МОМЕНТАЛЬНОГО РАГА НА ПЕРВОЙ СВЕЧЕ (First Candle Rug Exit)
            # Даём 30 секунд на проход стартового спреда; если после 30с дев сливает стек (m1 <= -12% или продавцы > 60%) — режем.
            if 0.50 <= minutes_held <= 3.0 and pnl_pct <= -0.13:
                _pc_chk = _pd_chk.get("priceChange") or {}
                _m1_c = float(_pc_chk.get("m1", 0) or 0)
                _txns = (_pd_chk.get("txns") or {}).get("m5") or {}
                _b5 = int(_txns.get("buys", 0) or 0)
                _s5 = int(_txns.get("sells", 0) or 0)
                if _m1_c <= -12.0:
                    return f"🚨 First Candle Rug Dump: 1m crash ({_m1_c:.0f}%, PnL {pnl_pct*100:.1f}%)"
                if _s5 >= 6 and (_b5 / max(1, _s5)) < 0.40:
                    return f"🚨 First Candle Rug Cut: Sellers dominating ({_b5}b/{_s5}s, PnL {pnl_pct*100:.1f}%)"

        _pd_pos = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        _mint_pos = str(getattr(position, "mint", "") or "")
        _is_unlocked = config.is_position_unlocked_lp(
            _mint_pos,
            _pd_pos,
            bool(getattr(position, "is_lp_locked_80", False)),
        )
        position.is_unlocked_pool = _is_unlocked

        # =========================================================================
        # 0.48. БЫСТРЫЙ СРЕЗ НЕУДАВШЕГОСЯ ПРОБОЯ (QUICK SCRATCH ON FAILED BREAKOUT)
        # =========================================================================
        # Даём сделке минимум 45 секунд на проход стартового спреда и снайперского фитиля
        # (фикс VICE: -16.2% на 11-й секунде -> затем ракета +82.8%!).
        # Если на 45-120с монета ни разу не показала +3.0% и 2 тика подряд держится <= -12.0% — режем убыток.
        if 0.75 <= minutes_held <= 2.0 and max_pnl_pct < 0.030 and pnl_pct <= -0.12:
            _early_c = int(getattr(position, "_early_scratch_n", 0) or 0) + 1
            position._early_scratch_n = _early_c
            if _early_c >= 2:
                return f"⚡ Quick Scratch Failed Breakout ({pnl_pct*100:.1f}%, {minutes_held*60:.0f}с без импульса)"
        else:
            position._early_scratch_n = 0

        # =========================================================================
        # 0.5. ЖЕСТКИЙ СТОП-ЛОСС И ЗАЩИТА ОТ ОБВАЛА (LOSS CONTROL WITH SPREAD IMMUNITY)
        # =========================================================================
        # На кривых Pump.fun LP не может быть выдернут девом. Стартовые фитили -15%..-28% — норма волатильности
        # перед пампами +500%..+800% (кейсы wuf -23.8% -> +788%, LETTER -16.2% -> +284%, FiveM -21% -> +124%).
        # Для незалоченных пулов Meteora держим стоп строже (-18%), так как там дев может снять LP.
        if _is_unlocked:
            _sl = -abs(float(getattr(config, "STOP_LOSS_UNLOCKED_PCT", -0.18)))
            _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_UNLOCKED_PCT", -0.25)))
        else:
            _sl = -abs(float(getattr(config, "STOP_LOSS_PCT", -0.32)))
            _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.40)))

        if pnl_pct <= _emerg_cap:
            _em_c = int(getattr(position, "emerg_confirm_n", 0) or 0) + 1
            position.emerg_confirm_n = _em_c
            # На первых 60с требуем 2 тика подтверждения (защита от разового битого тика DexScreener/спреда):
            if minutes_held >= 1.0 or _em_c >= 2 or pnl_pct <= -0.75:
                return f"🚨 Emergency Cap Dump ({pnl_pct*100:.1f}%)"
        else:
            position.emerg_confirm_n = 0
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            # Подтверждаем 2 тика (защита от сквиза/спреда на 1-й секунде):
            if _sl_c >= 2 or pnl_pct <= (_sl * 1.15):
                return f"🚨 Fast Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 0.6. КОНТРОЛЬ СТАТУСА ПУЛА И ЗАЩИТА ОТ ЗАВИСАНИЯ (БЕЗ РАННЕГО СРЕЗА РАКЕТ!)
        # =========================================================================
        # Критично для 2x-10x: НЕ закрываем +15%..+50% волны через 12-18 секунд!
        # От рагов и дампов на ранней фазе уже защищают:
        # - Absolute Liquidity Loss / Early Liquidity Drop Escape (срез LP < 55%)
        # - First Candle Rug Cut (-7.0% в первые 2.5м) / Fast Stop Loss (-7.5%)
        # - Breakeven Shield (+16% пик -> стоп в безубыток/плюс, в минус уйти НЕЛЬЗЯ)
        # - Crash Guard (-18%..-50% за 1м)

        # Для незалоченных кастомных пулов: мгновенно забираем прибыль при застое (от 1 мин после пика +8% и отката 18%+),
        # не дожидаясь пока дев выдернет LP (кейс SCHRODING, где бот сидел 4 минуты до вывода пула в $0)!
        if not _is_mb and _is_unlocked:
            if minutes_held >= 1.0 and _peak_age_min >= 0.75 and max_pnl_pct >= 0.08 and pnl_pct <= max_pnl_pct * 0.82:
                return f"⚡ Unlocked Fast Profit Exit ({pnl_pct*100:+.1f}%, застой {_peak_age_min*60:.0f}с после пика +{max_pnl_pct*100:.1f}%)"
            elif minutes_held >= 1.5 and max_pnl_pct < 0.03 and pnl_pct < 0.01:
                return f"⚡ Unlocked Fast Stale Scratch ({pnl_pct*100:+.1f}%, 90с без взлёта на незалоченном пуле — сброс перед риском LP rug)"
            elif minutes_held >= 2.0 and pnl_pct < 0.02:
                return f"⚡ Unlocked Stale Exit ({pnl_pct*100:+.1f}%, 2м без импульса на незалоченном пуле — защита от LP rug)"

        # Б. СРЕЗ МЁРТВЫХ ФЛЕТОВ ПОСЛЕ КОНСОЛИДАЦИИ (НЕ рубим +0.0% на 35-й секунде!):
        # Даём сделке минимум 3.0 минуты (180с) на раскачку и обновление живых котировок;
        # от реальных дампов с 1-й секунды защищают Fast Stop Loss (-7.5%), Rug Cut (-7.0%) и LP Drop Escape.
        _scratch_min = max(2.5, float(getattr(config, "UNLOCKED_ENTRY_SCRATCH_SEC", 180.0)) / 60.0)
        if not _is_mb and minutes_held >= _scratch_min and max_pnl_pct < 0.04 and -0.055 <= pnl_pct <= -0.008:
            return f"⚡ Zero-Momentum Scratch ({pnl_pct*100:+.1f}%, нет отрыва за {minutes_held*60:.0f}с)"
        if not _is_mb and minutes_held >= max(4.5, _scratch_min + 1.5) and max_pnl_pct < 0.04 and -0.055 <= pnl_pct <= 0.005:
            return f"⚡ Dead Flat Scratch ({pnl_pct*100:+.1f}%, полный флет {minutes_held*60:.0f}с)"

        # =========================================================================
        # 2. ПРОСТАЯ СИСТЕМА ВЫХОДА (TP лесенка без сломанного мунбэга)
        # =========================================================================
        _tp1_t, _tp1_f, _tp2_t, _tp2_f, _tp3_t, _tp3_f, _tp4_t, _tp4_f, _tp5_t, _tp5_f = config.get_sol_tp_ladder(_is_unlocked)

        # TP1: частичные фиксации (TP1/TP2) выполняются в main.py через tracker.partial_close_position.
        # Если TP1_SELL_FRAC >= 1.0 (задана полная ликвидация без мунбэга), только тогда закрываем всю позицию:
        if _tp1_f >= 1.0 and not getattr(position, "tp1_done", False) and pnl_pct >= _tp1_t and minutes_held >= 0.20:
            position.tp1_done = True
            return f"TP1 (+{pnl_pct*100:.1f}%, продано 100%)"

        if _tp2_f >= 1.0 and getattr(position, "tp1_done", False) and not getattr(position, "tp2_done", False) and (pnl_pct + 0.005) >= _tp2_t:
            position.tp2_done = True
            return f"TP2 (+{pnl_pct*100:.1f}%, продано 100%)"

        # =========================================================================
        # 3. ДИНАМИЧЕСКИЙ ТРЕЙЛИНГ ПО ЦЕНЕ ОТ ATH И ПОЛ ПРИБЫЛИ (ДЛЯ РАКЕТ 2x-10x!)
        # =========================================================================
        # ВАЖНО:
        # - До пика +45% НЕ включаем Breakeven / Floor / Trailing — иначе 6x-8x ракеты вытряхивает
        #   на первой коррекции (кейсы DCTC +763% вышел на +23%, GTASZN +635% вышел на +2%, GTAgent +65% вышел в -1.3%)!
        # - Трейлинг измеряется СТРОГО как реальный откат ЦЕНЫ от пика ((max_price - price) / max_price),
        #   а НЕ как доля от max_pnl_pct (при +35% старая формула давала срез на -5.7% цены!).
        # - После TP1 (+110%, 50% продано -> Risk-Free Moonbag) даём мунбэгу широкий трейлинг 28-32% для полёта в 5x-10x!
        _has_tp1 = bool(_is_mb or getattr(position, "tp1_done", False))
        # Защита от slow-cook cabal рагов (кейс FIONA: 15м лесенкой +15%..+68% -> 1-блоковый сброс -96.3% на 16-й мин!):
        # Если монета не пробила TP1 (+35%) за 12 минут, это не взрывная ракета — забираем профит / выходим до дампа.
        # ВАЖНО: не режем монету, если она прямо сейчас обновляет локальные хаи (_peak_age_min < 2.5m) или активно идёт к TP1!
        _sc_prof_m = float(getattr(config, "SLOW_COOK_PROFIT_EXIT_MIN", 12.0) or 12.0)
        _sc_hard_m = float(getattr(config, "SLOW_COOK_HARD_EXIT_MIN", 15.0) or 15.0)
        if not _has_tp1 and _sc_prof_m > 0 and minutes_held >= _sc_prof_m:
            if pnl_pct >= 0.05 and _peak_age_min >= 2.5 and pnl_pct < 0.35:
                return f"⏱️ Slow-Cook Profit Harvest (+{pnl_pct*100:.1f}%, {minutes_held:.0f}м без пробоя TP1 — фиксация до кабал-дампа)"
            if _sc_hard_m > 0 and minutes_held >= _sc_hard_m and pnl_pct < 0.20:
                return f"⏱️ Slow-Cook Timeout Exit ({pnl_pct*100:+.1f}%, {minutes_held:.0f}м без ракеты — защита от slow-cook рага)"
        if max_pnl_pct >= 3.00 and _peak_age_min >= 12.0:
            return f"Parabolic Harvest (+{pnl_pct*100:.0f}% locked, пик был +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m без нового максимума)"

        _be_thr = float(getattr(config, "BREAKEVEN_PCT", 0.45) or 0.45)
        _floor_pnl = None
        _trail_price_drop = None
        _drop_from_peak = (
            (position.max_price_usd - current_price) / position.max_price_usd
            if position.max_price_usd > 0 else 0.0
        )

        if _has_tp1:
            # Risk-Free Moonbag (50% уже окупили ставку на +110% TP1):
            if max_pnl_pct >= 4.00:       # 5x+ мега-ракета: широкий трейл -28% от цены пика, пол +200%+
                _floor_pnl = max(2.00, round(max_pnl_pct * 0.55, 2))
                _trail_price_drop = 0.28
            elif max_pnl_pct >= 2.00:     # 3x..5x ракета: трейл -30% от цены пика, пол +90%
                _floor_pnl = 0.90
                _trail_price_drop = 0.30
            else:                         # 2.1x..3x (сразу после TP1): трейл -32% от пика, пол +15% (никогда в минус!)
                _floor_pnl = 0.15
                _trail_price_drop = 0.32
        else:
            # До TP1 (+110%): даём ракете дышать на откатах -25%, не вытряхивая на +15%..+35% шуме!
            if max_pnl_pct >= 1.10:
                _floor_pnl = 0.50
                _trail_price_drop = 0.28
            elif max_pnl_pct >= 0.80:     # +80%..+110%: фиксируем минимум +35%, трейл -26% от цены пика
                _floor_pnl = 0.35
                _trail_price_drop = 0.26
            elif max_pnl_pct >= 0.55:     # +55%..+80% (GTAgent +65%, GTASANTA +76%): пол +15%, трейл -26% с +72%
                _floor_pnl = 0.15
                _trail_price_drop = 0.26 if max_pnl_pct >= 0.72 else None
            elif max_pnl_pct >= _be_thr:  # +45%..+55%: Breakeven Shield (+3%, перекрывает комиссии)
                _floor_pnl = 0.03
                _trail_price_drop = None

        # 1. Трейлинг-стоп по реальному откату цены от ATH (2 тика подтверждения против ложного прокола):
        if _trail_price_drop is not None and _drop_from_peak >= _trail_price_drop:
            _tr_c = int(getattr(position, "_trail_conf_n", 0) or 0) + 1
            position._trail_conf_n = _tr_c
            if _tr_c >= 2 or _drop_from_peak >= (_trail_price_drop + 0.08):
                return f"Trailing Stop (+{pnl_pct*100:.1f}%, откат цены {_drop_from_peak*100:.0f}% от пика +{max_pnl_pct*100:.0f}%)"
        else:
            position._trail_conf_n = 0

        # 2. Неснижаемый пол прибыли (Floor Lock, 2 тика подтверждения):
        if _floor_pnl is not None and max_pnl_pct >= 0.55 and pnl_pct <= _floor_pnl:
            _fl_c = int(getattr(position, "_floor_conf_n", 0) or 0) + 1
            position._floor_conf_n = _fl_c
            if _fl_c >= 2 or pnl_pct <= (_floor_pnl - 0.08):
                return f"Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%)"
        else:
            position._floor_conf_n = 0

        # 3. Breakeven Shield: включается только после уверенного отрыва (+45% пик), 2 тика подтверждения:
        if max_pnl_pct >= _be_thr and pnl_pct <= 0.03:
            _be_c = int(getattr(position, "_be_conf_n", 0) or 0) + 1
            position._be_conf_n = _be_c
            if _be_c >= 2 or pnl_pct <= -0.02:
                return f"BREAKEVEN ({pnl_pct*100:+.1f}%, пик был +{max_pnl_pct*100:.1f}%)"
        else:
            position._be_conf_n = 0

        # =========================================================================
        # 4. CRASH VELOCITY GUARD (Дев-дамп / аномальное обрушение за 1 минуту)
        # =========================================================================
        # Если 1-минутная свеча упала > 25% — это не обычная коррекция, это дамп!
        # Нормальные Solana-откаты: -5%..-15% за минуту. -25%+ = дев выходит / кит сливает.
        # Выходим немедленно если мы в профите — не ждём трейлинга.
        # Если мы в убытке — уже есть стоп-лосс, он поймает.
        _pd = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        _m1_crash = None
        _vol_spike = False
        if isinstance(_pd, dict) and _pd:
            pc = _pd.get("priceChange") or {}
            _m1_crash = float(pc.get("m1", 0) or 0)
            txns_m5 = (_pd.get("txns") or {}).get("m5") or {}
            b5 = int(txns_m5.get("buys", 0) or 0)
            s5 = int(txns_m5.get("sells", 0) or 0)
            vol_m5 = float((_pd.get("volume") or {}).get("m5", 0) or 0)
            vol_h1 = float((_pd.get("volume") or {}).get("h1", 0) or 0)
            # Всплеск объёма продаж: объём за 5 минут > 40% часового → аномалия
            _vol_spike = (vol_m5 > vol_h1 * 0.40) if vol_h1 > 0 else False
            bs_ratio = (b5 / s5) if s5 > 0 else 2.0

            # === СЛУЧАЙ 1: ПОЛНЫЙ ДЕВ-ДАМП (-50%+ за 1 минуту) ===
            # Это однозначно не откат — уходим при любом PnL кроме глубокого стопа
            if _m1_crash <= -50.0 and minutes_held >= 0.5:
                return f"🚨 Crash Guard: Dev Dump ({_m1_crash:.0f}% за 1m, PnL {pnl_pct*100:+.1f}%)"

            # === СЛУЧАЙ 2: СИЛЬНЫЙ КРАШ (-35% или -25% при давлении продавцов) + МЫ В ПРОФИТЕ ===
            # На risk-free мунбэге не паникуем на -25% откате (только при настоящем обвале <= -42%):
            if ((_m1_crash <= -35.0) or (_m1_crash <= -25.0 and bs_ratio < 0.55)) and pnl_pct >= 0.08 and minutes_held >= 0.5 and (not _has_tp1 or _m1_crash <= -42.0):
                return f"🚨 Crash Guard: Price Crash ({_m1_crash:.0f}% за 1m, locked {pnl_pct*100:+.1f}%)"

            # === СЛУЧАЙ 3: УМЕРЕННЫЙ КРАШ (-18%) + ДОМИНИРОВАНИЕ ПРОДАВЦОВ + ВСПЛЕСК ===
            if _m1_crash <= -18.0 and pnl_pct >= 0.20 and bs_ratio < 0.30 and _vol_spike:
                return f"🚨 Crash Guard: Coordinated Sell ({_m1_crash:.0f}% 1m, {b5}b/{s5}s, vol spike, {pnl_pct*100:+.1f}%)"

        # =========================================================================
        # 5. FRONT-RUN PROFIT EXIT (Разворот стакана только при +65%+ — не режем волны на пути к 2x)
        # =========================================================================
        if isinstance(_pd, dict) and _pd:
            txns_m5 = (_pd.get("txns") or {}).get("m5") or {}
            b5 = int(txns_m5.get("buys", 0) or 0)
            s5 = int(txns_m5.get("sells", 0) or 0)
            pc = _pd.get("priceChange") or {}
            m1 = float(pc.get("m1", 0) or 0)
            bs_ratio = (b5 / s5) if s5 > 0 else 2.0
            total_tx5 = b5 + s5
            if minutes_held >= 4.0 and pnl_pct >= 0.65 and total_tx5 >= 30 and bs_ratio < 0.35 and m1 <= -8.0:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"
            vol_m5_now = float((_pd.get("volume") or {}).get("m5", 0) or 0)
            vol_h1_now = float((_pd.get("volume") or {}).get("h1", 0) or 0)
            _vol_dying = vol_h1_now > 0 and vol_m5_now < vol_h1_now * 0.05
            if minutes_held >= 5.0 and pnl_pct >= 0.60 and bs_ratio < 0.40 and _vol_dying and m1 <= -5.0:
                return f"Volume Exhaustion Exit: торги утихли ({b5}b/{s5}s, vol 5m ${vol_m5_now:.0f} vs h1 ${vol_h1_now:.0f}) в профите +{pnl_pct*100:.1f}%"

        # =========================================================================
        # 5. MAX HOLD TIMEOUT (ДАЁМ РАКЕТАМ ДО 38-55 МИНУТ НА РАЗГОН 2x-10x)
        # =========================================================================
        _mb_max_hold = float(getattr(config, "MOONBAG_MAX_HOLD_MIN", 55.0)) if _is_mb else 38.0
        if not _is_mb and minutes_held >= 20.0 and 0.03 <= pnl_pct < 0.25 and _peak_age_min >= 8.0:
            return f"Max Hold Profit Take (+{pnl_pct*100:.1f}% locked at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if _is_mb and minutes_held >= 60.0 and _peak_age_min >= 25.0:
            return f"Moonbag Max Hold Take ({pnl_pct*100:+.1f}% at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if minutes_held >= _mb_max_hold:
            return f"Max Hold Expiry ({pnl_pct*100:+.1f}% after {minutes_held:.0f}m)"

        # =========================================================================
        # 6. СТАГНАЦИЯ И МЕРТВЫЙ ИМПУЛЬС (Срез только реально мёртвых позиций без отрыва в рост)
        # =========================================================================
        if not _is_mb and minutes_held >= 8.0 and max_pnl_pct < 0.10 and _peak_age_min >= 5.0 and pnl_pct <= 0.02:
            _sign = "+" if pnl_pct >= 0 else ""
            return f"Stalled Momentum Escape ({_sign}{pnl_pct*100:.1f}%, no ATH {_peak_age_min:.1f}m at {minutes_held:.1f}m)"

        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 420.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.055))
        if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.05 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        if minutes_held >= 18.0 and pnl_pct <= -0.04 and max_pnl_pct < 0.06:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"
        if minutes_held >= 24.0 and pnl_pct < 0.02 and max_pnl_pct < 0.08:
            return f"Stagnant Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"

        # === ФЛЕТ В ПЛЮС: фиксируем профит даже если монета стоит на месте ===
        _fp_enabled = bool(getattr(config, "FLAT_PROFIT_ENABLED", True))
        if _fp_enabled and not _is_mb:
            _fp_min_pnl = float(getattr(config, "FLAT_PROFIT_MIN_PNL_PCT", 0.08))
            _fp_min_hold = float(getattr(config, "FLAT_PROFIT_MIN_HOLD_MIN", 5.0))
            _fp_peak_age = float(getattr(config, "FLAT_PROFIT_PEAK_AGE_MIN", 8.0))
            _fp_drift = float(getattr(config, "FLAT_PROFIT_PRICE_DRIFT_PCT", 0.03))
            _fp_drop = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            if (minutes_held >= _fp_min_hold
                    and pnl_pct >= _fp_min_pnl
                    and _peak_age_min >= _fp_peak_age
                    and _fp_drop <= _fp_drift):
                return f"Flat Profit Harvest (+{pnl_pct*100:.1f}% locked, {minutes_held:.0f}m hold, {_peak_age_min:.0f}m no ATH)"

        return None


class TrailingExitManager:
    def __init__(self, trail_pct=0.10, min_profit=0.25):
        self.trail_pct = trail_pct
        self.min_profit = min_profit

    def evaluate_exit(self, position, current_price):
        try:
            import rug_hunter as _rh_tr_em
            if _rh_tr_em.is_rug_hunter_position(position):
                return _rh_tr_em.evaluate_rug_hunter_exit(position, current_price)
        except Exception:
            pass

        entry = position.entry_price_usd
        pnl = (current_price - entry) / entry
        max_pnl = (position.max_price_usd - entry) / entry

        _ms_sl = -abs(float(getattr(config, "STOP_LOSS_PCT", -0.10)))
        if pnl <= _ms_sl:
            return f"Moonshot Hard Stop ({_ms_sl*100:.0f}%)"

        if max_pnl >= self.min_profit and not getattr(position, 'tp_1_hit', False):
            position.tp_1_hit = True
            return f"Moonshot TP +{self.min_profit*100:.0f}% (30% продано)"

        if getattr(position, 'tp_1_hit', False):
            trail_stop = position.max_price_usd * (1 - self.trail_pct)
            if current_price <= trail_stop:
                return f"Moonshot Trailing Stop (-{int(self.trail_pct*100)}% от пика)"

        for pct, label in [(5.0, "5x"), (20.0, "20x"), (100.0, "100x")]:
            if max_pnl >= pct and not getattr(position, f'tp_{label}_hit', False):
                setattr(position, f'tp_{label}_hit', True)
                return f"Moonshot TP +{pct*100:.0f}% ({label})"

        return None
