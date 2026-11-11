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

        pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd

        if not hasattr(position, 'max_price_usd') or position.max_price_usd <= 0:
            position.max_price_usd = current_price
            position.peak_time = time.time()
        elif current_price > position.max_price_usd:
            position.max_price_usd = current_price
            position.peak_time = time.time()

        max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd else 0.0

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
        _min_tp_ticket = float(getattr(config, "PARTIAL_TP_MIN_TICKET_USD", 20.0))
        _pos_val = float(getattr(position, "original_amount_usd", 0.0) or getattr(position, "amount_usd", 0.0) or 0.0)
        _allow_partial_tp = getattr(config, "PARTIAL_TP_ENABLED", True) and (_pos_val >= _min_tp_ticket)

        # =========================================================================
        # 0. МГНОВЕННЫЙ СБРОС ПРИ ДАМПЕ ПУЛА / ВЫВОДЕ ЛИКВИДНОСТИ (0ms Liquidity Shield)
        # =========================================================================
        _pd_chk = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        if isinstance(_pd_chk, dict) and _pd_chk:
            _cur_l = float(((_pd_chk.get("liquidity") or {}).get("usd", 0) or 0))
            _peak_l = float(getattr(position, "peak_liquidity_usd", 0.0) or getattr(position, "initial_liquidity_usd", 0.0) or 0.0)
            if _cur_l > _peak_l:
                position.peak_liquidity_usd = _cur_l
                _peak_l = _cur_l
            # Абсолютная потеря ликвидности (вывод пула девом в 0):
            if (_peak_l >= 1000.0 and _cur_l <= 200.0) or (_peak_l >= 3000.0 and _cur_l < (_peak_l * 0.20)):
                return f"Absolute Liquidity Loss (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
            # Ранний срез вывода ликвидности девом (>35% просадки пула) — спасаем деньги ДО полного слива:
            elif _peak_l >= 3000.0 and _cur_l < (_peak_l * 0.65):
                return f"Early Liquidity Drop Escape (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
            else:
                position._liq_drop_streak_em = 0

            # Ранний сброс при агрессивном даме продавцов на первых минутах (<2м):
            if pnl_pct <= -0.070 and minutes_held < 2.0:
                txns_m5 = (_pd_chk.get("txns") or {}).get("m5") or {}
                b5_chk = int(txns_m5.get("buys", 0) or 0)
                s5_chk = int(txns_m5.get("sells", 0) or 0)
                if s5_chk >= 10 and (b5_chk / max(1, s5_chk)) < 0.40:
                    return f"Early Seller Dump Cut ({pnl_pct*100:.1f}%, {b5_chk}b/{s5_chk}s)"

        # =========================================================================
        # 1. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (EMERGENCY CAP & STOP LOSS) — ПЕРВЫЙ ПРИОРИТЕТ!
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.250)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"

        _base_sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', -0.180)))
        _has_organic_bid = False
        _feats = getattr(position, "ml_features", None) or {}
        if isinstance(_feats, dict) and _feats:
            _f_liq = float(_feats.get("liquidity", 0) or 0)
            _f_bs = float(_feats.get("buy_sell_ratio", 0) or 0)
            if _f_liq >= 15000.0 and _f_bs >= 1.15:
                _has_organic_bid = True
        _sl = min(_base_sl, -0.220) if _has_organic_bid else _base_sl
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            # Требуем минимум 2 тика подтверждения (или глубокий провал на 5% ниже стопа),
            # чтобы мгновенный спред/вик DEX на 2-й минуте не выбивал ракету вроде CULT или PINCHE!
            if _sl_c >= 2 or pnl_pct <= (_sl - 0.05):
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # Безубыток для мунбэга (не даем отдать прибыль после взятия TP1):
        if _is_mb:
            if pnl_pct <= 0.020:
                return f"Mature Moonbag Breakeven (+{pnl_pct*100:.1f}% locked after TP1)"

        # =========================================================================
        # 2. РАКЕТНЫЙ ТРЕЙЛИНГ-СТОП И PROFIT FLOOR (СТРАТЕГИЯ 1000% РАКЕТ)
        # =========================================================================
        # Лестничный трейлинг от +12% до +1000%:
        # До +12% монета свободно дышит.
        # От +12% включается защита от разворота в минус.
        # От +20% и +35% гарантируется чистый плюс (кейсы Democat +41%, PIRATE +48%, 牛逼 +42%).
        # На ракетах (+50%..+1000%) широкий трейлинг даёт выжимать 10x-потенциал!
        _floor_pnl = None
        _trail_dist = None

        if _is_mb:
            # === МУНБЭГ-ТРЕЙЛИНГ НА +200% ===
            # TP1 уже забрал 45% на +18% — сделка в плюсе. Остаток едет свободно:
            # трейлинг включается только от пика +200%, ниже — лишь страховочные полы (без трейлинга).
            _mb_act = float(getattr(config, "MOONBAG_TRAIL_ACTIVATION_PCT", 2.00))
            _mb_dist = float(getattr(config, "MOONBAG_TRAIL_DISTANCE_PCT", 0.25))
            if max_pnl_pct >= 5.00:
                _floor_pnl = round(max_pnl_pct * 0.70, 2)
                _trail_dist = min(_mb_dist, 0.20)
            elif max_pnl_pct >= _mb_act:
                _floor_pnl = round(_mb_act * 0.60, 2)   # на +200% пол +120%
                _trail_dist = _mb_dist
            elif max_pnl_pct >= 1.00:
                _floor_pnl = 0.50                        # страховка: не отдаём 2x обратно
            elif max_pnl_pct >= 0.50:
                _floor_pnl = 0.15                        # страховка: не отдаём +50% обратно
            # ниже +50% пика — только безубыток +2% (выше)
        elif max_pnl_pct >= 5.00:         # +500%+ (6x - 11x+ гипер-ракета)
            _floor_pnl = round(max_pnl_pct * 0.70, 2)  # Забираем 70% от пика (на +1000% выход на +700%!)
            _trail_dist = 0.18          # Трейлинг 18% от пика
        elif max_pnl_pct >= 2.50:       # +250%..+500% (3.5x - 6x ракета)
            _floor_pnl = 1.60           # Гарантируем минимум +160% чистыми (+$8.00 на $5!)
            _trail_dist = 0.20          # Трейлинг 20% от пика
        elif max_pnl_pct >= 1.00:       # +100%..+250% (2x - 3.5x ракета)
            _floor_pnl = 0.60           # Гарантируем минимум +60% чистыми (+$3.00 на $5!)
            _trail_dist = 0.22          # Широкий трейлинг 22% от пика
        elif max_pnl_pct >= 0.50:       # +50%..+100% (1.5x - 2x первый взлет)
            _floor_pnl = 0.25           # Гарантируем минимум +25% чистыми (+$1.25 на $5!)
            _trail_dist = 0.22          # Трейлинг 22% от пика (дает свободно дышать)
        elif max_pnl_pct >= 0.35:       # +35%..+50% (уверенный взлет, кейсы Democat +41%, PIRATE +48%, 牛逼 +42%)
            _floor_pnl = 0.12           # Фиксируем минимум +12% чистой прибыли
            _trail_dist = 0.20          # Широкий трейлинг 20% от пика (не душим нормальные откаты)
        # До +35% пика не режем позицию микро-трейлингом 10%! Даём дойти до TP1 (+18%) и превратиться в Moonbag!

        if _floor_pnl is not None and minutes_held >= 0.20:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            _tag = "Moonbag" if _is_mb else "Full Runner"
            if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                return f"Mature {_tag} Trailing ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif pnl_pct <= _floor_pnl:
                return f"Mature {_tag} Floor (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 3. PARABOLIC MOMENTUM HARVEST (ФИКСАЦИЯ ПРИ ЗАСТРЕВАНИИ НА ВЕРШИНЕ)
        # =========================================================================
        # Застревание на хаях без новых ATH больше 8-15 минут — предвестник дев-дампа:
        # Мунбэг ниже +200% не режем «харвестом» — его страхуют полы выше.
        _mb_free_ride = _is_mb and max_pnl_pct < float(getattr(config, "MOONBAG_TRAIL_ACTIVATION_PCT", 2.00))
        if max_pnl_pct >= 2.00 and _peak_age_min >= 8.0:
            return f"Parabolic 3x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if not _mb_free_ride and max_pnl_pct >= 1.00 and _peak_age_min >= 10.0:
            return f"Parabolic 2x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if not _mb_free_ride and max_pnl_pct >= 0.50 and _peak_age_min >= 15.0 and pnl_pct >= 0.20:
            return f"Rocket Momentum Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        # =========================================================================
        # 4. FRONT-RUN PROFIT EXIT (Разворот стакана только при уверенном профите >= +35%)
        # =========================================================================
        _pd = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        if isinstance(_pd, dict) and _pd:
            txns_m5 = (_pd.get("txns") or {}).get("m5") or {}
            b5 = int(txns_m5.get("buys", 0) or 0)
            s5 = int(txns_m5.get("sells", 0) or 0)
            pc = _pd.get("priceChange") or {}
            m1 = float(pc.get("m1", 0) or 0)
            bs_ratio = (b5 / s5) if s5 > 0 else 2.0
            total_tx5 = b5 + s5
            if minutes_held >= 3.0 and pnl_pct >= 0.35 and total_tx5 >= 25 and bs_ratio < 0.40 and m1 <= -5.0:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"

        # =========================================================================
        # 5. MAX HOLD TIMEOUT (УДЕРЖАНИЕ ДО 35-45 МИНУТ ДЛЯ РАСКРУТКИ ТРЕНДА)
        # =========================================================================
        _mb_max_hold = float(getattr(config, "MOONBAG_MAX_HOLD_MIN", 90.0)) if _is_mb else 45.0
        if not _is_mb and minutes_held >= 35.0 and pnl_pct >= 0.05 and _peak_age_min >= 15.0:
            return f"Max Hold Profit Take (+{pnl_pct*100:.1f}% locked at {minutes_held:.0f}m)"
        if _is_mb and minutes_held >= 60.0 and _peak_age_min >= 25.0:
            return f"Moonbag Max Hold Take ({pnl_pct*100:+.1f}% at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if minutes_held >= _mb_max_hold:
            return f"Max Hold Expiry ({pnl_pct*100:+.1f}% after {minutes_held:.0f}m)"

        # =========================================================================
        # 6. СТАГНАЦИЯ И МЕРТВЫЙ ИМПУЛЬС (Срез стоячих монет до дев-дампа)
        # =========================================================================
        # Если монета в рынке 6+ минут, её пик был слабым (< +20%), и новых ATH нет 4.5+ минут:
        # выходим немедленно! В мемкоинах затухание импульса означает скорый раг/дамп дева (кейс 牛逼 13м).
        if not _is_mb and minutes_held >= 6.0 and max_pnl_pct < 0.20 and _peak_age_min >= 4.5:
            _sign = "+" if pnl_pct >= 0 else ""
            return f"Stalled Momentum Escape ({_sign}{pnl_pct*100:.1f}%, no ATH {_peak_age_min:.0f}m at {minutes_held:.0f}m)"

        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 240.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.090))
        if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.04 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        if minutes_held >= 20.0 and pnl_pct <= -0.06 and max_pnl_pct < 0.04:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"
        if minutes_held >= 25.0 and pnl_pct < 0.015 and max_pnl_pct < 0.05:
            return f"Stagnant Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"

        return None


class TrailingExitManager:
    def __init__(self, trail_pct=0.10, min_profit=0.25):
        self.trail_pct = trail_pct
        self.min_profit = min_profit

    def evaluate_exit(self, position, current_price):
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
