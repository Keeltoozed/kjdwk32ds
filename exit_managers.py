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
        _tp1_thr = float(getattr(config, "TP1_TRIGGER_PCT", 0.16))
        _be_act = float(getattr(config, "BREAKEVEN_PCT", 0.095))
        _floor_pnl = None
        _trail_dist = None

        if max_pnl_pct >= 5.00:
            _floor_pnl = round(max_pnl_pct * 0.70, 2)
            _trail_dist = 0.22
        elif max_pnl_pct >= 2.00:
            _floor_pnl = 1.30
            _trail_dist = 0.20
        elif max_pnl_pct >= 1.00:
            _floor_pnl = 0.58
            _trail_dist = 0.18
        elif max_pnl_pct >= 0.50:
            _floor_pnl = 0.26
            _trail_dist = 0.16
        elif max_pnl_pct >= 0.28:
            _floor_pnl = 0.10
            _trail_dist = 0.15
        else:
            # ВАЖНО: На интервале +16%..+28% сразу после взятия TP1 НЕ ставим тесный пол +3%,
            # который убивал мунбэг за 16 секунд на первом же спредовом тике (кейсы farmsem, DUGE)!
            # Мунбэг защищён снизу подтверждённым безубытком -1.5% (секция 1.5 ниже).
            _floor_pnl = None
            _trail_dist = None

        if _floor_pnl is not None and minutes_held >= 0.45:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0

            if pnl_pct >= 0.02:
                if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                    _tc = int(getattr(position, "mature_trail_confirm", 0) or 0) + 1
                    position.mature_trail_confirm = _tc
                    if _tc >= 2:
                        return f"Mature Trailing Profit ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                elif pnl_pct <= _floor_pnl:
                    _fc = int(getattr(position, "mature_floor_confirm", 0) or 0) + 1
                    position.mature_floor_confirm = _fc
                    if _fc >= 2:
                        return f"Mature Profit Floor ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                else:
                    position.mature_trail_confirm = 0
                    position.mature_floor_confirm = 0
            else:
                position.mature_trail_confirm = 0
                position.mature_floor_confirm = 0

        # =========================================================================
        # 1.4. PRE-TP1 BREAKEVEN SHIELD (ЗАЩИТА СДЕЛОК С ПИКОМ +9.5%..+16%)
        # После достижения +9.5% пика при развороте <= +2.2% (2 тика подряд) закрываем сделку в плюс.
        # =========================================================================
        if not _is_mb and max_pnl_pct >= _be_act and minutes_held >= 0.65:
            if pnl_pct <= 0.022:
                _pbe = int(getattr(position, "pre_tp_be_confirm", 0) or 0) + 1
                position.pre_tp_be_confirm = _pbe
                if _pbe >= 2 or pnl_pct <= -0.025:
                    return f"Mature Breakeven Shield ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            else:
                position.pre_tp_be_confirm = 0

        # =========================================================================
        # 1.5. БЕЗУБЫТОК ДЛЯ МУНБЭГА ПОСЛЕ ВЗЯТИЯ TP1 (+16% уже в кармане на 40% объёма!)
        # Раньше 1-тиковый выход на <= +2.5% душил ракеты за 16 секунд (farmsem +6.2%, DUGE +4.0%).
        # Теперь даём 20с передышки после TP1 и держим пол -1.5% с 3-тиковым подтверждением:
        # суммарная сделка гарантированно остаётся в чистом плюсе (~+4.5%..+5.5%), а ракета переживает ретест!
        # =========================================================================
        if _is_mb and max_pnl_pct >= _tp1_thr:
            _since_tp1 = now_ts - float(getattr(position, "last_tp_ts", 0.0) or 0.0)
            if pnl_pct <= -0.08:
                return f"Mature Breakeven ({pnl_pct*100:+.1f}% hard reversal after TP1)"
            if _since_tp1 >= 20.0 and pnl_pct <= -0.015:
                _mb_be = int(getattr(position, "mb_be_confirm_n", 0) or 0) + 1
                position.mb_be_confirm_n = _mb_be
                if _mb_be >= 3:
                    return f"Mature Breakeven ({pnl_pct*100:+.1f}% locked after TP1)"
            elif pnl_pct > -0.015:
                position.mb_be_confirm_n = 0

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
        # 3. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (БЕЗ ПРОВАЛОВ -42% В ПЕРВУЮ МИНУТУ!)
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.18)))
        if pnl_pct <= _emerg_cap:
            _em_c = int(getattr(position, "emerg_confirm_n", 0) or 0) + 1
            position.emerg_confirm_n = _em_c
            # При реальном провале пула режем сразу без задержек (кейс Burn / STONKS):
            # при <= -20% или повторном тике отсекаем мгновенно, не допуская слива до -32%..-42%
            if pnl_pct <= -0.20 or _em_c >= 2:
                return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"
        else:
            position.emerg_confirm_n = 0

        _base_sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', 0.135)))
        # Если пул глубокий ($22k+), есть соцсети и покупатели продолжают выкупать пролив (кейс DEBTDOG -14% -> +46%),
        # даём буфер до -15.5% против сноса стопов маркетмейкером
        _has_organic_bid = False
        _feats = getattr(position, "ml_features", None) or {}
        if isinstance(_feats, dict) and _feats:
            _f_liq = float(_feats.get("liquidity", 0) or 0)
            _f_bs = float(_feats.get("buy_sell_ratio", 0) or 0)
            _f_links = int(_feats.get("links_count", 0) or (1 if _feats.get("socials") else 0))
            if _f_liq >= 22000.0 and _f_bs >= 1.28 and _f_links >= 1:
                _has_organic_bid = True
        _sl = min(_base_sl, -0.155) if _has_organic_bid else _base_sl
        if pnl_pct <= _sl and (minutes_held >= 0.25 or pnl_pct <= -0.16):
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            if _sl_c >= 2 or pnl_pct <= -0.16:
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 4. ВЫХОД ПО СТАГНАЦИИ
        # =========================================================================
        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 360.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.095))
        _buyers_active_stall = False
        if isinstance(_pd, dict) and _pd:
            _tx_st = (_pd.get("txns") or {}).get("m5") or {}
            _b_st = int(_tx_st.get("buys", 0) or 0)
            _s_st = int(_tx_st.get("sells", 0) or 0)
            if _b_st >= 25 and _b_st >= _s_st * 1.20:
                _buyers_active_stall = True
        _eff_stall_sec = (_stall_sec + 180.0) if _buyers_active_stall else _stall_sec
        if minutes_held >= (_eff_stall_sec / 60.0) and max_pnl_pct < 0.05 and pnl_pct <= _stall_loss:
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

        if pnl <= -0.16:
            return "Moonshot Hard Stop (-16%)"

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
