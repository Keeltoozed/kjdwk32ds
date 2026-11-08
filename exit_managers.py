import time
import config


class MatureExitManager:
    """Exit-логика для сбора зеленых импульсов и удержания ракет (без закрытия откатов в минус)."""

    @staticmethod
    def evaluate_exit(position, current_price):
        if not hasattr(position, 'entry_price_usd') or position.entry_price_usd <= 0:
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

        # =========================================================================
        # 1. ФИКСАЦИЯ ПРИБЫЛИ (PROFIT FLOOR & TRAILING STOP ДЛЯ РАКЕТ + ЗАЩИТА ДО TP1)
        # =========================================================================
        _is_mb = getattr(position, "is_moonbag", False)
        _tp1_thr = float(getattr(config, "TP1_TRIGGER_PCT", 0.085))
        _be_act = float(getattr(config, "BREAKEVEN_PCT", 0.080))
        _min_tp_ticket = float(getattr(config, "PARTIAL_TP_MIN_TICKET_USD", 20.0))
        _pos_val = float(getattr(position, "original_amount_usd", 0.0) or getattr(position, "amount_usd", 0.0) or 0.0)
        _allow_partial_tp = getattr(config, "PARTIAL_TP_ENABLED", True) and (_pos_val >= _min_tp_ticket)
        _floor_pnl = None
        _trail_dist = None

        # Включаем широкий трейлинг для мунбэгов ИЛИ для полной позиции $4:
        # Даём РАКЕТЕ СВОБОДНО ЛЕТЕТЬ на 100% объема без срезания на копейки!
        _enable_runner_trailing = _is_mb or (not _allow_partial_tp)
        if _enable_runner_trailing:
            if max_pnl_pct >= 5.00:
                _floor_pnl = round(max_pnl_pct * 0.70, 2)
                _trail_dist = 0.25
            elif max_pnl_pct >= 2.50:       # +250%..+500% (3.5x - 6x ракета как Democat +300%!)
                _floor_pnl = 1.50           # Гарантируем минимум +150% (2.5x чистый профит!)
                _trail_dist = 0.16          # Плотный трейлинг 16% от пика
            elif max_pnl_pct >= 1.00:       # +100%..+250% (2x - 3.5x ракета как NEX Ai +249%!)
                _floor_pnl = 0.65           # Гарантируем минимум +65% прибыли
                _trail_dist = 0.18          # Трейлинг 18% от пика
            elif max_pnl_pct >= 0.50:       # +50%..+100% (Horned cat +93%!)
                _floor_pnl = 0.28           # Гарантируем минимум +28% прибыли
                _trail_dist = 0.18          # Трейлинг 18% от пика
            elif max_pnl_pct >= 0.25:       # +25%..+50% (UNEMPLOYED +38%!)
                _floor_pnl = 0.12           # Гарантируем минимум +12% прибыли
                _trail_dist = 0.15          # Трейлинг 15% от пика
            elif max_pnl_pct >= 0.14:
                _floor_pnl = 0.045
                _trail_dist = 0.11
            else:
                _floor_pnl = None
                _trail_dist = None

            if _floor_pnl is not None and minutes_held >= 0.15:
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
                if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                    _tag = "Moonbag" if _is_mb else "Full Runner"
                    return f"Mature {_tag} Trailing ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                elif pnl_pct <= _floor_pnl:
                    _tag = "Moonbag" if _is_mb else "Full Runner"
                    return f"Mature {_tag} Floor ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 1.2. PARABOLIC MOMENTUM HARVEST (Фиксация прибыли при затухании импульса на вершине)
        # =========================================================================
        # Если монета сделала 2x-4x, но уже несколько минут не может пробить новый ATH — забираем прибыль!
        if max_pnl_pct >= 2.00 and _peak_age_min >= 4.0:
            return f"Parabolic 3x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if max_pnl_pct >= 1.00 and _peak_age_min >= 6.0:
            return f"Parabolic 2x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if max_pnl_pct >= 0.40 and _peak_age_min >= 8.0:
            return f"Rocket Momentum Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if max_pnl_pct >= 0.20 and _peak_age_min >= 10.0 and pnl_pct >= 0.05:
            return f"Breakout Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        # =========================================================================
        # 1.3. МАКСИМАЛЬНОЕ ВРЕМЯ УДЕРЖАНИЯ (MAX HOLD TIMEOUT — защита от кейса UNEMPLOYED 57м)
        # =========================================================================
        if minutes_held >= 25.0 and pnl_pct >= 0.02:
            return f"Max Hold Profit Take (+{pnl_pct*100:.1f}% locked at {minutes_held:.0f}m)"
        if minutes_held >= 30.0:
            return f"Max Hold Expiry ({pnl_pct*100:+.1f}% after {minutes_held:.0f}m)"

        if _is_mb:
            # Безубыток для мунбэга: гарантирует чистый плюс по суммарной сделке!
            _since_tp1 = now_ts - float(getattr(position, "last_tp_ts", 0.0) or 0.0)
            if pnl_pct <= -0.025:
                return f"Mature Moonbag Reversal ({pnl_pct*100:+.1f}% hard drop after TP1)"
            if _since_tp1 >= 45.0 and pnl_pct <= 0.005:
                _mb_be = int(getattr(position, "mb_be_confirm_n", 0) or 0) + 1
                position.mb_be_confirm_n = _mb_be
                if _mb_be >= 2 or pnl_pct <= 0.00:
                    return f"Mature Moonbag Breakeven ({pnl_pct*100:+.1f}% locked after TP1)"
            elif pnl_pct > 0.005:
                position.mb_be_confirm_n = 0

        else:
            # === ДЛЯ ОСНОВНОЙ ПОЗИЦИИ ДО ВЗЯТИЯ TP1 (только если билет >= $20) ===
            if _allow_partial_tp and max_pnl_pct >= _tp1_thr:
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
                if drop_from_max >= 0.040 and pnl_pct >= 0.035 and not getattr(position, "tp1_done", False):
                    # Откат на 4% от пика >= +8.5%: фиксируем TP1 сейчас, не давая растерять прибыль!
                    return "PARTIAL_TP1_TRIGGER"

            # Защита от ухода в минус после сильного пика (+8.0%+):
            # Если монета развернулась к цене входа (<= +1.2%), закрываем в безубыток!
            if max_pnl_pct >= _be_act and minutes_held >= 0.15:
                if pnl_pct <= 0.012:
                    return f"Mature Breakeven Shield ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 2. PRE-DUMP FRONT-RUNNER (Выход при развороте стакана ТОЛЬКО при солидной прибыли >= +20%)
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

            if minutes_held >= 2.0 and pnl_pct >= 0.20 and total_tx5 >= 20 and bs_ratio < 0.65 and m1 <= -3.5:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"

        # =========================================================================
        # 3. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (БЕЗ ПРОВАЛОВ -22%..-32%!)
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.105)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"

        _base_sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', -0.090)))
        # Если пул глубокий ($25k+), есть соцсети и покупатели продолжают выкупать пролив,
        # даём небольшой буфер до -10.5% против сноса стопов маркетмейкером
        _has_organic_bid = False
        _feats = getattr(position, "ml_features", None) or {}
        if isinstance(_feats, dict) and _feats:
            _f_liq = float(_feats.get("liquidity", 0) or 0)
            _f_bs = float(_feats.get("buy_sell_ratio", 0) or 0)
            _f_links = int(_feats.get("links_count", 0) or (1 if _feats.get("socials") else 0))
            if _f_liq >= 25000.0 and _f_bs >= 1.35 and _f_links >= 1:
                _has_organic_bid = True
        _sl = min(_base_sl, -0.105) if _has_organic_bid else _base_sl
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            if _sl_c >= 2 or pnl_pct <= -0.100 or minutes_held >= 0.10:
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 4. ВЫХОД ПО СТАГНАЦИИ
        # =========================================================================
        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 210.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.075))
        _buyers_active_stall = False
        if isinstance(_pd, dict) and _pd:
            _tx_st = (_pd.get("txns") or {}).get("m5") or {}
            _b_st = int(_tx_st.get("buys", 0) or 0)
            _s_st = int(_tx_st.get("sells", 0) or 0)
            if _b_st >= 25 and _b_st >= _s_st * 1.30:
                _buyers_active_stall = True
        _eff_stall_sec = (_stall_sec + 60.0) if _buyers_active_stall else _stall_sec
        if minutes_held >= (_eff_stall_sec / 60.0) and max_pnl_pct < 0.04 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        _loss_min = float(getattr(config, 'STAGNANT_LOSS_MIN', 12.0))
        _hold_min = float(getattr(config, 'STAGNANT_HOLD_MIN', 20.0))

        if minutes_held >= 20.0 and pnl_pct >= 0.12 and _peak_age_min >= 10.0:
            return f"Stagnant Profit Harvest (+{pnl_pct*100:.1f}% after {minutes_held:.0f}m)"

        if minutes_held >= _loss_min and pnl_pct <= -0.06 and max_pnl_pct < 0.04:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"

        if minutes_held >= _hold_min and pnl_pct < 0.015 and max_pnl_pct < 0.06:
            return f"Stagnant Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"

        if hasattr(config, 'TIME_EXIT_MINUTES') and hasattr(config, 'TIME_EXIT_PROFIT_REQ'):
            if minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
                return f"Mature Time Exit ({pnl_pct*100:+.1f}%)"

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
