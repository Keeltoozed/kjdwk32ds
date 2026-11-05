import asyncio
import websockets
import json
import time
import aiohttp

# Программы бирж/агрегаторов. pump.fun и PumpSwap сверены с документацией, остальные - общеизвестные адреса.
# Фильтр НЕ полагается только на адреса: проходит и по тексту лога (Swap/Buy/Sell), так что ошибка в адресе не выбросит биржу.
DEX_PROGRAMS = (
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun (bonding curve)
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter v6
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",   # Meteora DLMM
)
SWAP_LOG_HINTS = ("Instruction: Buy", "Instruction: Sell", "Instruction: Swap", "SwapBaseIn", "SwapBaseOut", "ray_log", "Instruction: Route")


class CopyTrader:
    BANNED_FILE = "banned_whales.json"

    def __init__(self, tracker, analyzer=None):
        self.tracker = tracker
        self.analyzer = analyzer
        self._banned = self._load_banned()
        self._wstat = {}          # кошелёк -> [прибыльных копий, убыточных копий]
        self._copy_wallets = {}   # mint -> кошельки, чей сигнал мы скопировали
        self.wallets = self.load_wallets()
        import os as _os, config as _cfg
        self.HELIUS_API_KEY = _os.getenv("HELIUS_API_KEY") or getattr(_cfg, "HELIUS_API_KEY", "")
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}" if self.HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
        self.wss_url = f"wss://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}" if self.HELIUS_API_KEY else "wss://api.mainnet-beta.solana.com"
        self.processed_sigs = set()
        self._req_wallet = {}   # id запроса подписки -> кошелёк
        self._sub_wallet = {}   # id подписки -> кошелёк
        self._rate = {}         # кошелёк -> времена последних уведомлений
        self._muted = {}        # кошелёк -> до какого времени игнорируем
        self.whale_buys = {}      # mint -> {wallet: ts}
        try:
            _orig_close = self.tracker.close_position
            def _hooked_close(mint, exit_price, reason, *a, **k):
                try:
                    _p = self.tracker.get_open_positions().get(mint)
                    if _p is not None and str(getattr(_p, "source", "")).startswith("COPY") and mint in self._copy_wallets:
                        _e = float(getattr(_p, "entry_price_usd", 0.0) or getattr(_p, "entry_price", 0.0) or 0.0)
                        _x = float(exit_price or 0.0)
                        _sym = getattr(_p, "symbol", mint[:8])
                        _tp1 = getattr(_p, "tp1_done", False)
                        if _e > 0:
                            # Если позиция уже зафиксировала TP1 (+15%), сделка в целом плюсовая
                            _pnl = 15.0 if _tp1 else ((_x / _e - 1.0) * 100.0)
                            self._score_wallets(mint, _pnl, exit_reason=str(reason), symbol=_sym)
                except Exception as _err:
                    print(f"⚠️ [COPY-HOOK] Ошибка скоринга кита: {_err}")
                return _orig_close(mint, exit_price, reason, *a, **k)
            self.tracker.close_position = _hooked_close
        except Exception as _e:
            print(f"⚠️ [COPY] не удалось повесить учёт результатов на кошельки: {_e}")
        self.consensus_done = set()
        self.whale_first_price = {}  # mint -> цена в момент покупки ПЕРВОГО кита

    def _load_banned(self) -> set:
        """Загружает список навсегда забаненных китов из локального файла."""
        import os
        banned = set()
        if os.path.exists(self.BANNED_FILE):
            try:
                with open(self.BANNED_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        banned.update(data.keys())
                    elif isinstance(data, list):
                        banned.update(data)
            except Exception as e:
                print(f"⚠️ [COPY] Ошибка чтения {self.BANNED_FILE}: {e}")
        return banned

    def ban_whale_permanently(self, wallet: str, reason: str, symbol: str = "", pnl_pct: float = 0.0):
        """Навсегда банит кошелёк кита, если его сигнал не принёс прибыли:
        1. Добавляет в self._banned
        2. Удаляет из активных подписок
        3. Записывает в banned_whales.json на диске
        4. Обновляет Supabase: ставит tier = -1 (персистентный бан в облаке)
        """
        import os
        if not wallet or wallet in self._banned:
            return
        self._banned.add(wallet)

        # 1. Запись на диск
        records = {}
        if os.path.exists(self.BANNED_FILE):
            try:
                with open(self.BANNED_FILE, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    if not isinstance(records, dict):
                        records = {k: {} for k in records}
            except Exception:
                records = {}
        records[wallet] = {
            "banned_at": time.time(),
            "reason": reason,
            "symbol": symbol,
            "pnl_pct": round(pnl_pct, 2)
        }
        try:
            with open(self.BANNED_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ [COPY] Ошибка записи в {self.BANNED_FILE}: {e}")

        # 2. Обновляем в локальной базе охотника
        try:
            from smart_money_hunter import smart_money_hunter as _smh
            if wallet in _smh.db:
                _smh.db[wallet]["tier"] = -1
        except Exception:
            pass

        # 3. Обновляем в Supabase (tier = -1: навсегда забанен)
        try:
            def _bg_sb_ban():
                try:
                    import config as _cfg
                    url = getattr(_cfg, "SUPABASE_URL", None)
                    key = getattr(_cfg, "SUPABASE_KEY", None)
                    if url and key:
                        from supabase import create_client
                        sb = create_client(url, key)
                        sb.table("smart_wallets").update({"tier": -1}).eq("wallet", wallet).execute()
                        print(f"☁️ [SUPABASE] Кит {wallet[:8]}... помечен tier=-1 (BANNED)")
                except Exception as ex:
                    print(f"⚠️ [SUPABASE-BAN] Ошибка обновления в облаке: {ex}")
            import threading
            threading.Thread(target=_bg_sb_ban, daemon=True).start()
        except Exception:
            pass

        # 4. Удаляем из активных подписок
        trader_name = self.wallets.pop(wallet, f"@{wallet[:6]}")

        print(
            f"🚫 [WHALE PERMA-BAN] Кит {trader_name} ({wallet[:8]}...) НАВСЕГДА ЗАБАНЕН!\n"
            f"   Причина: {reason}\n"
            f"   Итог: PnL {pnl_pct:+.2f}%. Сигналы этого кита исключены из торговли!"
        )

    def load_wallets(self):
        """Кошельки для копирования.
        1. Читает всех китов из Supabase / SmartMoneyHunter (1500-2000 кошельков).
        2. Исключает навсегда забаненных (tier <= 0 или в banned_whales.json).
        3. Ранжирует всех китов по PnL и подтвержденным победам (от лучших к худшим).
        4. Отбирает ТОП-80 самых прибыльных китов для активной WSS-подписки Helius (без 429 и лимитов сокета).
        5. При убытке/нуле от кита -> мгновенный перманентный бан (tier=-1 в Supabase),
           и его место в ТОП-80 сразу занимает следующий кит из базы!
        """
        import os
        import config as _cf

        candidates = {}  # wallet -> dict
        
        # 1. Живая база охотника (уже загружена из Supabase)
        try:
            from smart_money_hunter import smart_money_hunter as _smh
            for w, data in (_smh.db or {}).items():
                if isinstance(data, dict) and data.get("chain", "solana") == "solana":
                    candidates[w] = data
        except Exception:
            pass

        # 2. Локальный smart_wallets_db.json
        if os.path.exists("smart_wallets_db.json"):
            try:
                with open("smart_wallets_db.json", "r", encoding="utf-8") as f:
                    for w, data in json.load(f).items():
                        if w not in candidates and isinstance(data, dict):
                            candidates[w] = data
            except Exception:
                pass

        # 3. Резервная прямая подгрузка из Supabase
        if len(candidates) < 10:
            try:
                url = getattr(_cf, "SUPABASE_URL", None)
                key = getattr(_cf, "SUPABASE_KEY", None)
                if url and key:
                    from supabase import create_client
                    sb = create_client(url, key)
                    res = sb.table("smart_wallets").select("*").limit(5000).execute()
                    for r in (res.data or []):
                        w = r.get("wallet")
                        if w:
                            candidates[w] = {
                                "chain": r.get("chain") or "solana",
                                "tier": int(r.get("tier") or 3),
                                "total_pnl": float(r.get("total_pnl") or 0.0),
                                "trade_count": int(r.get("trade_count") or 0),
                            }
            except Exception:
                pass

        min_pnl = float(getattr(_cf, "COPY_MIN_PNL", 300.0))
        max_active = int(getattr(_cf, "COPY_MAX_ACTIVE_WHALES", 80))

        valid_whales = []
        for w, d in candidates.items():
            if w in self._banned:
                continue
            tier = int(d.get("tier", 3) or 3)
            if tier <= 0:  # tier <= 0 означает забанен в Supabase
                self._banned.add(w)
                continue
            pnl = float(d.get("total_pnl", 0.0) or 0.0)
            trades = int(d.get("trade_count", 0) or 0)

            # Отсекаем только явный мусор с отрицательным PnL
            if pnl < min_pnl and tier not in (1, 2):
                continue

            tier_mult = 2.0 if tier == 1 else (1.4 if tier == 2 else 1.0)
            rank_score = (pnl * tier_mult) + (trades * 10.0)
            valid_whales.append((w, rank_score, tier, pnl, trades))

        # Сохраняем полный пул китов для скоринга в анализаторе и памп.фане
        self.all_whales_pool = {w for w, _, _, _, _ in valid_whales}

        # Сортируем: сверху самые прибыльные и проверенные киты
        valid_whales.sort(key=lambda x: x[1], reverse=True)

        # Отбираем ТОП-N для активной WSS подписки
        active_wallets = {}
        for w, _, tier, pnl, trades in valid_whales[:max_active]:
            active_wallets[w] = f"@Tier{tier}_{w[:4]}_+${pnl:,.0f}"

        # Дефолтный fallback если база пуста
        if not active_wallets:
            default_whales = {
                "5FGoPPj1nL8LCnfVnpTmreqQtqLuMXXAwuS1uahMrp8V": "@DumbCrayon",
                "2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V": "@Salem1299",
                "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm": "@Whale_TopPnL_1",
                "7rhxnLV8C76MiKSnSqhMYnNvBjRHdJP36yGjwmF3tV3x": "@Whale_TopPnL_2",
                "3FqQ42gknhMNNXnRMm7e4RU83Gk7VdcpRDQsgpn3eWPf": "@Whale_TopPnL_3",
            }
            for w, name in default_whales.items():
                if w not in self._banned:
                    active_wallets[w] = name

        print(
            f"🐋 [COPYTRADER] Загружено китов: {len(active_wallets)} активных в WSS "
            f"(из {len(candidates)} в базе Supabase) | Забанено за убытки: {len(self._banned)}"
        )
        return active_wallets

    def _score_wallets(self, mint, pnl_pct, exit_reason: str = "", symbol: str = ""):
        """Учёт результата КОПИИ по кошелькам.
        Если сделка дала убыток или 0 прибыли (pnl_pct <= 0) -> КИТ БАНИТСЯ НАВСЕГДА!
        """
        import config as _c
        whales = list(self._copy_wallets.get(mint, set()))
        if not whales:
            return

        for w in whales:
            st = self._wstat.setdefault(w, [0, 0])
            if pnl_pct > 1.0: # Чистая прибыль выше 1% (с запасом на комиссии)
                st[0] += 1
                trader_name = self.wallets.get(w, f"@{w[:6]}")
                print(
                    f"🏆 [WHALE PROFIT] Кит {trader_name} принёс прибыль {pnl_pct:+.2f}% на {symbol}! "
                    f"Статистика кита: {st[0]} побед / {st[1]} поражений. Оставляем в топе."
                )
            else:
                # МЫ НИЧЕГО НЕ ЗАРАБОТАЛИ (0% или минус) -> БАН НАВСЕГДА!
                st[1] += 1
                trader_name = self.wallets.get(w, f"@{w[:6]}")
                reason = f"Сигнал на {symbol} дал {pnl_pct:+.2f}% (убыток/ноль). Причина выхода: {exit_reason}"
                self.ban_whale_permanently(w, reason=reason, symbol=symbol, pnl_pct=pnl_pct)

    def _handle_ws_message(self, message) -> None:
        """Одно сообщение WSS. Проваленные транзакции (у ботов их много) и перегруз очереди отбрасываем,
        иначе на каждую запись уходит 1-3 запроса getTransaction -> 429 у Helius и разрывы соединения."""
        try:
            data = json.loads(message)
        except Exception:
            return
        # ответ на подписку: запоминаем, какой id подписки принадлежит какому кошельку
        if "id" in data and "result" in data and "method" not in data:
            w = getattr(self, "_req_wallet", {}).pop(data["id"], None)
            if w is not None:
                self._sub_wallet[data["result"]] = w
            return
        if data.get("method") != "logsNotification":
            return
        try:
            val = data["params"]["result"]["value"]
        except Exception:
            return
        if val.get("err"):
            return                      # транзакция провалилась - покупки не было
        sig = val.get("signature")
        if not sig or sig in self.processed_sigs:
            return

        # 1) не своп/покупка (переводы, стейкинг, вотчдоги и т.п.) - выбрасываем по логам, без запросов к сети
        logs = val.get("logs") or []
        try:
            import config as _cf
            if getattr(_cf, "COPY_PREFILTER", True) and logs:
                progs = tuple(getattr(_cf, "COPY_DEX_PROGRAMS", DEX_PROGRAMS))
                if not any((p in l) for l in logs for p in progs) and not any((h in l) for l in logs for h in SWAP_LOG_HINTS):
                    self._pre_dropped = getattr(self, "_pre_dropped", 0) + 1
                    return
        except Exception:
            pass

        # 2) кошелёк-спамер (MEV/бот): >N записей в минуту -> молчим 10 минут, копировать такого бессмысленно
        now = time.time()
        wallet = self._sub_wallet.get(data["params"].get("subscription"))
        if wallet:
            if now < self._muted.get(wallet, 0.0):
                return
            dq = self._rate.setdefault(wallet, [])
            dq.append(now)
            while dq and now - dq[0] > 60.0:
                dq.pop(0)
            try:
                import config as _cf2
                lim = int(getattr(_cf2, "COPY_MAX_TX_PER_MIN", 40))
            except Exception:
                lim = 40
            if len(dq) > lim:
                self._muted[wallet] = now + 600.0
                dq.clear()
                print(f"🔇 Копитрейдер: {self.wallets.get(wallet, wallet[:8])} шлёт >{lim} транзакций/мин (бот) - игнорируем 10 минут")
                return
        if getattr(self, "_pending", 0) >= 120:
            self._dropped = getattr(self, "_dropped", 0) + 1
            if self._dropped % 200 == 1:
                print(f"⚠️ Копитрейдер: очередь перегружена, пропущено {self._dropped} (отсеяно префильтром: {getattr(self, '_pre_dropped', 0)})")
            return
        self._pending = getattr(self, "_pending", 0) + 1
        asyncio.create_task(self._guarded_process(sig))

    async def _guarded_process(self, sig: str) -> None:
        if not hasattr(self, "_sem"):
            self._sem = asyncio.Semaphore(4)
        try:
            async with self._sem:
                await self.process_transaction(sig)
        except Exception as e:
            print(f"⚠️ Копитрейдер: ошибка обработки {sig[:8]}: {type(e).__name__}")
        finally:
            self._pending = max(0, getattr(self, "_pending", 1) - 1)

    async def _subscribe_new(self, ws, subscribed: set) -> int:
        """Подписывается на кошельки, на которые ещё нет подписки (в т.ч. найденные охотником уже после старта)."""
        n = 0
        for wallet in list(self.wallets.keys()):
            if wallet in subscribed:
                continue
            self._req_id = getattr(self, "_req_id", 0) + 1
            self._req_wallet[self._req_id] = wallet
            await ws.send(json.dumps({
                "jsonrpc": "2.0", "id": self._req_id, "method": "logsSubscribe",
                "params": [{"mentions": [wallet]}, {"commitment": "processed"}]}))
            subscribed.add(wallet)
            n += 1
            await asyncio.sleep(0.05)   # не заливаем сервер сотней подписок за один миг
        return n

    async def _refresh_loop(self, ws, subscribed: set):
        """Раз в 2 минуты перечитывает базу охотника и подписывается на новых китов, не переподключаясь."""
        while True:
            await asyncio.sleep(120)
            try:
                self.wallets = self.load_wallets()
                added = await self._subscribe_new(ws, subscribed)
                if added:
                    print(f"👥 Копитрейдер: подписался на {added} новых китов (всего {len(subscribed)})")
            except Exception as e:
                print(f"⚠️ Копитрейдер: ошибка обновления базы китов: {e}")
                return

    async def get_token_price(self, mint):
        try:
            import market_data
            p = await market_data.get_price(mint)
            if p > 0:
                return p
        except:
            pass
        try:
            from http_client import fetch_json
            status, data = await fetch_json(
                f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                timeout=8, retries=1)
            if status == 200 and data:
                pairs = data.get("pairs") or []
                if pairs:
                    return float(pairs[0].get("priceUsd", 0))
        except:
            pass
        return 0.0001

    async def fetch_transaction(self, signature):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        
        rpc_endpoints = [
            self.rpc_url,
            "https://api.mainnet-beta.solana.com/",
            "https://solana-rpc.publicnode.com/"
        ]
        
        async with aiohttp.ClientSession() as session:
            for rpc in rpc_endpoints:
                try:
                    async with session.post(rpc, json=payload, timeout=5) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        elif resp.status == 429:
                            continue # Пробуем следующий RPC
                except:
                    continue
        return None

    async def process_transaction(self, signature):
        if signature in self.processed_sigs: return
        self.processed_sigs.add(signature)
        if len(self.processed_sigs) > 1000: self.processed_sigs.clear()
        
        # Даем RPC ноде 1.5 секунды на индексацию
        await asyncio.sleep(1.5)
        
        tx_data = await self.fetch_transaction(signature)
        if not tx_data or "result" not in tx_data or not tx_data["result"]:
            await asyncio.sleep(1.5)
            tx_data = await self.fetch_transaction(signature)
            if not tx_data or "result" not in tx_data or not tx_data["result"]: return

        meta = tx_data["result"].get("meta", {})
        if not meta or meta.get("err"): return # Ошибка транзакции (Failed)

        pre_bals = meta.get("preTokenBalances", [])
        post_bals = meta.get("postTokenBalances", [])

        # Быстрый O(1) поиск китов в транзакции через пересечение множеств
        tx_owners = {b.get("owner") for b in pre_bals if b.get("owner")} | {b.get("owner") for b in post_bals if b.get("owner")}
        matching_whales = tx_owners.intersection(self.wallets.keys())
        if not matching_whales:
            return

        for wallet in matching_whales:
            trader_name = self.wallets[wallet]
            pre_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in pre_bals if b.get("owner") == wallet}
            post_dict = {b["mint"]: float(b["uiTokenAmount"]["uiAmountString"]) for b in post_bals if b.get("owner") == wallet}

            all_mints = set(pre_dict.keys()) | set(post_dict.keys())
            for mint in all_mints:
                # Раньше только pump-минты — пропускали Raydium-миграции и прямые листинги.
                # Теперь все минты: ИИ-гейт ниже отсеет мусор.
                if len(mint) < 30:
                    continue
                if mint in ["So11111111111111111111111111111111111111112", 
                            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 
                            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]:
                    continue
                
                pre_amt = pre_dict.get(mint, 0.0)
                post_amt = post_dict.get(mint, 0.0)

                # 1. ОТСЛЕЖИВАНИЕ СБРОСА КИТА: если кит продаёт монету, которая у нас открыта — выходим вслед за ним!
                if post_amt < pre_amt and pre_amt > 0:
                    open_pos = self.tracker.get_open_positions()
                    _own = str(getattr(open_pos.get(mint), "source", "")).startswith("COPY")
                    import config as _cx
                    if mint in open_pos and (_own or not getattr(_cx, "COPY_EXIT_ONLY_OWN", True)):
                        sell_pct = (pre_amt - post_amt) / pre_amt
                        cur_p = await self.get_token_price(mint)
                        print(f"🐋 [WHALE DUMP] {trader_name} продал {sell_pct*100:.0f}% доли {mint[:12]}! Срочно выходим вслед за китом...")
                        if sell_pct >= 0.70:
                            self.tracker.close_position(mint, cur_p, f"Whale Exit ({trader_name} dump {sell_pct*100:.0f}%)")
                        elif getattr(_cx, "COPY_FRONTRUN_ENABLED", False):
                            self.tracker.partial_close_position(mint, cur_p, 0.50, f"Whale Frontrun ({trader_name} sold {sell_pct*100:.0f}%)")

                elif post_amt > pre_amt: # Баланс вырос = ПОКУПКА
                    import config
                    now = time.time()
                    if wallet in self._banned:
                        continue
                    self.whale_buys.setdefault(mint, {})
                    self.whale_buys[mint][wallet] = now
                    self.whale_buys[mint] = {w: t for w, t in self.whale_buys[mint].items() if now - t <= 600}
                    n_whales = len(self.whale_buys[mint])
                    _consensus = getattr(config, 'WHALE_CONSENSUS', 1)
                    if n_whales == 1:
                        self.whale_first_price[mint] = await self.get_token_price(mint)
                        if _consensus > 1:
                            print(f"👁️ WHALE WATCH: {trader_name} купил {mint[:12]}... слежу за ценой (вход на {_consensus}-м ките).")
                        else:
                            print(f"🐋 WHALE SIGNAL: {trader_name} купил {mint[:12]}... мгновенный вход (consensus=1)!")
                    print(f"🚨 COPYTRADE: {trader_name} купил {mint[:12]}... (китов за 10 мин: {n_whales}/{_consensus})")
                    if n_whales < _consensus or mint in self.consensus_done:
                        continue  # ждём раннего консенсуса
                    self.consensus_done.add(mint)
                    # Guard от опоздания: цена не должна улететь от точки входа первого кита
                    cur_price = await self.get_token_price(mint)
                    first_price = self.whale_first_price.get(mint, 0)
                    max_runup = getattr(config, 'WHALE_MAX_RUNUP', 1.35)
                    if first_price > 0 and cur_price > first_price * max_runup:
                        print(f"🚫 [WHALE LATE] {mint[:12]}: цена +{(cur_price/first_price-1)*100:.0f}% с покупки 1-го кита — киты уже надули, входим в их выход.")
                        return
                    print(f"🐋 SMART MONEY CONSENSUS: {n_whales} кита, цена ещё не улетела (+{(cur_price/first_price-1)*100:.0f}%) — РАННИЙ вход!")
                    if len(self.tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                        print("🚫 Лимит позиций. Пропускаем копитрейд.")
                        return
                        
                    if self.analyzer:
                        print(f"🤖 Передаем сигнал кита ИИ на проверку...")
                        is_safe = await self.analyzer.analyze_token(mint)
                        if not is_safe:
                            print(f"🚫 ИИ забраковал токен кита {trader_name}. Спасли твои деньги от рагпула!")
                            return
                        else:
                            print(f"✅ ИИ одобрил токен кита! Покупаем!")
                    else:
                        print("⚠️ ИИ не подключен к Копитрейдеру. Покупаем вслепую!")
                        
                    price_usd = await self.get_token_price(mint)
                    symbol = f"COPY_{trader_name[1:5].upper()}"

                    # Защита: инсайдеры тоже сливают на копирующих. Полный антискам Grok-JEV (RugCheck/GoPlus/холдеры/Jupiter).
                    if getattr(config, "COPY_USE_ANTISCAM", True):
                        try:
                            from grok_jev import grok_jev as _gj
                            _pair = await _gj.fetch_pair(mint, "solana")
                            if not _pair:
                                print(f"🚫 [COPY] {mint[:12]}: нет данных пары, не копируем вслепую")
                                continue
                            _ok, _why, _hs = await _gj.full_anti_scam_audit(mint, symbol, "solana", _pair)
                            if not _ok:
                                print(f"🚫 [COPY-SCAM] {trader_name} {mint[:12]}: {_why}")
                                continue
                            if getattr(config, "JEV_REQUIRE_HOLDER_DATA", True) and not (
                                    float(_hs.get("top1_pct", 0) or 0) > 0 or float(_hs.get("top10_pct", 0) or 0) > 0):
                                print(f"🚫 [COPY] {mint[:12]}: нет данных о холдерах")
                                continue
                            _px = float(_pair.get("priceUsd", 0) or 0)
                            if _px > 0:
                                price_usd = _px
                            symbol = str((_pair.get("baseToken") or {}).get("symbol") or symbol)
                        except Exception as _e:
                            print(f"⚠️ [COPY] антискам недоступен ({type(_e).__name__}), не копируем")
                            continue
                    
                    capital = self.tracker.get_total_capital()
                    _pct = float(getattr(config, "COMPOUNDING_PCT", 0.10))
                    _base = float(getattr(config, "BASE_TRADE_AMOUNT_USD", 2.0))
                    _cap = float(getattr(config, "COPY_MAX_TICKET_USD", 4.0))
                    position_size = max(_base, min(_cap, capital * _pct))

                    if mint not in self._copy_wallets:
                        self._copy_wallets[mint] = set()
                    self._copy_wallets[mint].add(wallet)
                    self.tracker.add_position(symbol, mint, price_usd, amount_usd=position_size,
                                                source=f"COPY:{trader_name}")
                    print(f"✅ Успешно скопировали сделку {trader_name} на {position_size:.2f}$!")

    async def listen(self):
        try:
            import config as _c
            if not getattr(_c, "USE_COPYTRADE", False):
                print("👥 Копитрейдер ВЫКЛ (USE_COPYTRADE=False): чужие пампы не покупаем.")
                return
        except Exception:
            pass
        # Список WSS эндпоинтов (Helius с ключом или надёжные публичные ноды)
        wss_endpoints = []
        if getattr(self, "HELIUS_API_KEY", ""):
            wss_endpoints.append(self.wss_url)
        wss_endpoints.extend([
            "wss://api.mainnet-beta.solana.com/",
            "wss://solana-rpc.publicnode.com/",
        ])
        endpoint_idx = 0
        
        while True:
            self.wallets = self.load_wallets()
            current_wss = wss_endpoints[endpoint_idx % len(wss_endpoints)]
            try:
                async with websockets.connect(current_wss, ping_interval=20, ping_timeout=60, close_timeout=5, max_size=2 ** 22) as ws:
                    print(f"👥 Копитрейдер: Подключен к {current_wss.split('.')[0]}... Слушаем {len(self.wallets)} китов")
                    
                    subscribed = set()
                    self._req_wallet.clear(); self._sub_wallet.clear()
                    await self._subscribe_new(ws, subscribed)
                    _refresher = asyncio.create_task(self._refresh_loop(ws, subscribed))
                    try:
                        async for message in ws:
                            self._handle_ws_message(message)
                    finally:
                        _refresher.cancel()
                                
            except Exception as e:
                err_msg = str(e)
                _name = current_wss.split("?")[0]            # ключ из адреса в лог не пишем
                print(f"⚠️ Копитрейдер: обрыв {_name}: {err_msg[:90] or type(e).__name__}. Следующий сервер...")
                endpoint_idx += 1
                await asyncio.sleep(3)