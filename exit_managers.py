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
            # Ранний срез вывода ликвидности девом (>35% просадки пула) — спасаем деньги ДО полного слива:
            elif _peak_l >= 3000.0 and _cur_l < (_peak_l * 0.65):
                return f"Early Liquidity Drop Escape (Liq dropped to ${_cur_l:.0f} from ${_peak_l:.0f})"
            else:
                position._liq_drop_streak_em = 0

            # Ранний сброс при агрессивном даме продавцов на первых минутах (<2м):
            if pnl_pct <= -0.070 and minutes_held < 2.0:
                txns_m5 = (_pd_chk.get("txns") or {}).get("m5") or {}
                b5_chk = int(txns_m5.get("buys", 0) or 0)
                s5_chk = int(txns_m5.get("sells", 0) or 0)
                if s5_chk >= 10 and (b5_chk / max(1, s5_chk)) < 0.40:
                    return f"Early Seller Dump Cut ({pnl_pct*100:.1f}%, {b5_chk}b/{s5_chk}s)"

            # СБРОС МОМЕНТАЛЬНОГО РАГА НА ПЕРВОЙ СВЕЧЕ (First Candle Rug Exit)
            # На ракетах (SWEATER, MUSTACHE) первая свеча зелёная, покупатели льют объём и pnl в плюсе.
            # Если токен — раг: на первой свече (0.5..3м) дев сливает стек, m1 падает > 18% или продавцы давят в одну калитку.
            # Сбрасываем ТОЛЬКО РАГ сразу на первой свече с минимальным убытком (-6%..-9%), не дожидаясь -25%..-35%!
            if 0.4 <= minutes_held <= 3.0 and pnl_pct <= -0.060:
                _pc_chk = _pd_chk.get("priceChange") or {}
                _m1_c = float(_pc_chk.get("m1", 0) or 0)
                _txns = (_pd_chk.get("txns") or {}).get("m5") or {}
                _b5 = int(_txns.get("buys", 0) or 0)
                _s5 = int(_txns.get("sells", 0) or 0)
                if _m1_c <= -18.0:
                    return f"🚨 First Candle Rug Dump: 1m crash ({_m1_c:.0f}%, PnL {pnl_pct*100:.1f}%)"
                if _s5 >= 8 and (_b5 / max(1, _s5)) < 0.35:
                    return f"🚨 First Candle Rug Cut: Sellers dominating ({_b5}b/{_s5}s, PnL {pnl_pct*100:.1f}%)"

        # =========================================================================
        # 1. ЖЕСТКИЙ КОНТРОЛЬ УБЫТКА (EMERGENCY CAP & STOP LOSS) — ПЕРВЫЙ ПРИОРИТЕТ!
        # =========================================================================
        # СБРОС ДЕВ-ДАМПА НА ПЕРВОЙ СВЕЧЕ: только при реальном обвале <= -15.0%
        # (не выбиваем ракеты на обычном Bid-Ask спреде -5%..-10%)!
        if minutes_held <= 2.5 and pnl_pct <= -0.150:
            return f"🚨 First Candle Rug Cut ({pnl_pct*100:.1f}% в первые {minutes_held*60:.0f}с)"

        _emerg_cap = -abs(float(getattr(config, "EMERGENCY_CAP_PCT", -0.180)))
        if pnl_pct <= _emerg_cap:
            return f"Mature Emergency Cap ({pnl_pct*100:.1f}%)"

        _base_sl = -abs(float(getattr(config, 'STOP_LOSS_PCT', -0.130)))
        _has_organic_bid = False
        _feats = getattr(position, "ml_features", None) or {}
        if isinstance(_feats, dict) and _feats:
            _f_liq = float(_feats.get("liquidity", 0) or 0)
            _f_bs = float(_feats.get("buy_sell_ratio", 0) or 0)
            if _f_liq >= 15000.0 and _f_bs >= 1.15:
                _has_organic_bid = True
        _sl = min(_base_sl, -0.150) if _has_organic_bid else _base_sl
        if pnl_pct <= _sl:
            _sl_c = int(getattr(position, "sl_confirm_n", 0) or 0) + 1
            position.sl_confirm_n = _sl_c
            # Требуем 2 тика подтверждения (или глубокий провал на 4% ниже стопа)
            if _sl_c >= 2 or pnl_pct <= (_sl - 0.040):
                return f"Mature Stop Loss ({pnl_pct*100:.1f}%)"
        else:
            position.sl_confirm_n = 0

        # =========================================================================
        # 2. РАКЕТНЫЙ ТРЕЙЛИНГ-СТОП И PROFIT FLOOR (СТРАТЕГИЯ 10x-20x РАКЕТ)
        # =========================================================================
        # ПРАВИЛО: ДО +110% ПИКА — ПОЗИЦИЯ ДЫШИТ СВОБОДНО!
        # Трейлинг и полы включаются СТРОГО от +110% (TP1) и выше.
        # Downside защищён только стоп-лоссом (-12%) — макс. потеря -$0.60 на $5.
        # Это даёт ракетам (CULT +4294%, Mão +10287%) не быть выбитыми на 20-30% дипах!
        _floor_pnl = None
        _trail_dist = None

        if _is_mb:
            # === МУНБЭГ-ТРЕЙЛИНГ (ТОЛЬКО ДЛЯ ПОЗИЦИЙ, ВЗЯВШИХ +110%) ===
            _mb_act = float(getattr(config, "MOONBAG_TRAIL_ACTIVATION_PCT", 1.10))
            _mb_dist = float(getattr(config, "MOONBAG_TRAIL_DISTANCE_PCT", 0.25))
            if max_pnl_pct >= 5.00:                      # +500%+ (6x): жёсткий трейлинг
                _floor_pnl = round(max_pnl_pct * 0.70, 2)
                _trail_dist = min(_mb_dist, 0.20)
            elif max_pnl_pct >= _mb_act:                 # от +110% до +500%: 25% трейлинг
                _floor_pnl = round(_mb_act * 0.60, 2)   # пол +66% от TP1
                _trail_dist = _mb_dist
            # Ниже +110% пика мунбэг тоже дышит свободно — стоп-лосс его держит
        else:
            # === ОБЫЧНАЯ ПОЗИЦИЯ (ДО МУНБЭГА): МНОГОУРОВНЕВАЯ ЗАЩИТА ПРИБЫЛИ + РАКЕТНЫЙ ТРЕЙЛИНГ ===
            # До +40% позиция дышит свободно (чтобы не выбивать ракеты на стандартном откате -20%),
            # а начиная с +40%..+110% включаем ступенчатую страховку прибыли!
            if max_pnl_pct >= 5.00:         # +500%+ (6x - 11x+ гипер-ракета)
                _floor_pnl = round(max_pnl_pct * 0.70, 2)
                _trail_dist = 0.18
            elif max_pnl_pct >= 2.50:       # +250%..+500% (3.5x - 6x ракета)
                _floor_pnl = 1.60
                _trail_dist = 0.20
            elif max_pnl_pct >= 1.10:       # +110%..+250% (первый взлет)
                _floor_pnl = 0.66           # пол +66% (TP1 уже в кармане)
                _trail_dist = 0.25
            elif max_pnl_pct >= 0.70:       # +70%..+109% (сильный импульс перед 2x)
                _floor_pnl = round(max_pnl_pct * 0.62, 2)  # фиксируем 62% от пика (минимум +43%..+67%)
                _trail_dist = 0.18
            elif max_pnl_pct >= 0.40:       # +40%..+69% (уверенный разгон — кейсы YOUNGBULL +40.4%, ANGEL +46.7%)
                _floor_pnl = round(max_pnl_pct * 0.60, 2)  # фиксируем 60% от пика (+24% при пике +40%, +28% при пике +47%, не отдаём профит до +3.9%!)
                _trail_dist = 0.16
            elif max_pnl_pct >= 0.25:       # +25%..+39% (средний импульс)
                _floor_pnl = round(max_pnl_pct * 0.50, 2)  # фиксируем половину импульса (+12.5%..+19.5%)
                _trail_dist = 0.16

        if _floor_pnl is not None and minutes_held >= 0.20:
            drop_from_max = (position.max_price_usd - current_price) / position.max_price_usd if position.max_price_usd > 0 else 0.0
            _tag = "Moonbag" if _is_mb else "Full Runner"
            if _trail_dist is not None and drop_from_max >= _trail_dist and pnl_pct >= _floor_pnl:
                return f"Mature {_tag} Trailing ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"
            elif pnl_pct <= _floor_pnl:
                return f"Mature {_tag} Floor (+{pnl_pct*100:.1f}% locked after peak +{max_pnl_pct*100:.1f}%)"

        # =========================================================================
        # 3. PARABOLIC MOMENTUM HARVEST (ФИКСАЦИЯ ПРИ ЗАСТРЕВАНИИ НА ВЕРШИНЕ)
        # =========================================================================
        # Застревание на хаях без новых ATH больше 8-15 минут — предвестник дев-дампа:
        # Мунбэг ниже +200% не режем «харвестом» — его страхуют полы выше.
        _mb_free_ride = _is_mb and max_pnl_pct < float(getattr(config, "MOONBAG_TRAIL_ACTIVATION_PCT", 2.00))
        if max_pnl_pct >= 2.00 and _peak_age_min >= 8.0:
            return f"Parabolic 3x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"
        if not _mb_free_ride and max_pnl_pct >= 1.00 and _peak_age_min >= 10.0:
            return f"Parabolic 2x Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

        # === СБОР ПРИБЫЛИ И ЗАЩИТА ОТ ЗАСТРЕВАНИЯ ===
        # Даём ракетам свободно дышать до +110% (где включается Full Runner Trailing).
        # Не срезаем растущие монеты на микро-профитах (+6%..+15%)!
        if not _mb_free_ride:
            # 1. Dead Churn Escape: ТОЛЬКО для дохлых монет, которые вообще не дали импульса (пик < +8%)
            # и стоят на месте в нуле/минусе 3+ минут (кейс XBT -1.4%) — режем стоячий мусор, не трогая ракеты!
            if minutes_held >= 3.5 and _peak_age_min >= 2.5 and max_pnl_pct < 0.08 and pnl_pct <= 0.02:
                return f"Dead Churn Escape ({pnl_pct*100:+.1f}%, застрял без импульса {_peak_age_min:.1f}м — выход)"

            # 2. Mid-Peak Profit Shield: если монета давала хороший импульс (+22%..+39%, как ZAPZEKE +22.9%),
            # но 2+ минуты не обновляет хай и начала сползать к точке входа (<= +5%) — забираем плюс, не ждём 0% или минуса!
            if max_pnl_pct >= 0.22 and _peak_age_min >= 2.0 and pnl_pct <= 0.05:
                return f"Mid-Peak Profit Shield ({pnl_pct*100:+.1f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.1f}m no ATH)"

            # 2b. Faded Impulse Stop: импульс был (+8%..+39%), но затух (2.5+ мин без нового ATH) и сполз в минус (-4%)
            # Спасает от 10-минутного медленного сползания в -28% (кейс SPEED -$1.45)!
            if max_pnl_pct >= 0.08 and _peak_age_min >= 2.5 and pnl_pct <= -0.04:
                return f"Faded Impulse Stop ({pnl_pct*100:+.1f}%, затухание импульса после пика +{max_pnl_pct*100:.0f}%)"

            # 3. ATH Exhaustion Exit: пик был сильным (+40%+), но 5+ минут нет нового ATH — забираем +15%+ профита
            if max_pnl_pct >= 0.40 and _peak_age_min >= 5.0 and pnl_pct >= 0.15:
                return f"ATH Exhaustion Exit (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"

            # 4. Rocket Momentum Harvest: пик +50%+, 8+ минут без ATH — забираем +20%+ профита
            if max_pnl_pct >= 0.50 and _peak_age_min >= 8.0 and pnl_pct >= 0.20:
                return f"Rocket Momentum Harvest (+{pnl_pct*100:.0f}% locked after peak +{max_pnl_pct*100:.0f}%, {_peak_age_min:.0f}m no ATH)"


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

            # === СЛУЧАЙ 3: УМЕРЕННЫЙ КРАШ (-15%) + ДОМИНИРОВАНИЕ ПРОДАВЦОВ + ВСПЛЕСК ===
            # Классика: кит начал аккуратно сливать, после него придёт дев.
            if _m1_crash <= -15.0 and pnl_pct >= 0.15 and bs_ratio < 0.30 and _vol_spike:
                return f"🚨 Crash Guard: Coordinated Sell ({_m1_crash:.0f}% 1m, {b5}b/{s5}s, vol spike, {pnl_pct*100:+.1f}%)"

        # =========================================================================
        # 5. FRONT-RUN PROFIT EXIT (Разворот стакана только при уверенном профите >= +35%)
        # =========================================================================
        if isinstance(_pd, dict) and _pd:
            txns_m5 = (_pd.get("txns") or {}).get("m5") or {}
            b5 = int(txns_m5.get("buys", 0) or 0)
            s5 = int(txns_m5.get("sells", 0) or 0)
            pc = _pd.get("priceChange") or {}
            m1 = float(pc.get("m1", 0) or 0)
            bs_ratio = (b5 / s5) if s5 > 0 else 2.0
            total_tx5 = b5 + s5
            if minutes_held >= 3.0 and pnl_pct >= 0.35 and total_tx5 >= 25 and bs_ratio < 0.40 and m1 <= -5.0:
                return f"Front-Run Profit Exit: Sellers dominating ({b5}b/{s5}s) while in profit (+{pnl_pct*100:.1f}%)"
            # Тихое угасание: объём упал, продажи доминируют, цена вниз — дев готовится к дрейну
            vol_m5_now = float((_pd.get("volume") or {}).get("m5", 0) or 0)
            vol_h1_now = float((_pd.get("volume") or {}).get("h1", 0) or 0)
            _vol_dying = vol_h1_now > 0 and vol_m5_now < vol_h1_now * 0.08  # объём за 5м < 8% часового
            if minutes_held >= 2.0 and pnl_pct >= 0.25 and bs_ratio < 0.50 and _vol_dying and m1 <= -3.0:
                return f"Volume Exhaustion Exit: торги утихли ({b5}b/{s5}s, vol 5m ${vol_m5_now:.0f} vs h1 ${vol_h1_now:.0f}) в профите +{pnl_pct*100:.1f}%"

        # =========================================================================
        # 5. MAX HOLD TIMEOUT (УДЕРЖАНИЕ ДО 28 МИНУТ — ВЫХОД ДО РАГА НА 40-Й МИНУТЕ)
        # =========================================================================
        # Ракеты разгоняются в первые 3-25 минут. Монеты, которые рагаются, обычно выдергивают ликву на 35-45 минуте.
        # Выходим по таймауту на 28 минуте (или на 20-22 минуте при застревании без ATH),
        # забирая прибыль ДО того как дев выдернет ликвидность!
        _mb_max_hold = float(getattr(config, "MOONBAG_MAX_HOLD_MIN", 90.0)) if _is_mb else 28.0
        if not _is_mb and minutes_held >= 20.0 and pnl_pct >= 0.05 and _peak_age_min >= 8.0:
            return f"Max Hold Profit Take (+{pnl_pct*100:.1f}% locked at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if _is_mb and minutes_held >= 60.0 and _peak_age_min >= 25.0:
            return f"Moonbag Max Hold Take ({pnl_pct*100:+.1f}% at {minutes_held:.0f}m, {_peak_age_min:.0f}m no ATH)"
        if minutes_held >= _mb_max_hold:
            return f"Max Hold Expiry ({pnl_pct*100:+.1f}% after {minutes_held:.0f}m)"

        # =========================================================================
        # 6. СТАГНАЦИЯ И МЕРТВЫЙ ИМПУЛЬС (Срез стоячих монет до дев-дампа)
        # =========================================================================
        # Если монета в рынке 6+ минут, её пик был слабым (< +15%), и новых ATH нет 4+ минут:
        # выходим! В мемкоинах полное затухание импульса означает скорый раг дева.
        if not _is_mb and minutes_held >= 6.0 and max_pnl_pct < 0.15 and _peak_age_min >= 4.0:
            _sign = "+" if pnl_pct >= 0 else ""
            return f"Stalled Momentum Escape ({_sign}{pnl_pct*100:.1f}%, no ATH {_peak_age_min:.1f}m at {minutes_held:.1f}m)"

        _stall_sec = float(getattr(config, "STALL_TIMER_SEC", 240.0))
        _stall_loss = float(getattr(config, "STALL_MAX_LOSS_PCT", -0.090))
        if minutes_held >= (_stall_sec / 60.0) and max_pnl_pct < 0.04 and pnl_pct <= _stall_loss:
            return f"Dead Momentum Stall ({minutes_held*60:.0f}s no breakout, pnl {pnl_pct*100:.1f}%)"

        if minutes_held >= 20.0 and pnl_pct <= -0.06 and max_pnl_pct < 0.04:
            return f"Stagnant Loss Cut ({minutes_held:.0f}m, {pnl_pct*100:.1f}%)"
        if minutes_held >= 25.0 and pnl_pct < 0.015 and max_pnl_pct < 0.05:
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
