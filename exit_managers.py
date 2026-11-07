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
        # 1. ФИКСАЦИЯ ПРИБЫЛИ (PROFIT FLOOR & TRAILING STOP ДЛЯ РАКЕТ + ЗАЩИТА +8.5%..+12%)
        # =========================================================================
        _is_mb = getattr(position, "is_moonbag", False)
        _tp1_thr = float(getattr(config, "TP1_TRIGGER_PCT", 0.12))
        _be_act = float(getattr(config, "BREAKEVEN_PCT", 0.085))
        _floor_pnl = None
        _trail_dist = None

        if max_pnl_pct >= 5.00:
            _floor_pnl = round(max_pnl_pct * 0.70, 2)
            _trail_dist = 0.22
        elif max_pnl_pct >= 2.00:
            _floor_pnl = 1.35
            _trail_dist = 0.20
        elif max_pnl_pct >= 1.00:
            _floor_pnl = 0.60
            _trail_dist = 0.18
        elif max_pnl_pct >= 0.50:
            _floor_pnl = 0.28
            _trail_dist = 0.15
        elif max_pnl_pct >= 0.25:
            _floor_pnl = 0.12
            _trail_dist = 0.13
        elif _is_mb and max_pnl_pct >= _tp1_thr:
            # Для мунбэгов после взятого TP1 (+12%) держим пол +3.0% и просторный трейлинг 14%
            _floor_pnl = 0.03
            _trail_dist = 0.14
        else:
            _floor_pnl = None
            _trail_dist = None

        if _floor_pnl is not None and minutes_held >= 0.5:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0

            if pnl_pct >= 0.025:
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

            # Если был серьезный пик (>= TP1), но монета полностью сдулась и ушла ниже -3.5% — выходим с минимальным риском
            if max_pnl_pct >= _tp1_thr and pnl_pct <= -0.035 and minutes_held >= 1.0:
                _pc_cut = int(getattr(position, "post_peak_cut_confirm", 0) or 0) + 1
                position.post_peak_cut_confirm = _pc_cut
                if _pc_cut >= 2:
                    return f"Post-Peak Reversal Cut (+{max_pnl_pct*100:.1f}% peak -> {pnl_pct*100:.1f}%)"
            else:
                position.post_peak_cut_confirm = 0

        # =========================================================================
        # 1.4. PRE-TP1 BREAKEVEN SHIELD (ЗАЩИТА СДЕЛОК С ПИКОМ +8.5%..+12%)
        # Раньше любая сделка с ростом +9%..+17% не имела безубытка и скатывалась до -9%..-14%!
        # Теперь после достижения +8.5% пика даём монете спокойно дышать выше +2.2%,
        # но при развороте <= +2.2% (2 тика подряд) закрываем сделку в гарантированный плюс!
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
        # 1.5. БЕЗУБЫТОК ДЛЯ ПОЗИЦИЙ ПОСЛЕ ВЗЯТИЯ TP1
        # Гарантирует, что монета после частичной фиксации (+12%) никогда не уйдёт в минус!
        # =========================================================================
        if _is_mb and max_pnl_pct >= _tp1_thr and pnl_pct <= 0.025:
            return f"Mature Breakeven ({pnl_pct*100:+.1f}% locked after TP1)"

        # =========================================================================
        # 2. PRE-DUMP FRONT-RUNNER (Выход при развороте стакана ТОЛЬКО при солидной прибыли >= +15%)
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

            if minutes_held >= 2.0 and pnl_pct >= 0.15 and total_tx5 >= 20 and bs_ratio < 0.65 and m1 <= -3.0:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"

        # =========================================================================
        # 3. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (РИСК-МЕНЕДЖМЕНТ)
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.22)))
        if pnl_pct <= _emerg_cap:
            _em_c = int(getattr(position, "emerg_confirm_n", 0) or 0) + 1
            position.emerg_confirm_n = _em_c
            # На 0-й минуте требуем минимум 2 тика подтверждения (или обвал <= -55%),
            # чтобы 1 отстающий тик DexScreener не закрывал живую сделку в -31% за 0м (кейс Burn)
            if pnl_pct <= -0.55 or (minutes_held >= 0.50 and _em_c >= 2) or _em_c >= 3:
                return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"
        else:
            position.emerg_confirm_n = 0

        _sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', 0.15)))
        if pnl_pct <= _sl and minutes_held >= 0.75:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            if _sl_c >= 2:
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
