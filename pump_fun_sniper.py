import asyncio
import json
import time
import websockets
import collections
import config
from sol_price import get_sol_price_sync

class TokenTrackerState:
    """Хранит состояние токена в оперативной памяти (RAM) для ультра-быстрой потоковой фильтрации."""
    def __init__(self, mint: str, symbol: str, trader_pubkey: str, initial_buy: float = 0.0):
        self.mint = mint
        self.symbol = symbol or "UNKNOWN"
        self.trader_pubkey = trader_pubkey  # dev/creator wallet
        self.initial_buy = initial_buy
        self.start_time = time.time()
        self.last_trade_time = self.start_time
        self.last_v_sol = 30.0  # начальный резерв виртуального SOL на pump.fun (~30 SOL)
        
        # Скользящее окно трейдов в RAM (ограничено, чтобы не расходовать память)
        self.trades = collections.deque(maxlen=150)
        
        # Флаги жизненного цикла
        self.is_auditing = False
        self.is_evaluated = False
        self.audit_failed = False
        self.is_entered = False
        self.is_migrated = False
        self.is_dead = False
        
        self.max_curve_progress = 0.0
        self.entry_price_usd = 0.0
        self.stage = 0           # самый дальний этап воронки (см. STAGES)
        self.fail_reasons = set()  # причины отсева (считаем один раз на токен)


# Этапы воронки: каждый токен считается на самом дальнем этапе, до которого дошёл.
STAGES = ["0 создан", "1 есть сделки", "2 покупателей достаточно", "3 объём SOL ок",
          "4 buy/sell ок", "5 без кита ок", "6 кривая ок", "7 финалист",
          "8 аудит пройден", "9 ВХОД"]


