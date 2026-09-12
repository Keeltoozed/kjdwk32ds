import asyncio
import json
import time
import websockets
import tornado.websocket
import tornado.iostream
import aiohttp
import pandas as pd
import numpy as np
import config
from ai_brain import ask_pro_oracle
from sol_price import get_sol_price_sync

class TokenTrackerState:
    def __init__(self, mint: str, symbol: str, trader_pubkey: str):
        self.mint = mint
        self.symbol = symbol
        self.trader_pubkey = trader_pubkey
        
        self.trades = []
        self.unique_buyers = set()
        self.total_volume_sol = 0
        self.start_time = time.time()
        
        self.is_ai_evaluated = False
        self.is_entered = False
        self.ai_confidence = 0
        self.ml_features_dict = {}
        
        self.max_curve_progress = 0.0
        self.is_migrated = False # Попала на Raydium (ракета)
        self.is_dead = False # Прошло 30 мин, не мигрировала (скам)
        self.entry_price_sol = 0.0

class PumpFunSniper:
    def __init__(self):
        self.running = False
        self.trackers = {} # mint -> TokenTrackerState

    async def evaluate_and_enter(self, state: TokenTrackerState):
        try:
            df = pd.DataFrame(state.trades)
            df['curve_sol_diff'] = df['curve_sol'].diff().fillna(0)
            df['volume_buy'] = np.where(df['type'] == 'buy', df['curve_sol_diff'].abs(), 0)
            df['volume_sell'] = np.where(df['type'] == 'sell', df['curve_sol_diff'].abs(), 0)
            df['price'] = df['curve_sol'] / 1_000_000_000 
            
            total_vol = df['volume_buy'] + df['volume_sell']
            df['ofi'] = np.where(total_vol > 0, (df['volume_buy'] - df['volume_sell']) / total_vol, 0)
            df['ofi_ema_5'] = df['ofi'].ewm(span=5, adjust=False).mean()
            df['total_vol'] = total_vol
            df['vol_change'] = df['total_vol'].diff().fillna(0)
            df['vol_acceleration'] = df['vol_change'].diff().fillna(0)
            
            df['log_return'] = np.log(df['price'] / df['price'].shift(1).replace(0, np.nan)).fillna(0)
            df['volatility_15m'] = df['log_return'].rolling(window=min(15, len(df))).std() * np.sqrt(15)
            df['volatility_15m'] = df['volatility_15m'].fillna(0)
            
            df['momentum_5m'] = df['price'].pct_change(min(5, len(df)-1)).fillna(0)
            df['momentum_15m'] = df['price'].pct_change(min(15, len(df)-1)).fillna(0)
            df['tx_count'] = 1
            
            pro_res = await ask_pro_oracle(df)
            state.ai_confidence = pro_res.get("score", 0)
            is_pro_approved = pro_res.get("is_approved", False)
            
            for k, v in df.iloc[-1].to_dict().items():
                if isinstance(v, pd.Timestamp): state.ml_features_dict[k] = str(v)
                elif hasattr(v, 'item'): state.ml_features_dict[k] = v.item()
                else: state.ml_features_dict[k] = v
                
        except Exception as e:
            print(f"⚠️ Ошибка подготовки PRO фичей: {e}")
            is_pro_approved = False
            state.ai_confidence = 0

        state.is_ai_evaluated = True

        if is_pro_approved:
            print(f"✅ [PRO AI ОДОБРЕНО] {state.symbol} прошел анализ! Уверенность: {state.ai_confidence}%")
            state.is_entered = True
            
            from tracker import PaperTracker
            from exit_manager import ExitManager
            tracker = PaperTracker()
            exit_mgr = ExitManager(config.HELIUS_RPC_URL)
            
            sol_amount = state.trades[-1]["curve_sol"] if state.trades else 0
            actual_price = (sol_amount / 1_000_000_000.0) * get_sol_price_sync() 
            
            capital = tracker.get_total_capital()
            base_position = capital * (config.REINVEST_PERCENT / 100.0)
            liq_usd = sol_amount * get_sol_price_sync() 
            max_allowed = liq_usd * 0.05
            position_size = max(4.0, min(base_position, max_allowed, 100.0))
            
            if position_size >= 4.0:
                print(f"🚀 PAPER СНАЙП {state.symbol}! Входим на {position_size}$")
                tracker.add_position(state.symbol, state.mint, actual_price, position_size, ml_features=state.ml_features_dict, ml_confidence=state.ai_confidence)
                
                async def panic_sell_callback(token_mint, reason):
                    pos = tracker.positions.get(token_mint)
                    if pos and pos.status == "open":
                        exit_price = pos.current_price_usd if pos.current_price_usd > 0 else pos.entry_price_usd
                        tracker.close_position(token_mint, exit_price, reason)
                        
                dev_wallet_pubkey = state.trader_pubkey if state.trader_pubkey else "11111111111111111111111111111111"
                asyncio.create_task(
                    exit_mgr.start_monitoring(
                        token_mint=state.mint,
                        dev_wallet=dev_wallet_pubkey, 
                        initial_dev_balance=1_000_000_000, 
                        on_panic_sell=panic_sell_callback
                    )
                )
        else:
            print(f"🚫 [AI ОТКАЗ] {state.symbol} (Уверенность: {state.ai_confidence}%). Следим для сбора метрик.")
            # === SHADOW TRADING HOOK ===
            try:
                from shadow_tracker import ShadowTracker
                shadow = ShadowTracker()
                current_price = (state.trades[-1]["curve_sol"] / 1_000_000_000.0) * get_sol_price_sync() if state.trades else 0
                shadow.log_rejection(
                    mint=state.mint,
                    reason=f"AI Score too low: {state.ai_confidence:.1f}%",
                    score=state.ai_confidence,
                    price=current_price,
                    features=state.ml_features_dict
                )
            except Exception as e:
                print(f"Ошибка вызова ShadowTracker: {e}")


    async def garbage_collector(self, ws):
        """Очищает мертвые трекеры и отписывается от WSS"""
        while self.running:
            await asyncio.sleep(60)
            now = time.time()
            to_remove = []
            
            for mint, state in list(self.trackers.items()):
                # Если прошло 30 минут, считаем что токен умер
                if now - state.start_time > 1800:
                    state.is_dead = True
                    print(f"💀 [Очистка] Токен {state.symbol} мертв (30 мин без миграции).")
                    
                    # Если был отвергнут ИИ, логируем неудачу (True Negative)
                    if state.is_ai_evaluated and not state.is_entered:
                        pass
                        
                    to_remove.append(mint)
                    
            for mint in to_remove:
                del self.trackers[mint]
                try:
                    await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))
                except:
                    pass

    async def connect_and_listen(self):
        self.running = True
        uri = config.PUMPPORTAL_WSS
        
        while self.running:
            try:
                print("🟢 Подключение к ЕДИНОМУ PumpPortal WSS...")
                headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://pumpportal.fun"
                }
                async with websockets.connect(uri, extra_headers=headers) as ws:
                    # 1. Подписка на новые токены
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    print("🚀 Подписка на InitializeMint оформлена!")
                    
                    # 2. Запуск сборщика мусора и Shadow Watcher
                    asyncio.create_task(self.garbage_collector(ws))
                    try:
                        from shadow_tracker import ShadowTracker
                        shadow = ShadowTracker()
                        asyncio.create_task(shadow.price_watcher_loop())
                    except Exception as e:
                        print(f"Ошибка запуска Shadow Watcher: {e}")
                    
                    while self.running:
                        message = await ws.recv()
                        data = json.loads(message)
                        
                        tx_type = data.get("txType")
                        
                        # --- НОВЫЙ ТОКЕН ---
                        if "mint" in data and tx_type == "create":
                            mint = data["mint"]
                            symbol = data.get("symbol", "UNKNOWN")
                            trader_pubkey = data.get("traderPublicKey", "")
                            initial_buy = data.get("initialBuy", 0)
                            
                            if initial_buy > 200_000_000:
                                continue # Пропускаем мега-дампы
                                
                            print(f"\n🔔 [НОВЫЙ ТОКЕН] {symbol} | Mint: {mint}")
                            
                            self.trackers[mint] = TokenTrackerState(mint, symbol, trader_pubkey)
                            # Динамически добавляем подписку на торги этого токена в ЭТОТ ЖЕ сокет
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                            
                        # --- СДЕЛКА ПО ТОКЕНУ ---
                        elif tx_type in ["buy", "sell"]:
                            mint = data.get("mint")
                            state = self.trackers.get(mint)
                            if not state:
                                continue
                                
                            sol_amount = data.get("vSolInBondingCurve", 0)
                            if sol_amount == 0:
                                continue
                                
                            # Фиксация трейда
                            state.trades.append({
                                'timestamp': time.time(),
                                'type': tx_type,
                                'curve_sol': sol_amount,
                                'wallet': data.get('traderPublicKey')
                            })
                            state.unique_buyers.add(data.get('traderPublicKey'))
                            
                            progress = (sol_amount / 85.0) * 100
                            if progress > state.max_curve_progress:
                                state.max_curve_progress = progress
                            
                            # Проверка на миграцию (Ракета)
                            if progress >= 100.0 and not state.is_migrated:
                                state.is_migrated = True
                                print(f"🚀🚀🚀 [РАКЕТА] Токен {state.symbol} мигрировал на Raydium!")
                                if state.is_ai_evaluated and not state.is_entered:
                                    # ИИ отверг, а она взлетела! Логируем (False Negative)
                                    pass
                                    
                                # Отписываемся, чтобы не засорять сокет Raydium торгами
                                del self.trackers[mint]
                                await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))
                                continue

                            # Обновляем LIVE цену в трекере, если мы в позиции!
                            if state.is_entered:
                                from tracker import PaperTracker
                                from sol_price import get_sol_price_sync
                                p_tracker = PaperTracker()
                                pos = p_tracker.positions.get(mint)
                                if pos and pos.status == "open":
                                    live_price = (sol_amount / 1_000_000_000.0) * get_sol_price_sync()
                                    pos.current_price_usd = live_price
                                    if live_price > pos.max_price_usd:
                                        pos.max_price_usd = live_price
                                    # Рассчитываем PNL для логов (Stop-Loss все равно сработает в главном цикле, но быстрее)
                                    pnl_pct = (live_price - pos.entry_price_usd) / pos.entry_price_usd
                                    pos.current_pnl_usd = pos.amount_usd * pnl_pct
                                    p_tracker.save_portfolio()
                                    
                            # Если достигли 20%, оцениваем ИИ
                            if not state.is_ai_evaluated and progress >= 20.0 and len(state.trades) > 5:
                                await self.evaluate_and_enter(state)
                                
            except websockets.exceptions.ConnectionClosed:
                print("⚠️ WSS соединение закрыто. Переподключение через 2 секунды...")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"❌ WSS Ошибка: {e}")
                await asyncio.sleep(2)

# === DUMMY HTTP SERVER FOR RENDER ===
from aiohttp import web
import os

async def health_check(request):
    return web.Response(text="Sniper Bot is running securely!")

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
    # Запускаем dummy-сервер для Render в фоне
    await start_web_server()
    
    # === ГИБРИДНЫЙ РЕЖИМ (СНАЙПЕР + ПАРСЕР СТАРЫХ МОНЕТ) ===
    from main import scanner_loop, position_manager_loop
    from analyzer import Analyzer
    from tracker import PaperTracker
    
    analyzer = Analyzer()
    tracker = PaperTracker()
    
    # Запускаем фоновые задачи для старых монет
    print("🧬 [HYBRID MODE] Запуск сканера DexScreener...")
    asyncio.create_task(scanner_loop(analyzer, tracker))
    asyncio.create_task(position_manager_loop(analyzer, tracker))
    
    # Запускаем основной луп снайпера (новые монеты по WSS)
    sniper = PumpFunSniper()
    await sniper.connect_and_listen()

if __name__ == "__main__":
    asyncio.run(main())
