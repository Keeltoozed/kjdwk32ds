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
        # 1. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (EMERGENCY CAP & STOP LOSS) — ПЕРВЫЙ ПРИОРИТЕТ!
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.15)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"

        _base_sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', -0.10)))
        _has_organic_bid = False
        _feats = getattr(position, "ml_features", None) or {}
        if isinstance(_feats, dict) and _feats:
            _f_liq = float(_feats.get("liquidity", 0) or 0)
            _f_bs = float(_feats.get("buy_sell_ratio", 0) or 0)
            _f_links = int(_feats.get("links_count", 0) or (1 if _feats.get("socials") else 0))
            if _f_liq >= 25000.0 and _f_bs >= 1.35 and _f_links >= 1:
                _has_organic_bid = True
        _sl = min(_base_sl, -0.12) if _has_organic_bid else _base_sl
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            if _sl_c >= 2 or pnl_pct <= -0.15 or minutes_held >= 0.75:
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 2. РАКЕТНЫЙ ТРЕЙЛИНГ-СТОП И PROFIT FLOOR (ТОЛЬКО ДЛЯ КРУПНЫХ ДВИЖЕНИЙ)
        # =========================================================================
        # Никакой копеечной микро-фиксации на $4 билете!
        # До +35% пика монета свободно дышит на любых откатах, чтобы не резать ракету на старте.
        # Трейлинг и полы прибыли включаются ТОЛЬКО от +35% и выше с широкой дистанцией!
        _floor_pnl = None
        _trail_dist = None

        if max_pnl_pct >= 5.00:         # +500%+ (6x+ гипер-ракета)
            _floor_pnl = round(max_pnl_pct * 0.72, 2)
            _trail_dist = 0.22
        elif max_pnl_pct >= 2.50:       # +250%..+500% (Democat +300% -> фиксируем +140%..+240% чистыми!)
            _floor_pnl = 1.40           # Гарантируем минимум +140%
            _trail_dist = 0.20          # Трейлинг 20% от пика
        elif max_pnl_pct >= 1.00:       # +100%..+250% (NEX Ai +249% -> фиксируем +60%..+190% чистыми!)
            _floor_pnl = 0.60           # Гарантируем минимум +60%
            _trail_dist = 0.18          # Трейлинг 18% от пика
        elif max_pnl_pct >= 0.50:       # +50%..+100% (JENSEN +50.4%, Horned cat +93% -> фиксируем +25%..+70%!)
            _floor_pnl = 0.25           # Гарантируем минимум +25%
            _trail_dist = 0.18          # Трейлинг 18% от пика
        elif max_pnl_pct >= 0.35:       # +35%..+50% (первый рубеж ракеты)
            _floor_pnl = 0.10           # Гарантируем минимум +10%
            _trail_dist = 0.18          # Широкий трейлинг 18% от пика

        if _floor_pnl is not None and minutes_held >= 0.35:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            _tag = "Moonbag" if _is_mb else "Full Runner"
            if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                return f"Mature {_tag} Trailing ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif pnl_pct <= _floor_pnl:
                return f"Mature {_tag} Floor ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 2.5. BREAKEVEN SHIELD (Только после реального разгона >= +30%)
        # =========================================================================
        _be_act = float(getattr(config, "BREAKEVEN_PCT", 0.30))
        if max_pnl_pct >= _be_act and minutes_held >= 0.50:
            if pnl_pct <= 0.015:
                _sign = "+" if pnl_pct >= 0 else ""
                return f"Mature Breakeven Shield ({_sign}{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # Безубыток для мунбэга:
        if _is_mb:
            if pnl_pct <= 0.010:
                return f"Mature Moonbag Breakeven (+{pnl_pct*100:.1f}% locked after TP1)"

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
