import time

import config


class MatureExitManager:
    """Exit-логика для зрелых монет (swing). Возвращает причину выхода или None.

    RIDE-режим (RIDE_RUNNERS_ENABLED, по умолчанию True): для позиций с меткой "RUNNER-" в source
    (маленькие билеты CURVE-RUNNER) пол прибыли и трейлинг расширены. Обычные пороги ($6 сканер) душили
    любую ракету: пол +24% при пике +35%, +80% при +100%, +165% при +200% = просадка от пика всего 8-12%,
    а у мем-ракет откаты 20-40% посреди пути — бот выходил с +50..100% вместо x5-x20.
    Остальные позиции работают по-старому."""

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

        # RIDE-режим для всех импульсных стратегий Solana (GROK-JEV, JUP, RUNNER, VIP, RAY-XGB, PUMP-XGB, SCOUT, STREAM):
        _src = str(getattr(position, 'source', '') or '').upper()
        _ride = bool(getattr(config, 'RIDE_RUNNERS_ENABLED', True)) and (
            not _src or any(k in _src for k in ('GROK', 'JEV', 'JUP', 'RUNNER', 'VIP', 'XGB', 'RAY', 'PUMP', 'SCOUT', 'STREAM', 'FOMO', 'SMART'))
        )

        # Аварийный кэп ПЕРВЫМ: гэпы типа FIBONACCI -68% проскакивают обычный стоп
        # Не режет рабочие 1-минутные тени на старте (-30%..-38%), а ловит только реальные раг-гэпы (-48%+):
        if pnl_pct <= -0.48:
            return f"Mature Emergency Cap ({pnl_pct*100:.0f}%)"

        # Infant guard: дамп в первые минуты = дев сливает, не шум.
        # Режем только катастрофический сброс дева (-35%..-90%), рабочие тени и откаты не трогаем.
        _inf_win = float(getattr(config, "INFANT_WINDOW_MIN", 2.0))
        _inf_pct = float(getattr(config, "INFANT_DUMP_PCT", -0.35))
        if minutes_held < _inf_win and pnl_pct <= _inf_pct:
            return f"Mature Infant Dump ({pnl_pct*100:.0f}%)"

        # GROK BOT x JEV: СТОП ПОД ФИТИЛЬ (по умолчанию ОТКЛЮЧЕН, чтобы не душить ракеты на -15%..-25% в первые минуты)
        if getattr(config, "JEV_WICK_STOP_ENABLED", False):
            _wick_stop = float(getattr(position, "stop_under_wick", 0.0) or 0.0)
            _wick_pct = float(getattr(position, "wick_stop_loss_pct", 0.0) or 0.0)
            if minutes_held >= 2.0:
                _eff_wick_pct = min(-0.16, _wick_pct) if _wick_pct < 0 else -0.18
                _eff_wick_stop = min(_wick_stop, position.entry_price_usd * 0.84) if _wick_stop > 0 else 0.0
                if (_eff_wick_stop > 0 and current_price <= _eff_wick_stop) or pnl_pct <= _eff_wick_pct:
                    _wc = int(getattr(position, "wick_confirm_n", 0) or 0) + 1
                    position.wick_confirm_n = _wc
                    if _wc >= 2:
                        if _eff_wick_stop > 0 and current_price <= _eff_wick_stop:
                            return f"JEV Wick Stop (${current_price:.6f} <= ${_eff_wick_stop:.6f}, {pnl_pct*100:.1f}%)"
                        return f"JEV Wick Stop ({pnl_pct*100:.1f}% <= {_eff_wick_pct*100:.1f}%)"
                else:
                    position.wick_confirm_n = 0

        # 1. ГАРАНТИРОВАННЫЙ НЕСНИЖАЕМЫЙ ПОРОГ ПРИБЫЛИ (PROFIT FLOOR LOCK)
        # Не душим ракеты на старте (+15..+35%, кейсы Chubby +19% -> -1% -> +60%, ELON +184%, SEXCOIN +258%):
        # фиксация пола включается только от +38% с запасом ~23% на рабочую 1-минутную коррекцию.
        max_pnl_pct = (position.max_price_usd - position.entry_price_usd) / position.entry_price_usd if position.entry_price_usd else 0
        _floor_pnl = None
        if _ride:
            # RIDE: пол = доля от пика, допускает откат для полета ракет, включается только от пика +38%
            if max_pnl_pct >= 5.00:       # +500%+: держим 65% прибыли
                _floor_pnl = round(max_pnl_pct * 0.65, 2)
            elif max_pnl_pct >= 3.00:     # +300%..+500%: 60%
                _floor_pnl = round(max_pnl_pct * 0.60, 2)
            elif max_pnl_pct >= 1.50:     # +150%..+300%: 50%
                _floor_pnl = round(max_pnl_pct * 0.50, 2)
            elif max_pnl_pct >= 1.00:     # +100%..+150%: 45% (пик +100% -> пол +45%)
                _floor_pnl = round(max_pnl_pct * 0.45, 2)
            elif max_pnl_pct >= 0.60:     # +60%..+100%: 40% (пик +60% -> пол +24%, пик +80% -> пол +32%)
                _floor_pnl = round(max_pnl_pct * 0.40, 2)
            elif max_pnl_pct >= 0.38:     # +38%..+60%: держим минимум +15% (даём ~23% запаса на ретест и не режем ракету на +19%!)
                _floor_pnl = 0.15
        elif max_pnl_pct >= 5.00:       # Гипер-ракета (+500%+): держим 78% прибыли
            _floor_pnl = round(max_pnl_pct * 0.78, 2)
        elif max_pnl_pct >= 3.00:     # Мега-ракета (+300%..+500%): держим 75% прибыли
            _floor_pnl = max(2.40, round(max_pnl_pct * 0.75, 2))
        elif max_pnl_pct >= 2.00:     # Пик +200%+ (3x) -> гарантируем минимум +165%
            _floor_pnl = 1.65
        elif max_pnl_pct >= 1.50:     # Пик +150%+ (2.5x) -> гарантируем минимум +122%
            _floor_pnl = 1.22
        elif max_pnl_pct >= 1.00:     # Пик +100%+ (2x) -> гарантируем минимум +80%
            _floor_pnl = 0.80
        elif max_pnl_pct >= 0.70:     # Пик +70%+ -> гарантируем минимум +55%
            _floor_pnl = 0.55
        elif max_pnl_pct >= 0.50:     # Пик +50%+ -> гарантируем минимум +32%
            _floor_pnl = 0.32
        elif max_pnl_pct >= 0.38:     # Пик +38%+ -> гарантируем минимум +18% (ниже +38% пол не ставим — даём ракете взлететь!)
            _floor_pnl = 0.18
        if _floor_pnl is not None and pnl_pct <= _floor_pnl:
            # 2-тиковое подтверждение пробоя пола (защита от 1-секундных проколов и теней свечей),
            # НО если это жесткий сброс (слив профита к нулю pnl_pct <= 0.08 при пике >= +38% или откат от хая >= 30%), выходим МГНОВЕННО без ожидания:
            _fc = int(getattr(position, "floor_confirm_n", 0) or 0) + 1
            position.floor_confirm_n = _fc
            _drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd else 0
            _fast_exit = (pnl_pct <= 0.08 and max_pnl_pct >= 0.38) or (_drop_from_max >= 0.30 and max_pnl_pct >= 0.50)
            if _fc < 2 and not _fast_exit:
                return None
            if pnl_pct >= 0:
                return f"Mature Profit Floor (+{_floor_pnl*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%)"
            return f"Crash Dump after peak (+{max_pnl_pct*100:.0f}% -> {pnl_pct*100:.0f}%)"
        else:
            position.floor_confirm_n = 0

        # 2. СУЖАЮЩИЙСЯ ПАРАБОЛИЧЕСКИЙ ТРЕЙЛИНГ-СТОП (Чем выше пик, тем плотнее зажимаем профит у вершины!)
        if hasattr(config, 'TRAILING_ACTIVATION_PCT'):
            if _ride:
                # RIDE: трейлинг с +60%, дистанция 28% (до +300%) и 22% выше — выдерживает рабочие откаты ракеты
                if max_pnl_pct >= 0.60:
                    _trail_dist = 0.22 if max_pnl_pct >= 3.00 else 0.28
                    drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                    if drop_from_max >= _trail_dist:
                        return f"Mature Trailing RIDE (+{max_pnl_pct*100:.0f}% peak, drop {drop_from_max*100:.0f}%)"
            elif max_pnl_pct >= getattr(config, 'TRAILING_ACTIVATION_PCT', 0.40):
                if max_pnl_pct >= 2.50:
                    _trail_dist = 0.08  # На гипер-пиках (+250%+) режем откат уже на 8% от макушки!
                elif max_pnl_pct >= 1.50:
                    _trail_dist = 0.09  # На +150..+250% держим плотный стоп 9%
                elif max_pnl_pct >= 0.70:
                    _trail_dist = 0.11  # На +70..+150% стоп 11%
                elif max_pnl_pct >= 0.40 or getattr(position, "is_moonbag", False):
                    _trail_dist = float(getattr(config, "TRAILING_RUNNER_DISTANCE_PCT", 0.11))
                else:
                    _trail_dist = float(getattr(config, "TRAILING_DISTANCE_PCT", 0.14))

                drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd
                if drop_from_max >= _trail_dist:
                    return f"Mature Trailing (+{max_pnl_pct*100:.0f}% peak, drop {drop_from_max*100:.0f}%)"

        # Runner Stagnation / Inactive Pool (фиксация прибыли при затухании объёмов в пуле)
        _pt = getattr(position, 'peak_time', 0.0) or et
        if hasattr(_pt, 'timestamp'):
            try:
                _pt = _pt.timestamp()
            except Exception:
                _pt = et
        if not _pt or float(_pt) <= 0:
            _pt = et
        try:
            _peak_age_min = (now_ts - float(_pt)) / 60
        except Exception:
            _peak_age_min = 0

        _dd_peak = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd else 0
        if max_pnl_pct >= 0.35 and _peak_age_min >= 15 and pnl_pct > 0.05 and (_dd_peak >= 0.06 or _peak_age_min >= 20):
            return f"Mature Runner Stagnation (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        if minutes_held >= 30 and _peak_age_min >= 20:
            if pnl_pct > 0.05:
                return f"Mature Inactive Pool TP (+{pnl_pct*100:.1f}%, {minutes_held:.0f}m)"
            elif pnl_pct < -0.15:
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
        # Мягкий стоп и Breakeven (ТОЛЬКО после 6 минут в рынке, чтобы не резать 1-2 минутные свечи ретеста на старте как Chubby!):
        if minutes_held >= 6.0 and _maxp >= 0.20 and pnl_pct <= -0.10:
            return f"Mature Soft Guard (был пик +{_maxp*100:.0f}%, слив до {pnl_pct*100:.1f}%)"
        _be = float(getattr(config, 'BREAKEVEN_PCT', 0.28))
        if minutes_held >= 6.0 and _maxp >= _be and pnl_pct <= 0.035:
            return f"Mature Breakeven (был пик +{_maxp*100:.0f}%, фикс {pnl_pct*100:+.1f}%)"

        # Hard stop loss (после 1.5 мин, чтобы не выбивать ракету на стартовом спреде в 0м)
        if minutes_held >= 4.0 and hasattr(config, 'STOP_LOSS_PCT') and pnl_pct <= config.STOP_LOSS_PCT:
            return f"Mature Stop Loss ({config.STOP_LOSS_PCT*100:.0f}%)"

        # Time exit - Stagnant: даём ракетам 25-35 минут на раскачку (кейс CATTLEAXE +23% на 37м, NIO +67% на 20м),
        # и режем только реальную просадку <= -10% без импульса (_maxp < +5%), не убивая консолидацию на 15-й минуте прямо перед пампом
        _loss_min = getattr(config, 'STAGNANT_LOSS_MIN', 25)
        _hold_min = getattr(config, 'STAGNANT_HOLD_MIN', 35)
        if minutes_held >= max(_loss_min, 25) and pnl_pct <= -0.10 and _maxp < 0.05:
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
