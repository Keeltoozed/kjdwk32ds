import time

import config


class MatureExitManager:
    """Exit-логика для зрелых монет (swing). Возвращает причину выхода или None."""

    @staticmethod
    def evaluate_exit(position, current_price):
        if not hasattr(position, 'entry_price_usd') or position.entry_price_usd <= 0:
            return None

        pnl_pct = (current_price - position.entry_price_usd) / position.entry_price_usd

        if not hasattr(position, 'max_price_usd') or position.max_price_usd <= 0:
            position.max_price_usd = current_price
        elif current_price > position.max_price_usd:
            position.max_price_usd = current_price

        now_ts = time.time()
        et = getattr(position, 'entry_time', now_ts)
        # entry_time может быть float (live) или datetime/Timestamp (бектест)
        if hasattr(et, 'timestamp'):
            try:
                et = et.timestamp()
            except Exception:
                et = now_ts
        try:
            minutes_held = (now_ts - float(et)) / 60
        except Exception:
            minutes_held = 0

        # Аварийный кэп ПЕРВЫМ: гэпы типа FIBONACCI -68% проскакивают обычный стоп
        if pnl_pct <= -0.30:
            return f"Mature Emergency Cap ({pnl_pct*100:.0f}%)"

        # Infant guard: дамп в первые минуты = дев сливает, не шум.
        # Было held<3 / -12%: резало SWORDGUY/LAP (-15..-18% за 30-70с) с живым
        # импульсом (m5 +16..+68%, 1000+ buys). Раги идут -40..-96% — их и -25% ловит.
        _inf_win = float(getattr(config, "INFANT_WINDOW_MIN", 2.0))
        _inf_pct = float(getattr(config, "INFANT_DUMP_PCT", -0.22))
        if minutes_held < _inf_win and pnl_pct <= _inf_pct:
            return f"Mature Infant Dump ({pnl_pct*100:.0f}%)"

        # 1. ГАРАНТИРОВАННЫЙ НЕСНИЖАЕМЫЙ ПОРОГ ПРИБЫЛИ (PROFIT FLOOR LOCK)
        max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd else 0
        _floor_pnl = None
        if max_pnl_pct >= 2.00:       # Пик +200%+ (3x) -> гарантируем минимум +100% (удвоение)
            _floor_pnl = 1.00
        elif max_pnl_pct >= 1.00:     # Пик +100%+ (2x) -> гарантируем минимум +50%
            _floor_pnl = 0.50
        elif max_pnl_pct >= 0.60:     # Пик +60%+ -> гарантируем минимум +30% (18.7% воздуха от пика)
            _floor_pnl = 0.30
        elif max_pnl_pct >= 0.40:     # Пик +40%+ -> гарантируем минимум +20% (14.3% воздуха от пика)
            _floor_pnl = 0.20
        elif max_pnl_pct >= 0.25:     # Пик +25%+ -> гарантируем минимум +10% (12% воздуха от пика)
            _floor_pnl = 0.10
        elif max_pnl_pct >= 0.15:     # Пик +15%+ -> гарантируем минимум +4% (комиссии окуплены)
            _floor_pnl = 0.04
        if _floor_pnl is not None and pnl_pct <= _floor_pnl:
            if pnl_pct >= 0:
                return f"Mature Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%)"
            return f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.0f}%)"

        # 2. ОСНОВНОЙ ПЛОТНЫЙ ТРЕЙЛИНГ-СТОП (Динамическая фиксация позиции)
        if hasattr(config, 'TRAILING_ACTIVATION_PCT'):
            if max_pnl_pct >= getattr(config, 'TRAILING_ACTIVATION_PCT', 0.15):
                if max_pnl_pct >= 3.0:
                    _trail_dist = 0.25
                elif max_pnl_pct >= 2.0 or getattr(position, "is_moonbag", False):
                    _trail_dist = 0.20
                elif max_pnl_pct >= 1.0:
                    _trail_dist = float(getattr(config, "TRAILING_RUNNER_DISTANCE_PCT", 0.16))
                elif max_pnl_pct >= 0.50:
                    _trail_dist = 0.14
                elif max_pnl_pct >= 0.25:
                    _trail_dist = 0.12
                else:
                    _trail_dist = float(getattr(config, "TRAILING_DISTANCE_PCT", 0.10))

                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if drop_from_max >= _trail_dist:
                    return f"Mature Trailing (+{max_pnl_pct*100:.0f}% peak, drop {drop_from_max*100:.0f}%)"
                    
        # Scalp Trailing (для мелких профитов) - ВЫКЛЮЧЕН: не режем ракеты на первом 5% откате при пике +10%
        if getattr(config, 'SCALP_PROFIT_ENABLED', False) and hasattr(config, 'TRAILING_ACTIVATION_PCT'):
            max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd
            if max_pnl_pct >= 0.10 and max_pnl_pct < config.TRAILING_ACTIVATION_PCT:
                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if drop_from_max >= 0.05:
                    return f"Scalp Profit (peak +{max_pnl_pct*100:.0f}%)"

        try:
            _maxp = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd
        except Exception:
            _maxp = 0
        # Мягкий стоп: был пик +10%+, сползли к -5% — импульс угас, фиксируем малый минус
        if _maxp >= 0.10 and pnl_pct <= -0.05:
            return f"Mature Soft Guard (был пик +{_maxp*100:.0f}%, слив до {pnl_pct*100:.1f}%)"
        # Breakeven: если был пик +12%+ и цена вернулась ко входу — импульс сдох,
        # выходим в безубыток (+0.5%..0%), а не ждём стоп -14%..-30%.
        _be = min(float(getattr(config, 'BREAKEVEN_PCT', 0.20)), 0.12)
        if _maxp >= _be and pnl_pct <= 0.005:
            return f"Mature Breakeven (был пик +{_maxp*100:.0f}%)"

        # Hard stop loss
        if hasattr(config, 'STOP_LOSS_PCT') and pnl_pct <= config.STOP_LOSS_PCT:
            return f"Mature Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)"

        # Time exit - Stagnant: минус режем быстро (он тянет вниз),
        # мелкий плюс держим до STAGNANT_HOLD_MIN - даём ракете время (раньше резали и его)
        _loss_min = getattr(config, 'STAGNANT_LOSS_MIN', 7)
        _hold_min = getattr(config, 'STAGNANT_HOLD_MIN', 25)
        if minutes_held >= _loss_min and pnl_pct < 0:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m)"
        if minutes_held >= _hold_min and pnl_pct < 0.05:
            return f"Stagnant Cut ({minutes_held:.0f}m)"
            
        # Old fallback
        if hasattr(config, 'TIME_EXIT_MINUTES') and hasattr(config, 'TIME_EXIT_PROFIT_REQ'):
            if minutes_held >= config.TIME_EXIT_MINUTES and pnl_pct < config.TIME_EXIT_PROFIT_REQ:
                return "Mature Time Exit"

        return None

