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
        _inf_pct = float(getattr(config, "INFANT_DUMP_PCT", -0.25))
        if minutes_held < _inf_win and pnl_pct <= _inf_pct:
            return f"Mature Infant Dump ({pnl_pct*100:.0f}%)"

        # 1. ГАРАНТИРОВАННЫЙ НЕСНИЖАЕМЫЙ ПОРОГ ПРИБЫЛИ (PROFIT FLOOR LOCK)
        # Не душим ракеты на старте (+15..+30%, кейсы ELON +184% и SEXCOIN +258%):
        # фиксация пола включается только от +35% (после TP1 30%) с запасом ~20% на рабочую коррекцию.
        max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd else 0
        _floor_pnl = None
        if max_pnl_pct >= 3.00:       # Пик +300%+ (4x) -> гарантируем минимум +235%
            _floor_pnl = 2.35
        elif max_pnl_pct >= 2.00:     # Пик +200%+ (3x) -> гарантируем минимум +150%
            _floor_pnl = 1.50
        elif max_pnl_pct >= 1.50:     # Пик +150%+ (2.5x) -> гарантируем минимум +110%
            _floor_pnl = 1.10
        elif max_pnl_pct >= 1.00:     # Пик +100%+ (2x) -> гарантируем минимум +70%
            _floor_pnl = 0.70
        elif max_pnl_pct >= 0.70:     # Пик +70%+ -> гарантируем минимум +45%
            _floor_pnl = 0.45
        elif max_pnl_pct >= 0.50:     # Пик +50%+ -> гарантируем минимум +28%
            _floor_pnl = 0.28
        elif max_pnl_pct >= 0.35:     # Пик +35%+ -> гарантируем безубыток +8%
            _floor_pnl = 0.08
        if _floor_pnl is not None and pnl_pct <= _floor_pnl:
            # Защита ТОЛЬКО от 1-тикового глюка вторичного пула (когда из плюса +40%+ цена за 1 тик падает в минус):
            if max_pnl_pct >= 0.40 and pnl_pct <= -0.05:
                _fc = int(getattr(position, "floor_confirm_n", 0) or 0) + 1
                position.floor_confirm_n = _fc
                if _fc < 2:
                    return None
            if pnl_pct >= 0:
                return f"Mature Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%)"
            return f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.0f}%)"
        else:
            position.floor_confirm_n = 0

        # 2. СУЖАЮЩИЙСЯ ПАРАБОЛИЧЕСКИЙ ТРЕЙЛИНГ-СТОП (Чем выше пик, тем плотнее зажимаем профит у вершины!)
        if hasattr(config, 'TRAILING_ACTIVATION_PCT'):
            if max_pnl_pct >= getattr(config, 'TRAILING_ACTIVATION_PCT', 0.45):
                if max_pnl_pct >= 2.50:
                    _trail_dist = 0.09  # На гипер-пиках (+250%+) режем откат уже на 9% от макушки!
                elif max_pnl_pct >= 1.50:
                    _trail_dist = 0.10  # На +150..+250% держим плотный стоп 10%
                elif max_pnl_pct >= 0.80 or getattr(position, "is_moonbag", False):
                    _trail_dist = float(getattr(config, "TRAILING_RUNNER_DISTANCE_PCT", 0.12))
                else:
                    _trail_dist = float(getattr(config, "TRAILING_DISTANCE_PCT", 0.16))

                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if drop_from_max >= _trail_dist:
                    return f"Mature Trailing (+{max_pnl_pct*100:.0f}% peak, drop {drop_from_max*100:.0f}%)"

        # Runner Stagnation / Inactive Pool (фиксация прибыли при затухании объёмов в пуле)
        _pt = getattr(position, 'peak_time', et)
        if hasattr(_pt, 'timestamp'):
            try:
                _pt = _pt.timestamp()
            except Exception:
                _pt = et
        try:
            _peak_age_min = (now_ts - float(_pt)) / 60
        except Exception:
            _peak_age_min = 0

        _dd_peak = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd else 0
        if max_pnl_pct >= 0.35 and _peak_age_min >= 25 and pnl_pct > 0.05 and _dd_peak >= 0.08:
            return f"Mature Runner Stagnation (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        if minutes_held >= 30 and _peak_age_min >= 25:
            if pnl_pct > 0.10:
                return f"Mature Inactive Pool TP (+{pnl_pct*100:.1f}%, {minutes_held:.0f}m)"
            elif pnl_pct < -0.05:
                return f"Mature Inactive Pool Cut ({pnl_pct*100:.1f}%, {minutes_held:.0f}m)"
                    
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
        # Мягкий стоп (только после 6 минут в рынке, чтобы не резать 1-2 минутные свечи на старте!):
        if minutes_held >= 6.0 and _maxp >= 0.18 and pnl_pct <= -0.08:
            return f"Mature Soft Guard (был пик +{_maxp*100:.0f}%, слив до {pnl_pct*100:.1f}%)"
        # Breakeven: если был пик +25%+ и спустя 4+ мин цена вернулась ко входу — выходим в безубыток
        _be = float(getattr(config, 'BREAKEVEN_PCT', 0.25))
        if minutes_held >= 4.0 and _maxp >= _be and pnl_pct <= 0.01:
            return f"Mature Breakeven (был пик +{_maxp*100:.0f}%)"

        # Zero-Momentum Cut: монета за 3.5+ мин ни разу не дала даже +1.5% и ушла в -10%
        if _maxp < 0.015 and pnl_pct <= -0.10 and minutes_held >= 3.5:
            return f"Mature Zero-Momentum Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"

        # Hard stop loss (после 1.5 мин, чтобы не выбивать ракету на стартовом спреде в 0м)
        if minutes_held >= 1.5 and hasattr(config, 'STOP_LOSS_PCT') and pnl_pct <= config.STOP_LOSS_PCT:
            return f"Mature Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)"

        # Time exit - Stagnant: не перекрываем проверку живого стакана из main.py раньше 15 минут,
        # и режем только реальную просадку <= -5% без импульса (_maxp < +5%), чтобы не убивать консолидацию перед пампом (кейс Chaton +62%)
        _loss_min = getattr(config, 'STAGNANT_LOSS_MIN', 15)
        _hold_min = getattr(config, 'STAGNANT_HOLD_MIN', 25)
        if minutes_held >= max(_loss_min, 15) and pnl_pct <= -0.05 and _maxp < 0.05:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"
        if minutes_held >= _hold_min and pnl_pct < 0.05 and _maxp < 0.15:
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
