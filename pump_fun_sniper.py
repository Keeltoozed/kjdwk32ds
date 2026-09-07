import asyncio
import websockets
import json
import config
import aiohttp
import time
from collections import defaultdict

class BondingCurveTracker:
    def __init__(self, mint: str, symbol: str):
        self.mint = mint
        self.symbol = symbol
        self.running = False
        self.start_time = time.time()
        
        # Метрики кривой
        self.trades = []
        self.unique_buyers = set()
        self.total_volume_sol = 0
        self.buys = 0
        self.sells = 0
        
    async def monitor(self):
        self.running = True
        uri = config.PUMPPORTAL_WSS
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://pumpportal.fun"
        }
        
        async with websockets.connect(uri, extra_headers=headers) as ws:
            # Подписываемся на сделки конкретного токена
            payload = {
                "method": "subscribeTokenTrade",
                "keys": [self.mint]
            }
            await ws.send(json.dumps(payload))
            print(f"📈 [Curve Tracker] Начало мониторинга кривой для {self.symbol} ({self.mint})")
            
            while self.running:
                try:
                    # Ожидание сделки с таймаутом, чтобы периодически выводить метрики
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(message)
                    
                    if data.get("mint") == self.mint:
                        tx_type = data.get("txType") # 'buy' или 'sell'
                        trader = data.get("traderPublicKey")
                        sol_amount = data.get("vSolInBondingCurve", 0) # Показывает текущий баланс пула
                        trade_tokens = data.get("tokenAmount", 0) # API отдает кол-во токенов
                        
                        self.trades.append({
                            "type": tx_type,
                            "time": time.time(),
                            "trader": trader,
                            "curve_sol": sol_amount,
                            "tokens": trade_tokens
                        })
                        
                        if tx_type == "buy":
                            self.buys += 1
                            self.unique_buyers.add(trader)
                        elif tx_type == "sell":
                            self.sells += 1
                            
                        # Считаем скорость (Velocity)
                        elapsed_minutes = (time.time() - self.start_time) / 60
                        if elapsed_minutes > 0:
                            tx_per_min = (self.buys + self.sells) / elapsed_minutes
                            
                            # Проверяем прогресс Bonding Curve
                            curve_progress_pct = max(0, min(100, ((sol_amount - 30) / 55) * 100))
                            
                            print(f"[{self.symbol}] 📊 Прогресс: {curve_progress_pct:.1f}% | Tx/Min: {tx_per_min:.1f} | Уникальных кошельков: {len(self.unique_buyers)} | Покупки/Продажи: {self.buys}/{self.sells}")
                            
                            # Условия для запуска ML-анализа (один раз)
                            if curve_progress_pct >= 20.0 and curve_progress_pct <= 40.0 and len(self.unique_buyers) > 5 and not getattr(self, "ml_evaluated", False):
                                self.ml_evaluated = True
                                print(f"🚀 [ML СИГНАЛ] {self.symbol} достиг нужного объема! Формируем фичи...")
                                
                                # Считаем балансы
                                balances = {}
                                for t in self.trades:
                                    w = t["trader"]
                                    amt = t.get("tokens", 0)
                                    if t["type"] == "buy":
                                        balances[w] = balances.get(w, 0) + amt
                                    else:
                                        balances[w] = max(0, balances.get(w, 0) - amt)
                                        
                                total_supply = 1_000_000_000
                                
                                # Предполагаем, что разраб - это создатель первого трейда
                                dev_wallet = self.trades[0]["trader"] if self.trades else ""
                                dev_holding_pct = (balances.get(dev_wallet, 0) / total_supply) * 100
                                
                                sorted_bals = sorted(balances.values(), reverse=True)
                                top_10_holding_pct = (sum(sorted_bals[:10]) / total_supply) * 100
                                
                                token_context = {
                                    "mint": self.mint,
                                    "dev_holding_pct": min(100.0, dev_holding_pct),
                                    "top_10_holding_pct": min(100.0, top_10_holding_pct),
                                    "tx_velocity_1m": tx_per_min,
                                    "has_socials": 1, 
                                    "funded_from_cex": 0
                                }
                                
                                from ai_brain import ask_ai_oracle
                                decision_res = await ask_ai_oracle(token_context)
                                
                                conf = decision_res.get("confidence", 0)
                                if decision_res.get("decision") == "BUY" or conf > 75:
                                    print(f"✅ [AI ОДОБРЕНО] {self.symbol} прошел XGBoost (Уверенность: {conf}%)!")
                                    
                                    from tracker import PaperTracker
                                    tracker = PaperTracker()
                                    
                                    # Импортируем ExitManager для симуляции умных выходов на Paper Trading
                                    from exit_manager import ExitManager
                                    exit_mgr = ExitManager(config.HELIUS_RPC_URL)
                                    
                                    actual_price = (sol_amount / 1_000_000_000.0) * 150.0 # примерный расчет
                                    
                                    capital = tracker.get_total_capital()
                                    base_position = capital * (config.REINVEST_PERCENT / 100.0)
                                    
                                    liq_usd = sol_amount * 150.0 
                                    max_allowed_by_pool = liq_usd * 0.05
                                    
                                    position_size = max(4.0, min(base_position, max_allowed_by_pool, 100.0))
                                    
                                    if position_size >= 4.0:
                                        print(f"🚀 PAPER СНАЙП PUMP.FUN РАКЕТЫ {self.symbol} ({self.mint})! Входим на {position_size}$")
                                        tracker.add_position(self.symbol, self.mint, actual_price, position_size)
                                        
                                        # Коллбек для ExitManager (закрываем бумажную сделку)
                                        async def panic_sell_callback(token_mint, reason):
                                            print(f"📉 [PAPER] PANIC SELL TRIGGERED для {token_mint}. Причина: {reason}")
                                            pos = tracker.positions.get(token_mint)
                                            if pos and pos.status == "open":
                                                # Используем текущую цену из трекера, либо цену входа, если еще не обновилась
                                                exit_price = pos.current_price_usd if pos.current_price_usd > 0 else pos.entry_price_usd
                                                tracker.close_position(token_mint, exit_price, reason)
                                                
                                        # Используем trader_pubkey, который мы получали в connect_and_listen
                                        # Если его нет, используем заглушку, чтобы код не падал
                                        dev_wallet_pubkey = "11111111111111111111111111111111" # Нужен проброс trader_pubkey, ставим заглушку, если его нет в scope
                                        
                                        # Запускаем мониторинг выхода в фоне
                                        asyncio.create_task(
                                            exit_mgr.start_monitoring(
                                                token_mint=self.mint,
                                                dev_wallet=dev_wallet_pubkey, 
                                                initial_dev_balance=1_000_000_000, # Идеально было бы взять из dev_profile, но пока заглушка
                                                on_panic_sell=panic_sell_callback
                                            )
                                        )
                                    else:
                                        print(f"🚫 Отказ (Ликвидность): Недостаточно ликвидности для входа.")
                                        
                                    self.running = False
                                else:
                                    print(f"🚫 [AI ОТКАЗ] {self.symbol} забракован (Уверенность: {conf}%). Отменяем мониторинг.")
                                    self.running = False
                                
                except asyncio.TimeoutError:
                    # Раз в 5 секунд, если нет сделок, проверяем не сдох ли токен
                    elapsed_minutes = (time.time() - self.start_time) / 60
                    if elapsed_minutes > 5 and len(self.trades) < 10:
                        print(f"💀 [Curve Tracker] {self.symbol} мертв (нет активности за 5 минут). Снимаем мониторинг.")
                        self.running = False
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    print(f"Curve Tracker Error: {e}")
                    break

