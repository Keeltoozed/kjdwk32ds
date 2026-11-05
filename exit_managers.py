import time
import config


class MatureExitManager:
    """Exit-логика для сбора всех зеленых импульсов и защиты от мелких минусов."""

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
        # 1. РАННИЙ ЗАХВАТ ПРИБЫЛИ И ТРЕЙЛИНГ (ЗАКРЫВАЕТ СЛЕПУЮ ЗОНУ +5.5%..+15%)
        # =========================================================================
        _is_mb = getattr(position, "is_moonbag", False)
        _floor_pnl = None
        _trail_dist = None

        if max_pnl_pct >= 5.00:
            _floor_pnl = round(max_pnl_pct * 0.60, 2)
            _trail_dist = 0.14
        elif max_pnl_pct >= 3.00:
            _floor_pnl = round(max_pnl_pct * 0.50, 2)
            _trail_dist = 0.14
        elif max_pnl_pct >= 2.00:
            _floor_pnl = 0.90
            _trail_dist = 0.15
        elif max_pnl_pct >= 1.00:
            _floor_pnl = 0.52
            _trail_dist = 0.15
        elif max_pnl_pct >= 0.50:
            _floor_pnl = 0.28
            _trail_dist = 0.12
        elif max_pnl_pct >= 0.25:
            _floor_pnl = 0.15
            _trail_dist = 0.08
        elif max_pnl_pct >= 0.15:
            _floor_pnl = 0.085
            _trail_dist = 0.060
        elif max_pnl_pct >= 0.095:
            _floor_pnl = 0.048
            _trail_dist = 0.042
        elif max_pnl_pct >= 0.055 or _is_mb:
            # Главная защита от мелких минусов: увидели +5.5% -> пол +2.2% (чистый плюс!)
            _floor_pnl = 0.022
            _trail_dist = 0.032

        if _floor_pnl is not None:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            if _trail_dist is not None and drop_from_max >= _trail_dist:
                if pnl_pct >= _floor_pnl:
                    return f"Mature Trailing Profit (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%, drop {drop_from_max*100:.1f}%)"
                elif pnl_pct >= 0:
                    return f"Mature Profit Floor (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                else:
                    return f"Post-Peak Protection Cut (+{max_pnl_pct*100:.1f}% peak -> {pnl_pct*100:.1f}%)"

            if pnl_pct <= _floor_pnl:
                if pnl_pct >= 0:
                    return f"Mature Profit Floor (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
                return f"Post-Peak Protection Cut (+{max_pnl_pct*100:.1f}% peak -> {pnl_pct*100:.1f}%)"

        # =========================================================================
        # 1.5. MICRO-PROFIT HARVESTER (СБОР ЗАСТРЯВШИХ ПЛЮСОВ +2.0%..+5.4%)
        # Если монета в плюсе 2.5+ минуты, но импульс затих (нет нового пика 1.5 мин) — забираем плюс!
        # =========================================================================
        if minutes_held >= 2.5 and 0.020 <= pnl_pct < 0.055:
            if _peak_age_min >= 1.4 or (max_pnl_pct >= 0.038 and (max_pnl_pct - pnl_pct) >= 0.015):
                return f"Micro Profit Harvest (+{pnl_pct*100:.1f}% locked, peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 2. PRE-DUMP FRONT-RUNNER (Выход в плюс при давлении продавцов)
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

            if minutes_held >= 1.5 and pnl_pct >= 0.025 and total_tx5 >= 15 and bs_ratio < 0.72 and m1 <= -2.0:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"

        # =========================================================================
        # 3. СТОПЫ И АВАРИЙНЫЕ КЭПЫ ДЛЯ УБЫТОЧНЫХ СДЕЛОК
        # =========================================================================
        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.22)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.0f}%)"

        _inf_win = float(getattr(config, "INFANT_WINDOW_MIN", 1.0))
        _inf_pct = float(getattr(config, "INFANT_DUMP_PCT", -0.42))
        if minutes_held < _inf_win and pnl_pct <= _inf_pct:
            if minutes_held >= 0.75 or pnl_pct <= -0.65:
                return f"Mature Infant Dump ({pnl_pct*100:.0f}%)"

        _sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', 0.14)))
        if pnl_pct <= _sl:
            if minutes_held >= 1.0 or pnl_pct <= -0.22:
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"

        # =========================================================================
        # 4. УМНЫЙ ТАЙМ-АУТ СТАГНАЦИИ (НЕ РЕЖЕТ БОКОВИКИ ЧЕРЕЗ 5 МИНУТ В МИНУС!)
        # =========================================================================
        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 720.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.14))
        if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.05 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        _loss_min = float(getattr(config, 'STAGNANT_LOSS_MIN', 14.0))
        _hold_min = float(getattr(config, 'STAGNANT_HOLD_MIN', 20.0))

        # Если через 8+ минут сделка в любом чистом плюсе (> +1.2%), но стоит на месте — закрываем в плюс!
        if minutes_held >= 8.0 and pnl_pct >= 0.012 and _peak_age_min >= 4.0:
            return f"Stagnant Profit Harvest (+{pnl_pct*100:.1f}% after {minutes_held:.0f}m)"

        # В минус закрываем по стагнации ТОЛЬКО после 14+ минут, если вообще не было импульса (<4%) и просадка хуже -6%
        if minutes_held >= _loss_min and pnl_pct <= -0.06 and max_pnl_pct < 0.04:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"

        if minutes_held >= _hold_min and pnl_pct < 0.01 and max_pnl_pct < 0.06:
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

        if pnl <= -0.25:
            return "Moonshot Hard Stop (-25%)"

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

