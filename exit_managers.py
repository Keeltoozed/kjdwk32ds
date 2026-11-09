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
            # Мгновенный выход на 1-м же тике при падении ликвидности > 40% (защита от вывода SOL девом)!
            if _peak_l >= 5000.0 and 0 < _cur_l < (_peak_l * 0.60):
                return f"Early Liquidity Drop Escape (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
            else:
                position._liq_drop_streak_em = 0

        # =========================================================================
        # 0.5. NARRATIVE / WORD SNIPER FAST EXIT ("НА ПЕРВОЙ ПРИБЫЛИ ПРОДАВАТЬ")
        # =========================================================================
        _src = str(getattr(position, "source", "") or "")
        _is_narrative = _src.startswith("NARRATIVE:")
        if _is_narrative:
            # 1. Защита от слива кривой (жесткий быстрый стоп -14%)
            _nar_sl = float(getattr(config, "NARRATIVE_STOP_LOSS_PCT", -0.14))
            if pnl_pct <= _nar_sl:
                return f"Narrative Fast Stop ({pnl_pct*100:.1f}%)"

            # 2. Мгновенный безубыток на стартовом импульсе (+10% пика)
            if max_pnl_pct >= 0.10 and minutes_held >= 0.05:
                if pnl_pct <= 0.020:
                    if pnl_pct >= 0.0:
                        return f"Narrative Breakeven Shield (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                    else:
                        return f"Narrative Crash Dump (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.1f}%)"

            # 3. Фиксация первой прибыли (быстро забираем плюс на пампе новости)
            drop = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            if max_pnl_pct >= 0.50:       # Выстрел +50%+
                if drop >= 0.10 or pnl_pct <= 0.28:
                    return f"Narrative Moonshot Take (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif max_pnl_pct >= 0.25:     # Памп +25%..+50%
                if drop >= 0.08 or pnl_pct <= 0.14:
                    return f"Narrative Fast Take (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif max_pnl_pct >= 0.14:     # Первый плюс +14%..+25% -> мгновенно фиксируем +6%..+10% чистыми!
                if drop >= 0.06 or pnl_pct <= 0.06:
                    return f"Narrative First Profit Take (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

            # 4. Сброс зависшей новости: если за 90 сек нет пампа и пошёл откат (pnl <= -6%) — выходим
            _nar_max_hold = float(getattr(config, "NARRATIVE_MAX_HOLD_SEC", 90.0)) / 60.0
            if minutes_held >= _nar_max_hold and pnl_pct <= -0.06 and max_pnl_pct < 0.08:
                return f"Narrative Stall Cut ({minutes_held*60:.0f}s no pump, pnl {pnl_pct*100:.1f}%)"
            if minutes_held >= 5.0 and pnl_pct >= 0.02:
                return f"Narrative Time Profit (+{pnl_pct*100:.1f}% at {minutes_held:.1f}m)"

            return None

        # =========================================================================
        # 1. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (EMERGENCY CAP & STOP LOSS) — ПЕРВЫЙ ПРИОРИТЕТ!
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.22)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"

        _base_sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', -0.16)))
        _has_organic_bid = False
        _feats = getattr(position, "ml_features", None) or {}
        if isinstance(_feats, dict) and _feats:
            _f_liq = float(_feats.get("liquidity", 0) or 0)
            _f_bs = float(_feats.get("buy_sell_ratio", 0) or 0)
            _f_links = int(_feats.get("links_count", 0) or (1 if _feats.get("socials") else 0))
            if _f_liq >= 25000.0 and _f_bs >= 1.35 and _f_links >= 1:
                _has_organic_bid = True
        _sl = min(_base_sl, -0.18) if _has_organic_bid else _base_sl
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            if _sl_c >= 2 or pnl_pct <= -0.20 or minutes_held >= 0.5:
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 2. BREAKEVEN SHIELD (ПРАВИЛО ИЗ 0d2b1ae: ЗЕЛЕНАЯ СДЕЛКА НЕ СТАНОВИТСЯ КРАСНОЙ!)
        # =========================================================================
        # Если монета брала импульс >= +14%, стоп БЕЗУСЛОВНО переносится в безубыток +2.0%!
        # Предотвращает слив ракет вроде QUAKE (+34.4% -> -9.1%), PAYDAY (+35.8% -> -11.8%)!
        _be_act = float(getattr(config, "BREAKEVEN_PCT", 0.14))
        if max_pnl_pct >= _be_act and minutes_held >= 0.08:
            if pnl_pct <= 0.020:
                if pnl_pct >= 0.0:
                    return f"Mature Breakeven Shield (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                else:
                    return f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.1f}%)"

        # Безубыток для мунбэга:
        if _is_mb:
            if pnl_pct <= 0.010:
                return f"Mature Moonbag Breakeven (+{pnl_pct*100:.1f}% locked after TP1)"

        # =========================================================================
        # 3. ТРЕЙЛИНГ-СТОП И СТУПЕНЧАТЫЙ PROFIT FLOOR (СЕКРЕТ ПРОФИТА ИЗ 0d2b1ae)
        # =========================================================================
        # При достижении ступеней фиксации бот гарантирует прибыль и трейлит от пика!
        _floor_pnl = None
        _trail_dist = None

        if max_pnl_pct >= 5.00:         # +500%+ (6x+ гипер-ракета)
            _floor_pnl = round(max_pnl_pct * 0.75, 2)
            _trail_dist = 0.20
        elif max_pnl_pct >= 2.50:       # +250%..+500% (Democat +300% -> фиксируем +150%..+240% чистыми!)
            _floor_pnl = 1.50           # Гарантируем минимум +150%
            _trail_dist = 0.15          # Плотный трейлинг 15% от пика
        elif max_pnl_pct >= 1.00:       # +100%..+250% (NEX Ai +249% -> фиксируем +65%..+190% чистыми!)
            _floor_pnl = 0.65           # Гарантируем минимум +65%
            _trail_dist = 0.16          # Трейлинг 16% от пика
        elif max_pnl_pct >= 0.50:       # +50%..+100% (Horned cat +93%, JENSEN +50.4% -> фиксируем +28%..+70%!)
            _floor_pnl = 0.28           # Гарантируем минимум +28%
            _trail_dist = 0.16          # Трейлинг 16% от пика
        elif max_pnl_pct >= 0.25:       # +25%..+50% (QUAKE +34.4%, NATO +30.1%, MAIN +66% -> фиксируем +12%..+20%!)
            _floor_pnl = 0.12           # Гарантируем минимум +12%
            _trail_dist = 0.14          # Трейлинг 14% от пика
        elif max_pnl_pct >= 0.14:       # +14%..+25% (ранний импульс — кейсы Cupsey +18%, MSTRT +21%)
            _floor_pnl = 0.05           # Гарантируем минимум +5% чистыми
            _trail_dist = 0.10          # Трейлинг 10% от пика

        if _floor_pnl is not None and minutes_held >= 0.08:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            _tag = "Moonbag" if _is_mb else "Full Runner"
            if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                return f"Mature {_tag} Trailing ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif pnl_pct <= _floor_pnl:
                if pnl_pct >= 0:
                    return f"Mature {_tag} Floor (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                else:
                    return f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.1f}%)"

        # =========================================================================
        # 4. PARABOLIC MOMENTUM HARVEST (ФИКСАЦИЯ ПРИ ЗАСТРЕВАНИИ НА ВЕРШИНЕ)
        # =========================================================================
        if max_pnl_pct >= 2.00 and _peak_age_min >= 4.0:
            return f"Parabolic 3x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if max_pnl_pct >= 1.00 and _peak_age_min >= 5.0:
            return f"Parabolic 2x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if max_pnl_pct >= 0.40 and _peak_age_min >= 7.0:
            return f"Rocket Momentum Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if max_pnl_pct >= 0.20 and _peak_age_min >= 9.0 and pnl_pct >= 0.05:
            return f"Breakout Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        # =========================================================================
        # 5. FRONT-RUN PROFIT EXIT (Разворот стакана при профите >= +20%)
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
        # 6. MAX HOLD TIMEOUT (Защита от кейса UNEMPLOYED 57м)
        # =========================================================================
        if minutes_held >= 25.0 and pnl_pct >= 0.02:
            return f"Max Hold Profit Take (+{pnl_pct*100:.1f}% locked at {minutes_held:.0f}m)"
        if minutes_held >= 30.0:
            return f"Max Hold Expiry ({pnl_pct*100:+.1f}% after {minutes_held:.0f}m)"

        # =========================================================================
        # 7. СТАГНАЦИЯ И МЕРТВЫЙ ИМПУЛЬС
        # =========================================================================
        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 210.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.075))
        if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.04 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        if minutes_held >= 15.0 and pnl_pct <= -0.05 and max_pnl_pct < 0.04:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"
        if minutes_held >= 20.0 and pnl_pct < 0.015 and max_pnl_pct < 0.06:
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