class PumpFunSniper:
    def __init__(self):
        self.running = False
        self.active_trackers = {} # mint -> tracker_task

    async def get_dev_profile(self, trader_pubkey: str) -> dict:
        profile = {
            "balance_sol": 0,
            "funded_from_cex": False,
            "is_fresh_wallet": True,
            "tx_count": 0,
            "risk_score": 50
        }
        if not trader_pubkey:
            return profile

        payload_balance = {
            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
            "params": [trader_pubkey, {"encoding": "jsonParsed"}]
        }
        
        payload_sigs = {
            "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
            "params": [trader_pubkey, {"limit": 10}]
        }

        async with aiohttp.ClientSession() as session:
            try:
                # 1. Баланс
                async with session.post(config.HELIUS_RPC_URL, json=payload_balance, timeout=2) as resp:
                    data = await resp.json()
                    lamports = data.get("result", {}).get("value", {}).get("lamports", 0) if data.get("result", {}).get("value") else 0
                    profile["balance_sol"] = lamports / 1e9

                # 2. История (Свежерег и поиск Funding Source)
                async with session.post(config.HELIUS_RPC_URL, json=payload_sigs, timeout=3) as resp:
                    data = await resp.json()
                    sigs = data.get("result", [])
                    profile["tx_count"] = len(sigs)
                    
                    if len(sigs) > 5:
                        profile["is_fresh_wallet"] = False
                        
                    # 3. Эвристика Funding Source
                    # Если кошелек совершил < 10 транзакций, вытягиваем самую первую транзакцию
                    # чтобы проверить, откуда он получил свои первые SOL
                    if 0 < len(sigs) <= 10:
                        oldest_sig = sigs[-1]["signature"]
                        payload_tx = {
                            "jsonrpc": "2.0", "id": 1, "method": "getTransactions",
                            "params": [[oldest_sig], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                        }
                        async with session.post(config.HELIUS_RPC_URL, json=payload_tx, timeout=3) as tx_resp:
                            tx_data = await tx_resp.json()
                            try:
                                # Ищем трансфер в инструкциях
                                first_tx = tx_data.get("result", [])[0]
                                instrs = first_tx["transaction"]["message"]["instructions"]
                                for inst in instrs:
                                    if inst.get("program") == "system" and inst.get("parsed", {}).get("type") == "transfer":
                                        source = inst["parsed"]["info"]["source"]
                                        # Список известных горячих кошельков CEX (Binance, Coinbase, Kraken и т.д.)
                                        # Пример адреса Binance: 5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1
                                        cex_hot_wallets = ["5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"]
                                        if source in cex_hot_wallets:
                                            profile["funded_from_cex"] = True
                            except:
                                pass
                    
            except Exception as e:
                pass

        return profile

    async def connect_and_listen(self):
        self.running = True
        uri = config.PUMPPORTAL_WSS
        
        while self.running:
            try:
                print("🟢 Подключение к PumpPortal WSS (Слушаем новые токены)...")
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Origin": "https://pumpportal.fun"
                }
                async with websockets.connect(uri, extra_headers=headers) as websocket:
                    payload = {"method": "subscribeNewToken"}
                    await websocket.send(json.dumps(payload))
                    print("🚀 Успешная подписка на поток InitializeMint (Pump.fun)!")
                    
                    while self.running:
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        if "mint" in data and data.get("txType") == "create":
                            mint = data["mint"]
                            symbol = data.get("symbol", "UNKNOWN")
                            trader_pubkey = data.get("traderPublicKey")
                            initial_buy = data.get("initialBuy", 0)
                            
                            print(f"\n🔔 [НОВЫЙ ТОКЕН] {symbol} | Mint: {mint}")
                            
                            dev_profile = await self.get_dev_profile(trader_pubkey)
                            
                            print(f"🔍 [Dev Profile] Balance: {dev_profile['balance_sol']:.2f} SOL | Fresh: {dev_profile['is_fresh_wallet']} | CEX Funded: {dev_profile['funded_from_cex']}")
                            
                            if initial_buy == 0:
                                print("🚫 Отказ: Dev не купил ни одного токена (0 Tokens). Нет 'шкуры на кону'.")
                                continue
                            if initial_buy > 200_000_000: # Максимум 20% саплая
                                print(f"🚫 Отказ: Dev выкупил слишком много ({initial_buy:,.0f} Tokens). Риск моментального дампа.")
                                continue
                                
                            # Если токен прошел первичный фильтр, запускаем трекер кривой связывания
                            tracker = BondingCurveTracker(mint, symbol)
                            self.active_trackers[mint] = asyncio.create_task(tracker.monitor())
                                
            except websockets.exceptions.ConnectionClosed:
                print("⚠️ WSS соединение закрыто. Переподключение через 2 секунды...")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"❌ WSS Ошибка: {e}")
                await asyncio.sleep(2)

if __name__ == "__main__":
    sniper = PumpFunSniper()
    asyncio.run(sniper.connect_and_listen())
