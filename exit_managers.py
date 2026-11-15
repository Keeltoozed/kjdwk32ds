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
            # Ранний срез вывода ликвидности девом (>45% просадки пула 2 тика подряд) — не режем от единичного спайка DS:
            elif _peak_l >= 3000.0 and _cur_l < (_peak_l * 0.55):
                _drop_s = int(getattr(position, "_liq_drop_streak_em", 0) or 0) + 1
                position._liq_drop_streak_em = _drop_s
                if _drop_s >= 2:
                    return f"Early Liquidity Drop Escape (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
            else:
                position._liq_drop_streak_em = 0

            # Ранний сброс при агрессивном даме продавцов на первых минутах (<2м):
            if pnl_pct <= -0.095 and minutes_held < 2.0:
                txns_m5 = (_pd_chk.get("txns") or {}).get("m5") or {}
                b5_chk = int(txns_m5.get("buys", 0) or 0)
                s5_chk = int(txns_m5.get("sells", 0) or 0)
                if s5_chk >= 12 and (b5_chk / max(1, s5_chk)) < 0.35:
                    return f"Early Seller Dump Cut ({pnl_pct*100:.1f}%, {b5_chk}b/{s5_chk}s)"

            # СБРОС МОМЕНТАЛЬНОГО РАГА НА ПЕРВОЙ СВЕЧЕ (First Candle Rug Exit)
            # На ракетах первая свеча зелёная, покупатели льют объём и pnl в плюсе.
            # Если токен — раг: дев сливает стек, m1 падает > 12% или продавцы давят в одну калитку.
            # Сбрасываем ТОЛЬКО РАГ сразу на первой свече с минимальным убытком (-6%..-7.5%), не дожидаясь -25%..-90%!
            if 0.3 <= minutes_held <= 3.0 and pnl_pct <= -0.070:
                _pc_chk = _pd_chk.get("priceChange") or {}
                _m1_c = float(_pc_chk.get("m1", 0) or 0)
                _txns = (_pd_chk.get("txns") or {}).get("m5") or {}
                _b5 = int(_txns.get("buys", 0) or 0)
                _s5 = int(_txns.get("sells", 0) or 0)
                if _m1_c <= -12.0:
                    return f"🚨 First Candle Rug Dump: 1m crash ({_m1_c:.0f}%, PnL {pnl_pct*100:.1f}%)"
                if _s5 >= 6 and (_b5 / max(1, _s5)) < 0.40:
                    return f"🚨 First Candle Rug Cut: Sellers dominating ({_b5}b/{_s5}s, PnL {pnl_pct*100:.1f}%)"

        # =========================================================================
        # 0.5. ЖЕСТКИЙ СТОП-ЛОСС И ЗАЩИТА ОТ ОБВАЛА НА 1-М ТИКЕ (LOSS CONTROL FIRST)
        # =========================================================================
        # Любой резкий дамп режется В ПЕРВУЮ ОЧЕРЕДЬ, до логики фиксаций и скретчей!
        _fast_sl = -abs(float(getattr(config, "STOP_LOSS_PCT", -0.075)))
        if pnl_pct <= _fast_sl:
            return f"🚨 Fast Stop Loss ({pnl_pct*100:.1f}%)"

        if minutes_held <= 2.5 and pnl_pct <= -0.070:
            return f"🚨 First Candle Rug Cut ({pnl_pct*100:.1f}% в первые {minutes_held*60:.0f}с)"

        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.090)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"

        # =========================================================================
        # 0.6. БЫСТРЫЙ СБОР ПРИБЫЛИ ПРИ ЗАТУХАНИИ ИМПУЛЬСА (FAST PROFIT HARVEST)
        # =========================================================================
        # На мемкоинах Solana окно максимальной прибыли длится 15-45 секунд.
        # Если монета сделала скачок и замерла — фиксируем прибыль СРАЗУ,
        # даже если монета потом рагнится! Быстро вытаскиваем профит до сброса стакана!
        _pd_pos = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        _mint_pos = str(getattr(position, "mint", "") or "")
        _base_a = str(((_pd_pos.get("baseToken") or {}).get("address")) or _mint_pos)
        _is_pump_token = _mint_pos.endswith("pump") or _base_a.endswith("pump")
        _dex_pos_low = str((_pd_pos.get("dexId", "") if isinstance(_pd_pos, dict) else "") or "").lower()

        _has_safe_lock = bool(getattr(position, "is_lp_locked_80", False)) or (
            _is_pump_token and _dex_pos_low in ("pumpswap", "pump-amm", "pump", "pumpfun")
        )
        _is_unlocked = not _has_safe_lock
        position.is_unlocked_pool = _is_unlocked

        # А. Фиксация прибыли при затухании импульса (для ЛЮБЫХ монет):
        if not _is_mb:
            if max_pnl_pct >= 0.50:
                # Памп +50%..+100%: если нет нового ATH 12 сек или откат >= 7% от пика — забираем прибыль!
                if (_peak_age_min >= 0.20 and pnl_pct <= max_pnl_pct * 0.93) or pnl_pct <= max_pnl_pct * 0.86:
                    return f"⚡ Runner Stall Harvest (+{pnl_pct*100:.1f}%, застой {_peak_age_min*60:.0f}с после пика +{max_pnl_pct*100:.1f}%)"
            elif max_pnl_pct >= 0.25:
                # Памп +25%..+50%: если нет нового ATH 12 сек или откат >= 6% от пика — забираем прибыль!
                if (_peak_age_min >= 0.20 and pnl_pct <= max_pnl_pct * 0.92) or pnl_pct <= max_pnl_pct * 0.84:
                    return f"⚡ Momentum Stall Harvest (+{pnl_pct*100:.1f}%, застой {_peak_age_min*60:.0f}с после пика +{max_pnl_pct*100:.1f}%)"
            elif max_pnl_pct >= 0.15:
                # Памп +15%..+25%: если нет нового ATH 14 сек или откат до +8% — забираем прибыль!
                if (_peak_age_min >= 0.23 and pnl_pct >= 0.08) or pnl_pct <= 0.08:
                    return f"⚡ Fast Profit Harvest (+{pnl_pct*100:.1f}%, застой {_peak_age_min*60:.0f}с после пика +{max_pnl_pct*100:.1f}%)"
            elif max_pnl_pct >= 0.08:
                # Малый отскок +8%..+15%: если застой 18 сек и цена сползает к +3.5% — фиксируем!
                if _peak_age_min >= 0.30 and pnl_pct <= 0.035:
                    return f"⚡ Quick Pop Lock (+{pnl_pct*100:.1f}%, откат после пика +{max_pnl_pct*100:.1f}%)"

        # Б. УНИВЕРСАЛЬНЫЙ СРЕЗ ЗАВИСШИХ МОНЕТ БЕЗ ИМПУЛЬСА (Zero-Momentum Scratch):
        # Строго в диапазоне около нуля: -4.5% <= pnl_pct <= +0.5%!
        # ДАМПЫ (pnl < -4.5%) УЖЕ перехвачены Stop Loss / Rug Cut выше!
        if minutes_held >= 0.58 and max_pnl_pct < 0.04 and -0.045 <= pnl_pct <= 0.005:
            return f"⚡ Zero-Momentum Scratch ({pnl_pct*100:+.1f}%, 0 импульса за {minutes_held*60:.0f}с — сброс до дампа дева)"

        _sl = -abs(float(getattr(config, "STOP_LOSS_PCT", -0.095)))
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            # На проливе <= -10.8% выходим за 1 тик, в зоне -9.5%..-10.7% ждём 2 тика (защита от 1-тикового сквиза)
            if _sl_c >= 2 or pnl_pct <= (_sl * 1.13):
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 2. BREAKEVEN SHIELD И ПРОГРЕССИВНЫЕ ЭТАЖИ ДЛЯ 2x-10x РАКЕТ (0d2b1ae + RUNNER CURVE)
        # =========================================================================
        _floor_pnl = None
        _trail_dist = None

        if _is_mb:
            # === МУНБЭГ-ТРЕЙЛИНГ ===
            _mb_act = float(getattr(config, "MOONBAG_TRAIL_ACTIVATION_PCT", 1.00))
            _mb_dist = float(getattr(config, "MOONBAG_TRAIL_DISTANCE_PCT", 0.22))
            if max_pnl_pct >= 5.00:
                _floor_pnl = round(max_pnl_pct * 0.78, 2)
                _trail_dist = min(_mb_dist, 0.22)
            elif max_pnl_pct >= 2.50:
                _floor_pnl = round(max(1.65, max_pnl_pct * 0.74), 2)
                _trail_dist = min(_mb_dist, 0.20)
            elif max_pnl_pct >= _mb_act:
                _floor_pnl = round(max(0.72, max_pnl_pct * 0.72), 2)
                _trail_dist = _mb_dist
            elif max_pnl_pct >= 0.55:
                _floor_pnl = 0.24
                _trail_dist = 0.22
        else:
            # === ОСНОВНАЯ ПОЗИЦИЯ: ЖЁСТКИЙ Б/У ЩИТ + СТУПЕНЧАТЫЙ ТРЕЙЛИНГ ДО 10x+ ===
            if max_pnl_pct >= 5.00:         # 6x+: фиксируем минимум 78% от ATH PnL
                _floor_pnl = round(max_pnl_pct * 0.78, 2)
                _trail_dist = 0.22
            elif max_pnl_pct >= 2.50:       # 3.5x - 6x: фиксируем минимум 74% от ATH (от +165%)
                _floor_pnl = round(max(1.65, max_pnl_pct * 0.74), 2)
                _trail_dist = 0.20
            elif max_pnl_pct >= 1.10:       # 2.1x - 3.5x: фиксируем от +72%, даём 20% места для волны 3x-5x
                _floor_pnl = round(max(0.72, max_pnl_pct * 0.72), 2)
                _trail_dist = 0.20
            elif max_pnl_pct >= 0.55:       # +55%..+110%: фиксируем минимум +34%, трейл 18% от цены ATH
                _floor_pnl = 0.34
                _trail_dist = 0.18
            elif max_pnl_pct >= 0.28:       # +28%..+55%: Wave-1 Shield (+14% пол после 45с, трейл 16%)
                if minutes_held >= 0.75:
                    _floor_pnl = 0.14
                    _trail_dist = 0.16
                else:
                    _floor_pnl = 0.06
                    _trail_dist = None
            elif max_pnl_pct >= float(getattr(config, "BREAKEVEN_PCT", 0.16) or 0.16):
                # Breakeven Shield: позиция с пиком >= +16% НЕ МОЖЕТ закрыться в минус!
                # Первые 45с / 18с после ATH держим пол +1.5% от шума сквиза (кейс 67GOOGLE), затем +4.5% (~+2.5..3% чистыми)
                if minutes_held >= 0.75 and _peak_age_min >= 0.30:
                    _floor_pnl = 0.045
                else:
                    _floor_pnl = 0.015
                _trail_dist = None

        if _floor_pnl is not None and minutes_held >= 0.20:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            _tag = "Moonbag" if _is_mb else "Full Runner"
            if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                return f"Mature {_tag} Trailing ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif pnl_pct <= _floor_pnl:
                return f"Mature {_tag} Floor (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 3. PARABOLIC MOMENTUM HARVEST (ФИКСАЦИЯ ПРИ ДОЛГОМ ЗАТУХАНИИ НА ХАЯХ)
        # =========================================================================
        _mb_free_ride = _is_mb and max_pnl_pct < float(getattr(config, "MOONBAG_TRAIL_ACTIVATION_PCT", 1.00))
        if max_pnl_pct >= 2.00 and _peak_age_min >= 8.0:
            return f"Parabolic 3x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if not _mb_free_ride and max_pnl_pct >= 1.00 and _peak_age_min >= 10.0:
            return f"Parabolic 2x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        # === ЗАЩИТА ОТ ОТКАТА НЕБОЛЬШИХ ПОПОВ (+10%..+15.9%) БЕЗ МИКРО-ПИЛА НА 2.5м ===
        if not _mb_free_ride:
            if 0.10 <= max_pnl_pct < 0.16 and minutes_held >= 4.0 and _peak_age_min >= 2.0 and pnl_pct <= -0.030:
                return f"🛡️ Small Pop Cut ({pnl_pct*100:+.1f}%, откат после поп-пика +{max_pnl_pct*100:.1f}%)"
            if max_pnl_pct >= 0.35 and _peak_age_min >= 3.5 and pnl_pct <= 0.14:
                return f"Mid-Peak Profit Shield ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.1f}m no ATH)"


        # =========================================================================
        # 4. CRASH VELOCITY GUARD (Дев-дамп / аномальное обрушение за 1 минуту)
        # =========================================================================
        # Если 1-минутная свеча упала > 25% — это не обычная коррекция, это дамп!
        # Нормальные Solana-откаты: -5%..-15% за минуту. -25%+ = дев выходит / кит сливает.
        # Выходим немедленно если мы в профите — не ждём трейлинга.
        # Если мы в убытке — уже есть стоп-лосс, он поймает.
        _pd = getattr(position, "pair_data", None) or getattr(position, "last_pair_data", None) or {}
        _m1_crash = None
        _vol_spike = False
        if isinstance(_pd, dict) and _pd:
            pc = _pd.get("priceChange") or {}
            _m1_crash = float(pc.get("m1", 0) or 0)
            txns_m5 = (_pd.get("txns") or {}).get("m5") or {}
            b5 = int(txns_m5.get("buys", 0) or 0)
            s5 = int(txns_m5.get("sells", 0) or 0)
            vol_m5 = float((_pd.get("volume") or {}).get("m5", 0) or 0)
            vol_h1 = float((_pd.get("volume") or {}).get("h1", 0) or 0)
            # Всплеск объёма продаж: объём за 5 минут > 40% часового → аномалия
            _vol_spike = (vol_m5 > vol_h1 * 0.40) if vol_h1 > 0 else False
            bs_ratio = (b5 / s5) if s5 > 0 else 2.0

            # === СЛУЧАЙ 1: ПОЛНЫЙ ДЕВ-ДАМП (-50%+ за 1 минуту) ===
            # Это однозначно не откат — уходим при любом PnL кроме глубокого стопа
            if _m1_crash <= -50.0 and minutes_held >= 0.5:
                return f"🚨 Crash Guard: Dev Dump ({_m1_crash:.0f}% за 1m, PnL {pnl_pct*100:+.1f}%)"

            # === СЛУЧАЙ 2: СИЛЬНЫЙ КРАШ (-25%..-50%) + МЫ В ПРОФИТЕ ===
            # Мог быть кит, мог быть дев. Если мы в плюсе — выходим, не рискуем.
            if _m1_crash <= -25.0 and pnl_pct >= 0.05 and minutes_held >= 0.5:
                return f"🚨 Crash Guard: Price Crash ({_m1_crash:.0f}% за 1m, locked {pnl_pct*100:+.1f}%)"

            # === СЛУЧАЙ 3: УМЕРЕННЫЙ КРАШ (-18%) + ДОМИНИРОВАНИЕ ПРОДАВЦОВ + ВСПЛЕСК ===
            if _m1_crash <= -18.0 and pnl_pct >= 0.20 and bs_ratio < 0.30 and _vol_spike:
                return f"🚨 Crash Guard: Coordinated Sell ({_m1_crash:.0f}% 1m, {b5}b/{s5}s, vol spike, {pnl_pct*100:+.1f}%)"

        # =========================================================================
        # 5. FRONT-RUN PROFIT EXIT (Разворот стакана только при +65%+ — не режем волны на пути к 2x)
        # =========================================================================
        if isinstance(_pd, dict) and _pd:
            txns_m5 = (_pd.get("txns") or {}).get("m5") or {}
            b5 = int(txns_m5.get("buys", 0) or 0)
            s5 = int(txns_m5.get("sells", 0) or 0)
            pc = _pd.get("priceChange") or {}
            m1 = float(pc.get("m1", 0) or 0)
            bs_ratio = (b5 / s5) if s5 > 0 else 2.0
            total_tx5 = b5 + s5
            if minutes_held >= 4.0 and pnl_pct >= 0.65 and total_tx5 >= 30 and bs_ratio < 0.35 and m1 <= -8.0:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"
            vol_m5_now = float((_pd.get("volume") or {}).get("m5", 0) or 0)
            vol_h1_now = float((_pd.get("volume") or {}).get("h1", 0) or 0)
            _vol_dying = vol_h1_now > 0 and vol_m5_now < vol_h1_now * 0.05
            if minutes_held >= 5.0 and pnl_pct >= 0.60 and bs_ratio < 0.40 and _vol_dying and m1 <= -5.0:
                return f"Volume Exhaustion Exit: торги утихли ({b5}b/{s5}s, vol 5m ${vol_m5_now:.0f} vs h1 ${vol_h1_now:.0f}) в профите +{pnl_pct*100:.1f}%"

        # =========================================================================
        # 5. MAX HOLD TIMEOUT (ДАЁМ РАКЕТАМ ДО 38-55 МИНУТ НА РАЗГОН 2x-10x)
        # =========================================================================
        _mb_max_hold = float(getattr(config, "MOONBAG_MAX_HOLD_MIN", 55.0)) if _is_mb else 38.0
        if not _is_mb and minutes_held >= 20.0 and 0.03 <= pnl_pct < 0.25 and _peak_age_min >= 8.0:
            return f"Max Hold Profit Take (+{pnl_pct*100:.1f}% locked at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if _is_mb and minutes_held >= 60.0 and _peak_age_min >= 25.0:
            return f"Moonbag Max Hold Take ({pnl_pct*100:+.1f}% at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if minutes_held >= _mb_max_hold:
            return f"Max Hold Expiry ({pnl_pct*100:+.1f}% after {minutes_held:.0f}m)"

        # =========================================================================
        # 6. СТАГНАЦИЯ И МЕРТВЫЙ ИМПУЛЬС (Срез только реально мёртвых позиций без отрыва в рост)
        # =========================================================================
        if not _is_mb and minutes_held >= 8.0 and max_pnl_pct < 0.10 and _peak_age_min >= 5.0 and pnl_pct <= 0.02:
            _sign = "+" if pnl_pct >= 0 else ""
            return f"Stalled Momentum Escape ({_sign}{pnl_pct*100:.1f}%, no ATH {_peak_age_min:.1f}m at {minutes_held:.1f}m)"

        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 420.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.055))
        if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.05 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        if minutes_held >= 18.0 and pnl_pct <= -0.04 and max_pnl_pct < 0.06:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"
        if minutes_held >= 24.0 and pnl_pct < 0.02 and max_pnl_pct < 0.08:
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
