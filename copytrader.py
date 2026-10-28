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
    def __init__(self, tracker, analyzer=None):
        self.tracker = tracker
        self.analyzer = analyzer
        self.wallets = self.load_wallets()
        import os as _os, config as _cfg
        self.HELIUS_API_KEY = _os.getenv("HELIUS_API_KEY") or getattr(_cfg, "HELIUS_API_KEY", "")
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"
        self.wss_url = f"wss://mainnet.helius-rpc.com/?api-key={self.HELIUS_API_KEY}"
        self.processed_sigs = set()
        self._req_wallet = {}   # id запроса подписки -> кошелёк
        self._sub_wallet = {}   # id подписки -> кошелёк
        self._rate = {}         # кошелёк -> времена последних уведомлений
        self._muted = {}        # кошелёк -> до какого времени игнорируем
        self.whale_buys = {}      # mint -> {wallet: ts}
        self._copy_wallets = {}   # mint -> кошельки, чей сигнал мы скопировали
        self._wstat = {}          # кошелёк -> [прибыльных копий, убыточных копий]
        self._banned = set()      # кошельки-«фармеры»: копии не приносят прибыль
        try:
            _orig_close = self.tracker.close_position
            def _hooked_close(mint, exit_price, reason, *a, **k):
                try:
                    _p = self.tracker.get_open_positions().get(mint)
                    if _p is not None and str(getattr(_p, "source", "")).startswith("COPY") and mint in self._copy_wallets:
                        _e = float(getattr(_p, "entry_price", 0) or 0)
                        _x = float(exit_price or 0)
                        if _e > 0:
                            self._score_wallets(mint, (_x / _e - 1.0) * 100.0)
                except Exception:
                    pass
                return _orig_close(mint, exit_price, reason, *a, **k)
            self.tracker.close_position = _hooked_close
        except Exception as _e:
            print(f"⚠️ [COPY] не удалось повесить учёт результатов на кошельки: {_e}")
        self.consensus_done = set()
        self.whale_first_price = {}  # mint -> цена в момент покупки ПЕРВОГО кита
        
    def load_wallets(self):
        """Кошельки для копирования. Источники по порядку:
        1) smart_money_hunter.db — база В ПАМЯТИ, которую охотник пополняет раз в 15 мин (самые свежие данные);
        2) smart_wallets_db.json — файл, который охотник пишет рядом с ботом (пропадает при рестарте Render, если не закоммичен);
        3) smart_wallets.txt — список (ручные + tier 1-2 из охотника).
        Из базы берутся только tier <= COPY_MIN_TIER (по умолчанию 2)."""
        import os
        import config as _cf
        min_tier = int(getattr(_cf, "COPY_MIN_TIER", 2))
        wallets = {}

        def _add_db(db):
            for w, data in (db or {}).items():
                if not isinstance(data, dict) or data.get("chain") != "solana" or w in wallets:
                    continue
                tier = int(data.get("tier", 3) or 3)
                if tier > min_tier:
                    continue   # слабые кошельки (tier-3: 1 удачный токен) не копируем
                pnl = float(data.get("total_pnl", 0) or 0)
                try:
                    _wins = data.get("winning_tokens", [])
                    if isinstance(_wins, str):
                        _wins = json.loads(_wins or "[]")
                    if len(_wins) < int(getattr(_cf, "COPY_MIN_WINS", 2)) or pnl < float(getattr(_cf, "COPY_MIN_PNL", 1000)):
                        continue   # одиночная удача — не «умный» кошелёк
                except Exception:
                    pass
                wallets[w] = f"@Tier{tier}_{w[:4]}_+${pnl:,.0f}"

        # 1) живая база охотника (тот же процесс)
        try:
            from smart_money_hunter import smart_money_hunter as _smh
            _add_db(_smh.db)
        except Exception:
            pass
        # 2) файл базы
        if os.path.exists("smart_wallets_db.json"):
            try:
                with open("smart_wallets_db.json", "r", encoding="utf-8") as f:
                    _add_db(json.load(f))
            except Exception:
                pass
        # 3) txt (ручные и синхронизированные tier 1-2)
        if os.path.exists("smart_wallets.txt"):
            try:
                with open("smart_wallets.txt", "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and line not in wallets:
                            wallets[line] = f"@Smart_{line[:4]}"
            except Exception:
                pass

        if not wallets:
            wallets = {
                "5FGoPPj1nL8LCnfVnpTmreqQtqLuMXXAwuS1uahMrp8V": "@DumbCrayon",
                "2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V": "@Salem1299"
            }
        return wallets

    def _score_wallets(self, mint, pnl_pct):
        """Учёт результата КОПИИ по кошелькам. Кошелёк, чьи копии в основном убыточны, банится."""
        import config as _c
        min_n = int(getattr(_c, "COPY_BAN_MIN_COPIES", 3))
        max_wr = float(getattr(_c, "COPY_BAN_WINRATE", 0.34))
        for w in self._copy_wallets.get(mint, ()):
            st = self._wstat.setdefault(w, [0, 0])
            st[0 if pnl_pct > 3.0 else 1] += 1
            n = st[0] + st[1]
            if n >= min_n and st[0] / n < max_wr and w not in self._banned:
                self._banned.add(w)
                print(f"🚫 [COPY] кошелёк {w[:6]}… забанен: {st[0]}/{n} прибыльных копий (фармер/шум)")

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

        # Проверяем транзакцию для каждого из 5 китов
        for wallet, trader_name in self.wallets.items():
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
                    if n_whales == 1:
                        self.whale_first_price[mint] = await self.get_token_price(mint)
                        print(f"👁️ WHALE WATCH: {trader_name} купил {mint[:12]}... слежу за ценой (вход на 2-м ките).")
                    print(f"🚨 COPYTRADE: {trader_name} купил {mint[:12]}... (китов за 10 мин: {n_whales}/{getattr(config, 'WHALE_CONSENSUS', 2)})")
                    if n_whales < getattr(config, 'WHALE_CONSENSUS', 2) or mint in self.consensus_done:
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
                    position_size = max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0)))
                    position_size = min(position_size, float(getattr(config, "COPY_MAX_TICKET_USD", 15.0)))
                    
                    self._copy_wallets[mint] = set(self.whale_buys.get(mint, {}).keys())
                    self.tracker.add_position(symbol, mint, price_usd, amount_usd=position_size,
                                                source=f"COPY:{trader_name}")
                    print(f"✅ Успешно скопировали сделку {trader_name} на {position_size}$!")

    async def listen(self):
        try:
            import config as _c
            if not getattr(_c, "USE_COPYTRADE", False):
                print("👥 Копитрейдер ВЫКЛ (USE_COPYTRADE=False): чужие пампы не покупаем.")
                return
        except Exception:
            pass
        # Список бесплатных публичных WSS
        wss_endpoints = [
            self.wss_url, # Helius
            "wss://api.mainnet-beta.solana.com/",
            "wss://solana-rpc.publicnode.com/"
        ]
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