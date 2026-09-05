import aiohttp
from datetime import datetime, timezone
import time
import config
from sentiment import analyze_sentiment
from ta_tools import TATools
import math

class Analyzer:
    async def fetch_latest_tokens(self) -> list:
        tokens = []
        async with aiohttp.ClientSession() as session:
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
        async with aiohttp.ClientSession() as session:
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
                print(f"Dexscreener token data error: {e}")
                return {}

    async def check_rugcheck(self, mint: str) -> bool:
        url = config.RUGCHECK_API.format(mint=mint)
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        score = data.get("score", 1000)
                        # Ужесточили проверку на скам для микрокапов
                        if score >= 300: # было 400, сделали строже
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
                print(f"RugCheck fetch error: {e}")
                return False

    async def analyze_token(self, mint: str) -> bool:
        pair_data = await self.fetch_token_data(mint)
        if not pair_data:
            return False
            
        symbol = pair_data.get("baseToken", {}).get("symbol", "UNKNOWN")
        liq = pair_data.get("liquidity", {}).get("usd", 0)
        vol_24h = pair_data.get("volume", {}).get("h24", 0)
        
        # 0. Заранее парсим транзакции для UI радара
        
        # --- 1. MOMENTUM SCORE (0-100) ---
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
        m5_change = pair_data.get("priceChange", {}).get("m5", 0)
        
        safe_sells = sells_m5 if sells_m5 > 0 else 1
        buy_sell_ratio = buys_m5 / safe_sells
        
        momentum_score = 20 # базовая оценка
        if buy_sell_ratio > 1.2: momentum_score += 30
        if buy_sell_ratio > 2.0: momentum_score += 20
        if buys_m5 > 50: momentum_score += 15
        if m5_change > 5: momentum_score += 15
        momentum_score = min(100, momentum_score)
        
        # --- 2. SAFETY SCORE (0-100) ---
        safety_score = 40 # базовая оценка
        if liq > 5000: safety_score += 20
        if liq > 20000: safety_score += 20
        if liq > 50000: safety_score += 20
        safety_score = min(100, safety_score)
        
        # --- 3. SOCIAL SCORE (0-100) ---
        social_score = 10 # базовая оценка
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        
        if len(socials) > 0: social_score += 40
        if len(websites) > 0: social_score += 30
        
        age_ms = datetime.now(timezone.utc).timestamp() * 1000 - pair_data.get("pairCreatedAt", datetime.now(timezone.utc).timestamp() * 1000)
        age_mins = age_ms / 60000
        if age_mins > 60: social_score += 20
        social_score = min(100, social_score)
        
        # --- COMPOSITE ALPHA SCORE ---
        alpha_score = int((safety_score * 0.35) + (momentum_score * 0.40) + (social_score * 0.25))
        
        self._save_scanned_token({
            "symbol": symbol,
            "mint": mint,
            "score": alpha_score,
            "safety": safety_score,
            "momentum": momentum_score,
            "social": social_score,
            "liquidity": liq,
            "vol_24h": vol_24h,
            "buys": buys_m5,
            "sells": sells_m5,
            "m5_change": m5_change,
            "time": time.time()
        })
            
        # Защита от FOMO (покупки отвесной вертикальной свечи)
        if m5_change > 70:
            print(f"🚫 Отказ (FOMO Защита): Монета улетела на +{m5_change}% за 5 минут.")
            return False
            
        h1_change = pair_data.get("priceChange", {}).get("h1", 0)
        if h1_change > 1000: # Повысили порог с 300 до 1000, чтобы ловить сильные ракеты, но отсекать совсем улетевшие
            print(f"🚫 Отказ (FOMO Защита): Монета уже сделала +{h1_change}% за час.")
            return False
            
        if not (config.MIN_LIQUIDITY <= liq <= config.MAX_LIQUIDITY):
            return False
        created_at = pair_data.get("pairCreatedAt")
        if not created_at:
            return False
            
        if not (config.MIN_AGE_MINUTES <= age_mins <= config.MAX_AGE_MINUTES):
            return False
            
        vol_1h = pair_data.get("volume", {}).get("h1", 0)
        max_vol = max(vol_24h, vol_1h)
        # Оборот (Turnover). Проверяем, чтобы монета была живой.
        if max_vol < liq * 0.3:
            return False
            
        if liq < 10000 or age_mins < 60:
            if buy_sell_ratio < 1.2:
                return False
            if buys_m5 < 10:
                return False

        if len(socials) + len(websites) < 1:
            print(f"🚫 Отказ: У {symbol} вообще нет соцсетей.")
            return False
            
        # 2. Базовая проверка безопасности кода и HHI Bubble Map
        if not await self.check_rugcheck(mint):
            print(f"🚫 Отказ: {symbol} не прошел RugCheck (скам/пузыри кошельков).")
            return False
            
        pair_address = pair_data.get("pairAddress")
        
        # 3. Анализ сентимента (Инфополе) с таймаутом
        try:
            import asyncio
            print(f"🔎 Сканируем инфополе (Twitter/Web) для {symbol}...")
            # Ставим жесткий таймаут 3 секунды, чтобы не тормозить снайпера
            sentiment = await asyncio.wait_for(analyze_sentiment(mint, symbol), timeout=3.0)
            if sentiment.get('decision') == "bearish":
                print(f"🚫 Отказ: Найдены предупреждения о скаме в интернете (FUD/Rugpull).")
                return False
        except asyncio.TimeoutError:
            print("⚠️ Таймаут сканирования инфополя. Пропускаем сентимент.")
        except Exception:
            pass
            
        # 4. Расширенный технический анализ (TA)
        if pair_address:
            print(f"📈 Загружаем свечи (OHLCV) для TA...")
            ohlcv = await TATools.fetch_ohlcv(pair_address, limit=40)
            
            if ohlcv and len(ohlcv) >= 20:
                rsi = TATools.calculate_rsi(ohlcv, periods=14)
                macd_data = TATools.calculate_macd(ohlcv)
                bb_data = TATools.calculate_bollinger_bands(ohlcv)
                
                current_price = float(pair_data.get("priceUsd", 0))
                
                print(f"📊 TA: RSI={rsi:.1f} | MACD Hist={macd_data['hist']:.6f}")
                
                if not math.isnan(rsi):
                    if rsi > 85:
                        print(f"🚫 Отказ: Монета экстремально перегрета (RSI {rsi:.2f} > 85).")
                        return False
                    if rsi < 30:
                        print(f"🚫 Отказ: Монета в жестком даунтренде (RSI {rsi:.2f} < 30).")
                        return False
                
                # Фильтр по Боллинджеру: не покупаем, если цена сильно пробила верхнюю полосу (откат неизбежен)
                if bb_data['upper'] > 0 and current_price > (bb_data['upper'] * 1.05):
                    print(f"🚫 Отказ: Цена пробила верхнюю полосу Боллинджера. Ожидается коррекция.")
                    return False
                    
                # Фильтр по MACD: ищем зарождающийся бычий тренд
                if macd_data['hist'] < 0 and macd_data['macd'] < macd_data['signal']:
                    # Тренд направлен вниз, но если MACD гистограмма начала расти (сужаться), это нормально.
                    # Для надежности требуем, чтобы RSI был не ниже 40.
                    if not math.isnan(rsi) and rsi < 40:
                        print(f"🚫 Отказ: Медвежий тренд по MACD. Покупать рано.")
                        return False
            else:
                print("⚠️ Свечи недоступны. Пропускаем фильтр TA (RSI/MACD/BB).")
                
            print(f"📊 Анализ транзакций (5м): Покупок {buys_m5}, Продаж {sells_m5} | Коэффициент: {buy_sell_ratio:.2f}")
            print(f"🧠 Alpha Agent Score: {alpha_score}/100 [Momentum: {momentum_score}, Safety: {safety_score}]")
            
            # ЛОГИКА ОЖИДАНИЯ ВЫСТРЕЛА (ФЛЭТ) ПО ЗАПРОСУ
            h1_change = pair_data.get("priceChange", {}).get("h1", 0)
            h6_change = pair_data.get("priceChange", {}).get("h6", 0)
            h24_change = pair_data.get("priceChange", {}).get("h24", 0)
            
            # 1. Анализ глобального графика (вместо жесткого среза по возрасту).
            # Защита от покупки "на дне после дампа".
            is_global_dump = (age_mins > 360 and h6_change < -30) or (age_mins > 1440 and h24_change < -40)
            is_bleeding = h1_change < -15 or is_global_dump
            
            # 2. Флэт или Моментум (Тренды)
            is_flat = abs(m5_change) < 15
            is_momentum = m5_change >= 15 and buy_sell_ratio >= 1.0 # Ловим и ракеты, которые уже начали рост!
            has_life = buys_m5 >= 1
            
            # 3. Возраст. Оцениваем по графику, а не по таймеру.
            is_young = age_mins < 10080 
            
            is_safe = safety_score >= 40 and len(socials) > 0 # Не скамится, есть минимальная ликвидность и соцсети
            
            if (is_flat or is_momentum) and is_young and is_safe and has_life and not is_bleeding:
                print(f"🚀 СИГНАЛ (КАНДИДАТ)! Монета прошла базовые фильтры. Передаем ИИ...")
                
                # Собираем контекст для ИИ
                token_context = {
                    "symbol": symbol,
                    "age_minutes": round(age_mins, 1),
                    "liquidity_usd": liq,
                    "volume_24h_usd": vol_24h,
                    "m5_buys": buys_m5,
                    "m5_sells": sells_m5,
                    "price_change_m5_pct": m5_change,
                    "price_change_h1_pct": h1_change,
                    "social_networks_count": len(socials) + len(websites),
                    "rsi_14": rsi if 'rsi' in locals() and not math.isnan(rsi) else None,
                    "macd_histogram": macd_data['hist'] if 'macd_data' in locals() else None,
                    "safety_score": safety_score
                }
                
                from ai_brain import ask_ai_oracle
                ai_decision = await ask_ai_oracle(token_context)
                
                print(f"🤖 ВЕРДИКТ ИИ: {ai_decision.get('decision')} (Уверенность: {ai_decision.get('confidence')}%) | Причина: {ai_decision.get('reason')}")
                
                if ai_decision.get("decision") == "BUY":
                    return True
                else:
                    return False
                
            if buy_sell_ratio < 1.0:
                print(f"🚫 Отказ: Слабый Momentum (Ratio {buy_sell_ratio:.2f} < 1.0). Тренд падающий.")
                return False
            
            # Отключаем покупку "активных ракет" по моментуму, чтобы не покупать на взлете!
            print(f"🚫 Отказ: Монета не во флэте. Мы ищем только засады до пампа. Пропускаем.")
            return False
        else:
            return False

    def _save_scanned_token(self, token_data):
        try:
            import json, os
            filename = "scanned_tokens.json"
            tokens = []
            if os.path.exists(filename):
                try:
                    with open(filename, 'r') as f:
                        tokens = json.load(f)
                except json.JSONDecodeError:
                    pass
            # Оставляем только последние 20 токенов
            tokens = [t for t in tokens if t['mint'] != token_data['mint']]
            tokens.insert(0, token_data)
            tokens = tokens[:20]
            with open(filename, 'w') as f:
                json.dump(tokens, f)
        except Exception as e:
            pass

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
        sol_price = 150.0 
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
        async with aiohttp.ClientSession() as session:
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
