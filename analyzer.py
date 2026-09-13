import aiohttp
from datetime import datetime, timezone
import time
import config
from sentiment import analyze_sentiment
from ta_tools import TATools
import math

class Analyzer:
    def __init__(self):
        self.session = None
        
    async def get_session(self):
        import aiohttp
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def fetch_latest_tokens(self) -> list:
        tokens = []
        session = await self.get_session()
        if True:
            # 1. Сканируем топовые (Boosted) монеты
            try:
                async with session.get(config.DEXSCREENER_LATEST, timeout=5) as response:
                    if response.status == 200:
                        tokens.extend(await response.json())
            except Exception as e:
                print(f"Dexscreener boosts fetch error: {e}")
                
            # 2. Сканируем новые профили, чтобы не пропускать свежие ракеты
            try:
                async with session.get(config.DEXSCREENER_PROFILES, timeout=5) as response:
                    if response.status == 200:
                        tokens.extend(await response.json())
            except Exception as e:
                print(f"Dexscreener profiles fetch error: {e}")
                
        # Возвращаем уникальные токены (по tokenAddress)
        seen = set()
        unique_tokens = []
        for t in tokens:
            addr = t.get("tokenAddress")
            chain = t.get("chainId")
            if addr and addr not in seen and chain == "solana":
                seen.add(addr)
                unique_tokens.append(t)
        return unique_tokens
                
    async def fetch_token_data(self, mint: str) -> dict:
        url = f"{config.DEXSCREENER_SEARCH}{mint}"
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                            if sol_pairs:
                                return sorted(sol_pairs, key=lambda x: x.get("liquidity", {}).get("usd", 0), reverse=True)[0]
                    return {}
            except Exception as e:
                print(f"Dexscreener token data error: {type(e).__name__} {e}")
                return {}

    async def is_clone(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float) -> bool:
        """Проверяет, является ли этот токен дешевой копией (клоном) более старого или крупного оригинала."""
        if not symbol or len(symbol) <= 2:
            return False 
            
        url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = data.get("pairs", [])
                        
                        for p in pairs:
                            if p.get("chainId") == "solana":
                                p_symbol = p.get("baseToken", {}).get("symbol", "").upper()
                                p_mint = p.get("baseToken", {}).get("address", "")
                                
                                if p_symbol == symbol.upper() and p_mint != current_mint:
                                    p_created_at = p.get("pairCreatedAt", float('inf'))
                                    p_fdv = p.get("fdv", 0)
                                    
                                    # Если мы нашли другой токен с таким же именем, который был создан РАНЬШЕ нас
                                    # и имеет какую-то капитализацию (не мертвый с 0 fdv), то наш токен - фейк.
                                    if p_created_at < current_created_at and p_fdv > 5000:
                                        return True
                                        
                                    # Либо если другой токен имеет огромную капу (в 10 раз больше нашей),
                                    # значит он - оригинал, а мы клон.
                                    if p_fdv > (current_fdv * 10) and p_fdv > 50000:
                                        return True
            except Exception as e:
                pass
        return False

    async def check_rugcheck(self, mint: str) -> bool:
        url = config.RUGCHECK_API.format(mint=mint)
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        score = data.get("score", 1000)
                        # Защита от моментальных дампов (-37%). Строгий фильтр скама.
                        if score >= 150: # было 300, сделали 150 (очень строго). Отсекает монеты, где у одного кошелька >20% саплая.
                            return False
                            
                        token_info = data.get("token", {})
                        if token_info.get("mintAuthority") is not None:
                            return False
                        if token_info.get("freezeAuthority") is not None:
                            return False
                        
                        # Фильтр по названию токена
                        name = token_info.get("name", "").lower()
                        symbol = token_info.get("symbol", "").lower()
                        bad_words = ["test", "scam", "fuck", "nigger", "pump and dump", "rug"]
                        if any(w in name for w in bad_words) or any(w in symbol for w in bad_words):
                            return False
                            
                        # Индекс Херфиндаля-Хиршмана (HHI) для выявления скрытых монополий (как Bubble Map)
                        top_holders = data.get("topHolders", [])
                        hhi_index = sum([(h.get("pct", 0) * 100) ** 2 for h in top_holders[:15] if not h.get("isContract", False)])
                        
                        top_10_pct = sum([h.get("pct", 0) for h in top_holders[:10] if not h.get("isContract", False)])
                        
                        # Если HHI высокий (>2000), значит кошельки сильно сконцентрированы (пузыри)
                        if top_10_pct >= 30 or hhi_index > 2500:
                            return False
                            
                        return True
                    return False
            except Exception as e:
                print(f"RugCheck fetch error: {type(e).__name__} {e}")
                return False

    async def get_helius_transaction_metrics(self, mint: str) -> tuple:
        """ Возвращает (unique_buyers_m5, smart_money_inflow) """
        unique_buyers = 0
        smart_money_inflow = 0
        
        # Читаем smart wallets
        smart_wallets = set()
        import os
        if os.path.exists("smart_wallets.txt"):
            with open("smart_wallets.txt", "r") as f:
                smart_wallets = {line.strip() for line in f if line.strip()}
                
        payload_sigs = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": 30}]
        }
        
        import aiohttp
        session = await self.get_session()
        if True:
            try:
                # 1. Получаем сигнатуры
                async with session.post(config.HELIUS_RPC_URL, json=payload_sigs, timeout=3) as resp:
                    data = await resp.json()
                    signatures = [item["signature"] for item in data.get("result", [])]
                
                if not signatures:
                    return 0, 0
                    
                # 2. Получаем детали транзакций
                payload_txs = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransactions",
                    "params": [signatures, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }
                async with session.post(config.HELIUS_RPC_URL, json=payload_txs, timeout=5) as resp:
                    tx_data = await resp.json()
                    transactions = tx_data.get("result", [])
                    
                    buyers = set()
                    for tx in transactions:
                        if not tx or not tx.get("transaction"):
                            continue
                            
                        account_keys = tx["transaction"]["message"]["accountKeys"]
                        for acc in account_keys:
                            if acc.get("signer"):
                                pubkey = acc.get("pubkey")
                                buyers.add(pubkey)
                                if pubkey in smart_wallets:
                                    smart_money_inflow += 1
                                break 
                                
                    unique_buyers = len(buyers)
                    
            except Exception as e:
                print(f"⚠️ Ошибка Helius RPC при парсинге транзакций: {e}")
                
        return unique_buyers, smart_money_inflow

    async def analyze_token(self, mint: str) -> bool:
        # Smart Router
        
        # 1. Сначала жесткий фильтр скама (RugCheck). Если это скам - даже не тратим лимиты.
        is_safe = await self.check_rugcheck(mint)
        if not is_safe:
            print(f"🚫 Скам-фильтр: {mint} не прошел проверку RugCheck (Риск дампа/MintAuthority).")
            return False
            
        pair_data = await self.fetch_token_data(mint)
        if not pair_data:
            print(f"⚠️ Пропуск: DexScreener не вернул данные для {mint} (Rate Limit или токен слишком новый).")
            return None
            
        # Блэклист тикеров и названий (Защита от фейковых токенов)
        base_token = pair_data.get("baseToken", {})
        name = base_token.get("name", "").upper()
        symbol = base_token.get("symbol", "").upper()
        
        scam_keywords = ["AAPL", "S&P", "SP500", "MSFT", "TSLA", "NVDA", "GOOG", "AMZN", "META", "NFLX", 
                         "PEPE", "SHIB", "DOGE", "FLOKI", "BONK", "WIF", "BOME", "POPCAT", "TRUMP", "BIDEN"]
                         
        if any(keyword in symbol for keyword in scam_keywords) or any(keyword in name for keyword in scam_keywords):
            print(f"🚫 Мусор: Токен {symbol} мимикрирует под известный бренд/мем. Это 100% scam.")
            return False
            
        # Защита от микро-пулов (Scam сетки типа Fly)
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        if liquidity < 15000:
            print(f"📉 Изоляция: {symbol} имеет микро-пул (${liquidity:.0f} < $15k). Риск 100% проскальзывания.")
            return False
            
        # 1.5 Защита от вторичных клонов (Copycat Filter)
        current_created_at = pair_data.get("pairCreatedAt", 0)
        current_fdv = pair_data.get("fdv", 0)
        if await self.is_clone(symbol, mint, current_created_at, current_fdv):
            print(f"🚫 Мусор: Токен {symbol} является клоном! На DexScreener найден более старый/крупный оригинал.")
            return False
            
        # 2. Обязательное наличие соцсетей (Proof of Effort: Twitter + Website/TG)
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        
        # Смягченный фильтр: достаточно хотя бы одной соцсети или сайта
        if not (has_twitter or has_tg or has_website):
            print(f"🚫 Мусор: У {mint} вообще нет ни одной соцсети или сайта.")
            return False
            
        dex_id = pair_data.get("dexId")
        import time
        created_at = pair_data.get("pairCreatedAt", 0)
        age_minutes = (time.time() * 1000 - created_at) / (1000 * 60) if created_at else 999
        
        if dex_id == "pump" and age_minutes <= 15:
            return await self.analyze_token_xgboost(mint, pair_data)
        else:
            return await self.analyze_token_raydium(mint, pair_data)
        
    async def analyze_token_xgboost(self, mint: str, pair_data: dict) -> bool:
        if not pair_data:
            return False
            
        # 1. Проверяем, что токен все еще на Pump.fun (не ушел на Raydium)
        if pair_data.get("dexId") != "pump":
            return False
            
        # 2. Проверяем возраст токена (XGBoost обучен на свежих монетах)
        created_at = pair_data.get("pairCreatedAt")
        if created_at:
            import time
            age_minutes = (time.time() * 1000 - created_at) / (1000 * 60)
            if age_minutes > 15:  # Игнорируем токены старше 15 минут
                return False
                
        import pandas as pd
        import joblib
        from pump_fun_sniper import PumpFunSniper
        
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
            
        tx_velocity_1m = (buys_m5 + sells_m5) / 5.0
        
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        has_twitter = any("twitter" in s.get("type", "").lower() or "x.com" in s.get("url", "").lower() for s in socials)
        has_tg = any("telegram" in s.get("type", "").lower() or "t.me" in s.get("url", "").lower() for s in socials)
        has_website = len(websites) > 0
        has_socials = 1 if (has_twitter and (has_website or has_tg)) else 0
        
        # Get holders via Helius RPC
        dev_holding_pct, top_10_holding_pct = 0.0, 0.0
        rpc_url = "https://mainnet.helius-rpc.com/?api-key=9efda6f4-fddb-42d3-a2b1-098bbbecd299"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint]
        }
        try:
            import aiohttp
            session = await self.get_session()
            if True:
                async with session.post(rpc_url, json=payload, timeout=5) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    total_supply = 1_000_000_000
                    if accounts:
                        # Exclude bonding curve account which holds ~80% initially
                        # We just sum the remaining top 9 accounts
                        non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]
                        top_10_amounts = non_curve_accounts[:10]
                        top_10_holding_pct = (sum(top_10_amounts) / total_supply) * 100
                        if top_10_amounts:
                            dev_holding_pct = (top_10_amounts[0] / total_supply) * 100 
                        
                        # Защита от Jito-бандлов (Sybil-атаки):
                        # Скаммеры часто раскидывают одинаковые суммы по свежим кошелькам.
                        # Если 3 и более кошельков в топе имеют одинаковый баланс (с погрешностью) - это бандл.
                        if len(top_10_amounts) >= 3:
                            rounded_amounts = [round(amt, -4) for amt in top_10_amounts if amt > 1000000]
                            if rounded_amounts:
                                # Ищем самый частый баланс
                                from collections import Counter
                                counts = Counter(rounded_amounts)
                                if counts.most_common(1)[0][1] >= 3:
                                    print(f"🚫 Мусор: Обнаружен Jito-бандл (Sybil attack) у {mint}.")
                                    return False
        except Exception as e:
            print(f"Helius RPC error: {e}")
        
        # Funded from CEX (simplified)
        funded_from_cex = 0
        
        features = pd.DataFrame([{
            "dev_holding_pct": dev_holding_pct,
            "top_10_holding_pct": top_10_holding_pct,
            "tx_velocity_1m": tx_velocity_1m,
            "has_socials": has_socials,
            "funded_from_cex": funded_from_cex
        }])
        
        import xgboost as xgb
        model = xgb.XGBClassifier()
        model.load_model("pump_model.json")
        prob = model.predict_proba(features)[0][1]
        conf = prob * 100
        print(f"🤖 XGBoost [DEX Poller]: {mint} | Score: {conf:.1f}%")
        import config; threshold = 75.0 if getattr(config, "AI_MODE", "sniper") == "sniper" else 65.0; return conf >= threshold

    async def analyze_token_raydium(self, mint: str, pair_data: dict) -> bool:
        # Безлимитный режим: используем ТОЛЬКО данные DexScreener
        if not pair_data:
            return False
            
        import pandas as pd
        import joblib
        
        # Извлекаем признаки
        txns_h24 = pair_data.get("txns", {}).get("h24", {})
        buys_h24 = txns_h24.get("buys", 0)
        sells_h24 = txns_h24.get("sells", 0)
        
        volume_h24 = pair_data.get("volume", {}).get("h24", 0)
        price_change_h24 = pair_data.get("priceChange", {}).get("h24", 0)
        
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        fdv = pair_data.get("fdv", 0)
        
        buy_sell_ratio = buys_h24 / (sells_h24 + 1)
        vol_to_liq = volume_h24 / (liquidity + 1)
        
        # Формируем DataFrame для XGBoost
        features = ['price_change_h24', 'volume_h24', 'buys_h24', 'sells_h24', 'liquidity', 'fdv', 'buy_sell_ratio', 'vol_to_liq']
        df = pd.DataFrame([{
            'price_change_h24': price_change_h24,
            'volume_h24': volume_h24,
            'buys_h24': buys_h24,
            'sells_h24': sells_h24,
            'liquidity': liquidity,
            'fdv': fdv,
            'buy_sell_ratio': buy_sell_ratio,
            'vol_to_liq': vol_to_liq
        }])
        
        try:
            import xgboost as xgb
            model = xgb.XGBClassifier()
            model.load_model("raydium_model_dex.json")
            prob = model.predict_proba(df)[0][1]
            conf = prob * 100
            
            # --- ИНТЕГРАЦИЯ LUNARCRUSH ---
            symbol = pair_data.get("baseToken", {}).get("symbol", "")
            if symbol:
                lc_data = await self.fetch_lunarcrush_sentiment(symbol)
                if lc_data:
                    interactions = lc_data.get("interactions", 0)
                    sentiment = lc_data.get("sentiment", 50)
                    print(f"🌕 [LunarCrush] {symbol}: Interactions: {interactions}, Sentiment: {sentiment}%")
                    
                    if sentiment >= 70 and interactions > 500:
                        conf += 15.0 # Бустим уверенность ИИ за счет сильного социального хайпа!
                        print(f"📈 [LunarCrush] Хайп подтвержден! Буст +15% к Score.")
                    elif sentiment < 30:
                        conf -= 20.0
                        print(f"📉 [LunarCrush] Негативный сентимент! Штраф -20% к Score.")
            # -------------------------------
            
            print(f"🧠 Raydium XGBoost (Безлимит): {mint} | Score: {conf:.1f}%")
            import config; threshold = 75.0 if getattr(config, "AI_MODE", "sniper") == "sniper" else 65.0
            
            is_buy = conf >= threshold
            
            if not is_buy:
                try:
                    from shadow_tracker import ShadowTracker
                    shadow = ShadowTracker()
                    price = float(pair_data.get("priceUsd", 0))
                    # Пишем Raydium FOMO-монеты в ту же таблицу shadow_log для дальнейшего анализа
                    shadow.log_rejection(
                        mint=mint,
                        reason=f"FOMO XGBoost low score: {conf:.1f}%",
                        score=conf,
                        price=price,
                        features=df.iloc[0].to_dict()
                    )
                except Exception as e:
                    print(f"Ошибка записи в ShadowTracker (Raydium): {e}")
                    
            return is_buy
        except Exception as e:
            print(f"⚠️ Ошибка XGBoost (analyze_token_raydium) для {mint}: {e}")
            return False
    async def analyze_token_ws(self, ws_data: dict) -> bool:
        mint = ws_data.get("mint")
        symbol = ws_data.get("symbol", "UNKNOWN")
        name = ws_data.get("name", "Unknown")
        
        # Читаем соцсети прямо из смарт-контракта (создатель обязан их указать при деплое на Pump.fun)
        has_twitter = bool(ws_data.get("twitter"))
        has_telegram = bool(ws_data.get("telegram"))
        has_website = bool(ws_data.get("website"))
        socials_count = sum([has_twitter, has_telegram, has_website])
        
        # Расчет стартовой ликвидности из кривой Bonding Curve
        v_sol = ws_data.get("vSolInBondingCurve", 30.0)
        from sol_price import get_sol_price_sync
        sol_price = get_sol_price_sync()
        liq_usd = v_sol * sol_price
        
        initial_buy = ws_data.get("initialBuy", 0)
        
        safety_score = 40
        momentum_score = 40 if initial_buy > 0 else 20
        
        # Social Score теперь зависит от того, сколько ссылок создатель прикрепил к контракту
        social_score = 10
        if has_twitter: social_score += 30
        if has_telegram: social_score += 30
        if has_website: social_score += 30
        social_score = min(100, social_score)
        
        alpha_score = int((safety_score * 0.35) + (momentum_score * 0.40) + (social_score * 0.25))
        
        self._save_scanned_token({
            "symbol": symbol,
            "mint": mint,
            "score": alpha_score,
            "safety": safety_score,
            "momentum": momentum_score,
            "social": social_score,
            "liquidity": liq_usd,
            "vol_24h": 0,
            "buys": 1 if initial_buy > 0 else 0,
            "sells": 0,
            "m5_change": 0,
            "age_mins": "0m (WSS)",
            "time": time.time()
        })
        
        print(f"📡 [WSS SNIPER] Пойман токен: {name} (${symbol}) | Liq: ${liq_usd:.0f} | Socials: {socials_count}")
        
        # СТРОГИЕ ФИЛЬТРЫ ДЛЯ 0-СЕКУНДНЫХ МОНЕТ
        
        # 1. Защита от ленивых скаммеров (мусор без соцсетей)
        if socials_count == 0:
            print(f"🚫 [WSS] Отказ: Создатель {symbol} даже не прикрепил соцсети. 100% мусор.")
            return False
            
        # 2. Skin in the game & Анти-монополия (Initial Buy)
        # PumpPortal отдает initialBuy в SOL. Требуем от 0.1 до 5 SOL.
        if initial_buy < 0.1:
            print(f"🚫 [WSS] Отказ: Создатель вкинул слишком мало ({initial_buy} SOL). У него нет 'шкуры на кону'.")
            return False
        if initial_buy > 5.0:
            print(f"🚫 [WSS] Отказ: Создатель выкупил слишком много токенов ({initial_buy} SOL). Высокий риск монопольного дампа.")
            return False
            
        # 3. Проверка кода (RugCheck)
        if not await self.check_rugcheck(mint):
            print(f"🚫 [WSS] Отказ: {symbol} не прошел стартовый RugCheck.")
            return False
            
        # 4. Проверка кошелька разработчика (Helius RPC) и метаданных IPFS
        trader_pubkey = ws_data.get("traderPublicKey")
        uri = ws_data.get("uri")
        
        dev_balance_sol = 0
        description = ""
        
        import aiohttp
        session = await self.get_session()
        if True:
            # Запрос баланса к Helius
            if trader_pubkey:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [trader_pubkey, {"encoding": "jsonParsed"}]
                }
                try:
                    async with session.post(config.HELIUS_RPC_URL, json=payload, timeout=2) as resp:
                        data = await resp.json()
                        lamports = data.get("result", {}).get("value", {}).get("lamports", 0) if data.get("result", {}).get("value") else 0
                        dev_balance_sol = lamports / 1e9
                except Exception as e:
                    print(f"⚠️ Ошибка RPC баланса: {e}")
                    
            # Загрузка метаданных IPFS
            if uri:
                try:
                    async with session.get(uri, timeout=2) as resp:
                        meta = await resp.json()
                        description = meta.get("description", "").lower()
                except Exception:
                    pass
                    
        if dev_balance_sol < 0.1:
            print(f"🚫 [WSS] Отказ: Кошелек разработчика пуст ({dev_balance_sol:.2f} SOL). Скаммер-однодневка.")
            return False
            
        bad_words = ["test", "scam", "fuck", "shit", "nigger", "pump and dump", "rug"]
        if any(word in description for word in bad_words) or len(description) < 5:
            print(f"🚫 [WSS] Отказ: Мусорное описание на IPFS (спам/короткое).")
            return False
            
        print(f"🚀 [WSS СИГНАЛ] Входим в токен {symbol} на нулевой секунде! (Dev Wallet: {dev_balance_sol:.2f} SOL, Socials: {socials_count})")
        
        # Эмуляция цены (записываем цену в usd в словарь, чтобы main.py мог ее взять)
        v_tok = ws_data.get("vTokensInBondingCurve", 1073000000.0)
        ws_data["priceUsd"] = (v_sol / v_tok) * sol_price
        return True

    async def fetch_lunarcrush_sentiment(self, symbol: str) -> dict:
        """
        Проверяет хайп (Social Sentiment) монеты в Twitter через LunarCrush.
        """
        import config
        api_key = getattr(config, "LUNARCRUSH_API_KEY", "")
        if not api_key:
            return {}
            
        url = f"https://lunarcrush.com/api4/public/coins/{symbol}/v1"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, headers=headers, timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        coin_data = data.get("data", {})
                        
                        return {
                            "social_volume": coin_data.get("social_volume_24h", 0),
                            "interactions": coin_data.get("interactions_24h", 0),
                            "sentiment": coin_data.get("sentiment", 50)
                        }
            except Exception as e:
                pass
        return {}