class TrailingExitManager:
    def __init__(self, trail_pct=0.12, min_profit=0.40):
        self.trail_pct = trail_pct
        self.min_profit = min_profit
    
    def evaluate_exit(self, position, current_price):
        entry = position.entry_price_usd
        pnl = (current_price - entry) / entry
        max_pnl = (position.max_price_usd - entry) / entry
        
        # Hard stop -40% (MOONSHOT_CONFIG)
        if pnl <= -0.40:
            return "Moonshot Hard Stop (-40%)"
        
        # Частичный тейк после +40% минимального профита
        if max_pnl >= self.min_profit and not getattr(position, 'tp_1_hit', False):
            position.tp_1_hit = True
            return "Moonshot TP +40% (30% продано)"
        
        # Trailing 12% после первого TP (не даём сливать 30% прибыли)
        if getattr(position, 'tp_1_hit', False):
            trail_stop = position.max_price_usd * (1 - self.trail_pct)
            if current_price <= trail_stop:
                return f"Moonshot Trailing Stop (-{int(self.trail_pct*100)}% от пика)"
        
        # Дополнительные уровни тейка (5x/20x/100x) — симулировано в backtest
        for pct, label in [(5.0, "5x"), (20.0, "20x"), (100.0, "100x")]:
            if max_pnl >= pct and not getattr(position, f'tp_{label}_hit', False):
                setattr(position, f'tp_{label}_hit', True)
                return f"Moonshot TP +{pct*100:.0f}% ({label})"
        
        return None