class PumpFunSniper:
    def __init__(self, tracker=None):
        self.tracker = tracker
        self.running = False
        self.trackers = {}  # mint -> TokenTrackerState
        self.funnel = collections.Counter()       # этап -> сколько токенов закончили на нём
        self.fails = collections.Counter()        # причина отсева -> число токенов
        self.audit_fails = collections.Counter()  # причина отказа аудита -> число
        self.created_total = 0
        self.trades_total = 0
        self._last_funnel_print = time.time()
        self._bg_tasks = []

    def _mark(self, state, stage: int):
        if stage > state.stage:
            state.stage = stage

    def _fail(self, state, reason: str):
        if reason not in state.fail_reasons:
            state.fail_reasons.add(reason)
            self.fails[reason] += 1

    def print_funnel(self):
        """Раз в минуту: сколько токенов на каком этапе остановились и почему."""
        live = collections.Counter(st.stage for st in self.trackers.values())
        print(f"\n📊 [STREAM FUNNEL] создано {self.created_total}, трейдов {self.trades_total}, в памяти {len(self.trackers)}")
        for i, name in enumerate(STAGES):
            print(f"   {name}: закончили {self.funnel.get(i, 0)}, сейчас {live.get(i, 0)}")
        if self.fails:
            print("   отсев (токенов): " + ", ".join(f"{k}={v}" for k, v in self.fails.most_common(8)))
        if self.audit_fails:
            print("   аудит отклонил: " + ", ".join(f"{k}={v}" for k, v in self.audit_fails.most_common(8)))

    async def run_security_audit(self, mint: str, symbol: str) -> tuple:
        """Внешняя проверка безопасности (GoPlus + RugCheck + RPC).
        Запускает проверки параллельно (asyncio.gather), ускоряя аудит с 15с до 1-2с!"""
        # 1. Проверка на мусорные названия и мимикрию под корпорации/акции
        bad_words = ["test", "scam", "fuck", "nigger", "pump and dump", "rug"]
        s_low = (symbol or "").lower()
        if any(w in s_low for w in bad_words):
            return False, "недопустимое имя токена"

        stocks = ["AAPL", "MSFT", "TSLA", "TESLA", "SPACEX", "STARLINK", "XAI", "NEURALINK",
                  "NVDA", "GOOG", "AMZN", "META", "NFLX", "ANTHROPIC", "OPENAI", "PERPLEXITY"]
        s_up = (symbol or "").upper()
        if any(k in s_up for k in stocks):
            return False, f"мимикрия под корпоративный бренд {symbol} — 100% скам"

        # 2. Параллельные проверки ончейн RPC + GoPlus и RugCheck
        async def _check_onchain_and_gp():
            try:
                from goplus import check_solana, is_hard_scam, check_onchain_solana_security
                _sol_ok, _sol_why = await check_onchain_solana_security(mint)
                if not _sol_ok:
                    return False, f"OnChain-Freeze: {_sol_why}"
                gp_ok, gp_reason = await check_solana(mint)
                if not gp_ok and is_hard_scam(gp_reason):
                    return False, f"GoPlus: {gp_reason}"
                return True, ""
            except Exception as e:
                return True, ""  # fail-open при сетевых сбоях

        async def _check_rugcheck():
            try:
                from http_client import fetch_json
                rc_url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
                status, data = await fetch_json(rc_url, timeout=5, retries=1)
                if status == 200 and isinstance(data, dict):
                    score = float(data.get("score", 0) or 0)
                    low_liq_penalty = sum(
                        float(rk.get("score", 0) or 0)
                        for rk in (data.get("risks") or [])
                        if "low liquidity" in str(rk.get("name", "")).lower()
                    )
                    clean_score = max(0.0, score - low_liq_penalty)
                    if clean_score >= 800:
                        return False, f"RugCheck clean score {clean_score:.0f} >= 800 (высокий риск)"

                    if data.get("freezeAuthority"):
                        return False, "RugCheck: Freeze Authority не отозвана"
                    if data.get("mintAuthority"):
                        return False, "RugCheck: Mint Authority не отозвана"

                    for r in (data.get("risks", []) or []):
                        r_lvl = str(r.get("level", "")).lower()
                        r_name = str(r.get("name", ""))
                        if "low liquidity" in r_name.lower():
                            continue  # Для токенов на бондинг-кривой низкая ликвидность — норма
                        if r_lvl in ("danger", "critical"):
                            return False, f"RugCheck: {r_name} ({r_lvl})"
                        r_name_low = r_name.lower()
                        if any(bad in r_name_low for bad in ("honeypot", "freeze", "mint authority", "transfer hook", "token-2022")):
                            return False, f"RugCheck: {r_name}"
                return True, ""
            except Exception as e:
                return True, ""

        res_gp, res_rc = await asyncio.gather(_check_onchain_and_gp(), _check_rugcheck())
        if not res_gp[0]:
            return res_gp
        if not res_rc[0]:
            return res_rc

        return True, ""

    async def audit_and_enter(self, state: TokenTrackerState, n_buyers: int, buy_sol: float):
        """Фаза 3 и 4: Внешняя проверка финалиста и немедленный вход в позицию."""
        mint = state.mint
        sym = state.symbol
        print(f"\n🎯 [STREAM FINALIST] {sym} ({mint[:8]}): {n_buyers} уникальных покупателей за {getattr(config, 'STREAM_WINDOW_SEC', 30)}с ({buy_sol:.1f} SOL)! Проверяем RugCheck + GoPlus...")

        try:
            # 1. Внешний аудит
            audit_ok, audit_reason = await self.run_security_audit(mint, sym)
            if not audit_ok:
                print(f"🚫 [STREAM AUDIT REJECT] {sym}: {audit_reason}")
                self.audit_fails[str(audit_reason)[:60]] += 1
                state.audit_failed = True
                state.is_evaluated = True
                return

            print(f"✅ [STREAM AUDIT PASSED] {sym} ({mint[:8]}): аудит пройден чисто! Готовим вход...")
            self._mark(state, 8)

            # 2. Проверка лимитов портфеля и защиты от серии минусов
            from tracker import PaperTracker
            tracker = self.tracker if self.tracker else PaperTracker()

            if not tracker.can_open_new_position(config.MAX_CONCURRENT_POSITIONS):
                print(f"⏳ [STREAM SKIP] {sym}: лимит открытых позиций ({len(tracker.get_open_positions())}/{config.MAX_CONCURRENT_POSITIONS})")
                state.is_auditing = False
                return

            cooling, cool_reason = tracker.is_streak_cooldown_active()
            if cooling:
                print(f"🛑 [STREAM COOLDOWN] {sym}: активна пауза streak cooldown ({cool_reason}).")
                state.is_auditing = False
                return

            last_loss = tracker.last_loss_pct(mint)
            if last_loss is not None and last_loss <= getattr(config, "RUG_REBUY_MAX_LOSS", -0.15):
                print(f"🚫 [STREAM REBUY BLOCK] {sym}: прошлый лосс {last_loss*100:.0f}%")
                state.is_evaluated = True
                return

            # 3. Расчет цены входа по кривой связывания (Bonding Curve)
            sol_usd = get_sol_price_sync()
            last_trade = state.trades[-1] if state.trades else {}
            mc_sol = last_trade.get("mc_sol", 0.0)
            v_sol = last_trade.get("v_sol", 0.0)

            if mc_sol > 0:
                raw_price = (mc_sol / 1_000_000_000.0) * sol_usd
            elif v_sol > 0:
                raw_price = (v_sol / 1_000_000_000.0) * sol_usd
            else:
                raw_price = 0.0

            if raw_price <= 0.0:
                print(f"⚠️ [STREAM PRICE ERR] {sym}: не удалось рассчитать входную цену.")
                state.is_evaluated = True
                return

            # Проскальзывание на раннем импульсе
            slip = float(getattr(config, "STREAM_SLIPPAGE", 0.02))
            entry_price = raw_price * (1.0 + slip)

            # 4. Размер позиции
            pos_size = float(getattr(config, "STREAM_ENTRY_SIZE_USD", 0.0) or getattr(config, "TRADE_AMOUNT_USD", 0.0) or 15.0)
            pos_size = max(5.0, min(pos_size, 30.0))

            # 5. Исполнение входа
            state.is_entered = True
            state.is_evaluated = True
            state.entry_price_usd = entry_price
            self._mark(state, 9)

            source_tag = f"STREAM:{n_buyers}b/{buy_sol:.1f}sol"
            tracker.add_position(
                sym,
                mint,
                entry_price,
                pos_size,
                chain="solana",
                source=source_tag,
                is_mature=False
            )
            print(f"🚀🚀🚀 [STREAM SNIPE] Входим в {sym} ({mint[:8]}) на ${pos_size:.1f} @ ${entry_price:.8f} ({source_tag})!")

            # 6. Запуск мониторинга слива создателя (Dev Panic Exit)
            try:
                from exit_manager import ExitManager
                exit_mgr = ExitManager(config.HELIUS_RPC_URL)

                async def panic_sell_cb(token_mint, reason):
                    pos = tracker.positions.get(token_mint)
                    if pos and pos.status == "open":
                        cur_px = pos.current_price_usd if pos.current_price_usd > 0 else pos.entry_price_usd
                        tracker.close_position(token_mint, cur_px, reason)

                dev_wallet = state.trader_pubkey if state.trader_pubkey else "11111111111111111111111111111111"
                asyncio.create_task(
                    exit_mgr.start_monitoring(
                        token_mint=mint,
                        dev_wallet=dev_wallet,
                        initial_dev_balance=1_000_000_000,
                        on_panic_sell=panic_sell_cb
                    )
                )
            except Exception:
                pass

        except Exception as e:
            print(f"⚠️ Ошибка в audit_and_enter ({sym}): {e}")
            state.is_evaluated = True
        finally:
            state.is_auditing = False

    async def evaluate_and_enter(self, state: TokenTrackerState):
        """Совместимость со старыми вызовами."""
        if not getattr(config, "SNIPER_ENTRIES_ENABLED", True):
            state.is_evaluated = True
            return
        await self.audit_and_enter(state, len(state.trades), state.trades[-1].get("sol", 0.0) if state.trades else 0.0)

    async def sync_fomo_positions(self, ws):
        """Подхватывает открытые позиции любого сканера и транслирует для них тики цен из WSS."""
        from tracker import PaperTracker
        p_tracker = self.tracker if self.tracker else PaperTracker()

        while self.running:
            try:
                open_positions = p_tracker.get_open_positions()
                for mint, pos in open_positions.items():
                    if mint not in self.trackers:
                        print(f"🔗 [WSS SYNC] Подключаем лайв-трекинг для {pos.symbol} (куплен сканером)")
                        st = TokenTrackerState(mint, pos.symbol, "")
                        st.is_entered = True
                        self.trackers[mint] = st
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
            except Exception as e:
                print(f"Ошибка синхронизации WSS: {e}")
            await asyncio.sleep(3)

    async def garbage_collector(self, ws):
        """Очищает оперативную память (RAM) от остывших токенов каждые 15 сек и отписывается от WSS."""
        while self.running:
            await asyncio.sleep(15)
            now = time.time()
            to_remove = []
            max_age = getattr(config, "STREAM_MAX_AGE_SEC", 300)
            _min_real_prog = float(getattr(config, "STREAM_GC_MIN_PROGRESS", 10.0))

            for mint, state in list(self.trackers.items()):
                # Позиции в открытых сделках никогда не удаляем из WSS-трекинга
                if state.is_entered:
                    continue

                idle_seconds = now - state.last_trade_time
                age_seconds = now - state.start_time

                # Условие очистки:
                # 1) Аудит заблокировал токен как скам
                # 2) Нет сделок более 40 секунд и прогресс кривой < 5%
                # 3) Токену больше max_age (300с) и он не набрал импульс (STREAM_GC_MIN_PROGRESS = 10%)
                if state.audit_failed or (idle_seconds > 40 and state.max_curve_progress < 5.0) or (age_seconds > max_age and state.max_curve_progress < _min_real_prog):
                    to_remove.append(mint)

            for mint in to_remove:
                _st = self.trackers.pop(mint, None)
                if _st is not None:
                    self.funnel[_st.stage] += 1
                try:
                    await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))
                except Exception:
                    pass

            if time.time() - self._last_funnel_print >= 60:
                self._last_funnel_print = time.time()
                try:
                    self.print_funnel()
                except Exception as _fe:
                    print(f"⚠️ funnel print err: {_fe}")

    async def connect_and_listen(self):
        """Фаза 1 и 2: Подключение к сырому бесплатному WebSocket и фильтрация в оперативной памяти (RAM)."""
        self.running = True
        uri = config.PUMPPORTAL_WSS
        backoff = 5.0

        while self.running:
            try:
                print("🟢 Подключение к бесплатному PumpPortal WSS (In-Memory Streaming)...")
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Origin": "https://pumpportal.fun"
                }
                async with websockets.connect(uri, extra_headers=headers, ping_interval=20, ping_timeout=20) as ws:
                    backoff = 5.0  # Успешное подключение — сброс задержки
                    # 1. Подписка на создание новых токенов (InitializeMint)
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    print("🚀 [STREAM] Подписка на поток новых токенов оформлена!")

                    # После реконнекта подписки на трейды теряются: переподписываем живые токены
                    for _t in self._bg_tasks:
                        _t.cancel()
                    self._bg_tasks = []
                    _keys = [m for m, st in self.trackers.items() if not st.is_dead]
                    for _i in range(0, len(_keys), 50):
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": _keys[_i:_i + 50]}))
                    if _keys:
                        print(f"🔁 [STREAM] переподписка на {len(_keys)} токенов после реконнекта.")

                    # 2. Запуск фоновых задач: очистка RAM и синхронизация открытых позиций
                    self._bg_tasks.append(asyncio.create_task(self.garbage_collector(ws)))
                    self._bg_tasks.append(asyncio.create_task(self.sync_fomo_positions(ws)))
                    try:
                        from shadow_tracker import ShadowTracker
                        shadow = ShadowTracker()
                        self._bg_tasks.append(asyncio.create_task(shadow.price_watcher_loop()))
                    except Exception as e:
                        pass

                    while self.running:
                        message = await ws.recv()
                        data = json.loads(message)
                        tx_type = data.get("txType")

                        # --- СОБЫТИЕ: МИНТ НОВОГО ТОКЕНА ---
                        if "mint" in data and tx_type == "create":
                            mint = data["mint"]
                            symbol = data.get("symbol", "UNKNOWN")
                            trader_pubkey = data.get("traderPublicKey", "")
                            initial_buy = float(data.get("initialBuy") or 0)

                            if initial_buy > 200_000_000:
                                continue  # Пропускаем явные мега-дампы

                            state = TokenTrackerState(mint, symbol, trader_pubkey, initial_buy)
                            self.trackers[mint] = state
                            self.created_total += 1

                            # Передаем в birth_tracker для созревания
                            try:
                                from birth_tracker import birth_tracker
                                birth_tracker.add_token(mint)
                            except Exception:
                                pass

                            # Мгновенно подписываемся на торги этого токена
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

                        # --- СОБЫТИЕ: ПОКУПКА ИЛИ ПРОДАЖА (ТРЕЙД) ---
                        elif tx_type in ["buy", "sell"]:
                            mint = data.get("mint")
                            state = self.trackers.get(mint)
                            if not state:
                                continue

                            v_sol = float(data.get("vSolInBondingCurve") or 0.0)
                            if v_sol == 0.0:
                                continue

                            sol_amount = float(data.get("solAmount") or 0.0)
                            if sol_amount == 0.0 and state.last_v_sol > 0 and v_sol > 0:
                                sol_amount = abs(v_sol - state.last_v_sol)
                            state.last_v_sol = v_sol

                            mc_sol = float(data.get("marketCapSol") or 0.0)
                            trader = data.get("traderPublicKey") or ""
                            now = time.time()
                            state.last_trade_time = now

                            trade_entry = {
                                'ts': now,
                                'type': tx_type,
                                'sol': sol_amount,
                                'v_sol': v_sol,
                                'mc_sol': mc_sol,
                                'trader': trader
                            }
                            state.trades.append(trade_entry)
                            self.trades_total += 1
                            self._mark(state, 1)

                            # Прогресс кривой связывания (Pump.fun стартует с 30 виртуальных SOL и финиширует при 85 реальных SOL)
                            real_sol = max(0.0, v_sol - 30.0)
                            progress = min(100.0, (real_sol / 85.0) * 100.0)
                            if progress > state.max_curve_progress:
                                state.max_curve_progress = progress

                            # Проверка на миграцию (Ракета ушла на Raydium)
                            if progress >= 100.0 and not state.is_migrated:
                                state.is_migrated = True
                                print(f"🚀🚀🚀 [РАКЕТА] {state.symbol} ({mint[:8]}) завершил кривую и мигрировал на Raydium!")
                                try:
                                    from birth_tracker import birth_tracker as _bt
                                    _bt.add_graduated(mint)
                                except Exception:
                                    pass
                                self.trackers.pop(mint, None)
                                await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))
                                continue

                            # Трансляция LIVE цен в позицию в реальном времени
                            if state.is_entered:
                                from tracker import PaperTracker
                                p_tracker = self.tracker if self.tracker else PaperTracker()
                                pos = p_tracker.positions.get(mint)
                                if pos and getattr(pos, "status", "") == "open":
                                    sol_usd = get_sol_price_sync()
                                    live_px = (mc_sol / 1_000_000_000.0) * sol_usd if mc_sol > 0 else (v_sol / 1_000_000_000.0) * sol_usd
                                    if live_px > 0:
                                        pos.current_price_usd = live_px
                                        pos.price_updated_at = now
                                        if live_px > float(getattr(pos, "max_price_usd", 0.0) or 0.0):
                                            pos.max_price_usd = live_px
                                            pos.peak_time = now
                                        if pos.entry_price_usd and pos.entry_price_usd > 0:
                                            pnl_pct = (live_px - pos.entry_price_usd) / pos.entry_price_usd
                                            pos.current_pnl_usd = pos.amount_usd * pnl_pct
                                        p_tracker.save_portfolio()

                            # === ФАЗА 2: ФИЛЬТРАЦИЯ В ОПЕРАТИВНОЙ ПАМЯТИ (RAM BUFFER) ===
                            if (not state.is_evaluated and not state.is_auditing and not state.is_entered 
                                    and getattr(config, "SNIPER_ENTRIES_ENABLED", True)):

                                # Слив дева (Dev Dump): если создатель продал хоть цент — мгновенный отсев
                                if tx_type == "sell" and trader and trader == state.trader_pubkey:
                                    state.dev_dumped = True
                                    state.is_evaluated = True
                                    self._fail(state, "дев продал")
                                    print(f"🚫 [STREAM DEV DUMP] {state.symbol} ({mint[:8]}): дев продал {sol_amount:.3f} SOL — мгновенный отсев.")
                                    continue

                                if getattr(state, "dev_dumped", False):
                                    state.is_evaluated = True
                                    continue

                                if state.trader_pubkey and any(t['type'] == 'sell' and t['trader'] == state.trader_pubkey for t in state.trades):
                                    state.dev_dumped = True
                                    state.is_evaluated = True
                                    self._fail(state, "дев продал")
                                    print(f"🚫 [STREAM DEV DUMP] {state.symbol} ({mint[:8]}): дев продавал в истории — мгновенный отсев.")
                                    continue

                                win_sec = getattr(config, "STREAM_WINDOW_SEC", 30)
                                recent = [t for t in state.trades if (now - t['ts']) <= win_sec]

                                buys = [t for t in recent if t['type'] == 'buy']
                                sells = [t for t in recent if t['type'] == 'sell']

                                # 1. Уникальные покупатели
                                unique_buyers = {t['trader'] for t in buys if t['trader'] and t['trader'] != state.trader_pubkey}
                                min_buyers = getattr(config, "STREAM_MIN_UNIQUE_BUYERS", 6)

                                if len(unique_buyers) <= min_buyers:
                                    self._fail(state, f"мало покупателей (<= {min_buyers})")
                                if len(unique_buyers) > min_buyers:
                                    self._mark(state, 2)
                                    buy_sol = sum(t['sol'] for t in buys)
                                    sell_sol = sum(t['sol'] for t in sells)
                                    min_sol = getattr(config, "STREAM_MIN_BUY_SOL", 2.5)

                                    # 2. Объем покупок
                                    if buy_sol < min_sol:
                                        self._fail(state, "мало SOL")
                                    if buy_sol >= min_sol:
                                        self._mark(state, 3)
                                        # 3. Баланс покупок и продаж (Buy/Sell pressure)
                                        min_bs = getattr(config, "STREAM_MIN_BS_RATIO", 1.8)
                                        if sell_sol > 0 and (buy_sol / sell_sol) < min_bs:
                                            self._fail(state, "buy/sell")
                                            continue
                                        self._mark(state, 4)

                                        # 4. Проверка на одиночного кита / манипуляцию (Sybil check)
                                        buyer_vols = {}
                                        for t in buys:
                                            w = t['trader']
                                            buyer_vols[w] = buyer_vols.get(w, 0.0) + t['sol']
                                        top_b_sol = max(buyer_vols.values()) if buyer_vols else 0.0
                                        max_ratio = getattr(config, "STREAM_MAX_TOP_BUYER_RATIO", 0.50)
                                        if buy_sol > 0 and (top_b_sol / buy_sol) > max_ratio:
                                            self._fail(state, "один кит-покупатель")
                                            continue
                                        self._mark(state, 5)

                                        # 5. Коридор кривой (не входить перед самой стеной миграции)
                                        max_prog = getattr(config, "STREAM_MAX_CURVE_PROGRESS", 85.0)
                                        if progress > max_prog:
                                            self._fail(state, "кривая слишком высоко")
                                            continue
                                        self._mark(state, 6)

                                        # 6. Защита от нулевого блока (минимум 5 секунд жизни)
                                        time_alive = now - state.start_time
                                        if time_alive < 5.0:
                                            self._fail(state, "младше 5с")
                                            continue

                                        # --- ТОКЕН СТАЛ ФИНАЛИСТОМ! ---
                                        state.is_auditing = True
                                        self._mark(state, 7)
                                        asyncio.create_task(self.audit_and_enter(state, len(unique_buyers), buy_sol))

            except websockets.exceptions.ConnectionClosed:
                print(f"⚠️ PumpPortal WSS соединение закрыто. Переподключение через {backoff:.0f}с...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 60.0)
            except Exception as e:
                print(f"❌ PumpPortal WSS Ошибка ({e}). Переподключение через {backoff:.0f}с...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 60.0)


# === DUMMY HTTP SERVER FOR RENDER ===
from aiohttp import web
import os

async def health_check(request):
    return web.Response(text="In-Memory Streaming Sniper is running securely!")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    print(f"🌐 [Render Server] Запуск HTTP сервера на порту {port} (Health-check)")
    await site.start()

async def main():
    await start_web_server()
    from main import scanner_loop, position_manager_loop
    from analyzer import Analyzer
    from tracker import PaperTracker
    
    analyzer = Analyzer()
    tracker = PaperTracker()
    
    print("🧬 [STREAM HYBRID] Запуск сканера DexScreener и менеджера позиций...")
    asyncio.create_task(scanner_loop(analyzer, tracker))
    asyncio.create_task(position_manager_loop(analyzer, tracker))
    
    sniper = PumpFunSniper(tracker)
    await sniper.connect_and_listen()

if __name__ == "__main__":
    asyncio.run(main())
