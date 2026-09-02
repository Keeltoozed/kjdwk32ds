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
            if addr and addr not in seen:
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
                        if score >= 400:
                            return False
                            
                        token_info = data.get("token", {})
                        if token_info.get("mintAuthority") is not None:
                            return False
                        if token_info.get("freezeAuthority") is not None:
                            return False
                            
                        top_holders = data.get("topHolders", [])
                        top_10_pct = sum([h.get("pct", 0) for h in top_holders[:10] if not h.get("isContract", False)])
                        if top_10_pct >= 30:
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
        
        # 0. Заранее парсим транзакции для UI радара (чтобы он был живым и показывал всё)
        # 0. Имитация алгоритма GMGNAI (Alpha Agent Composite Score)
        # Weighted: safety (35%), momentum (40%), social (25%)
        
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
        # Если монета старая, комьюнити крепче
        age_ms = datetime.now(timezone.utc).timestamp() * 1000 - pair_data.get("pairCreatedAt", datetime.now(timezone.utc).timestamp() * 1000)
        age_mins = age_ms / 60000
        if age_mins > 60: social_score += 20
        social_score = min(100, social_score)
        
        # --- COMPOSITE ALPHA SCORE ---
        alpha_score = int((safety_score * 0.35) + (momentum_score * 0.40) + (social_score * 0.25))
        
        # Сохраняем в UI ВСЕ найденные токены с детальной разбивкой (как в GMGNAI)
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
            
        # 1. Базовые фильтры для ПОКУПКИ
        # Защита от FOMO (покупки отвесной вертикальной свечи)
        if m5_change > 100:
            print(f"🚫 Отказ: Монета сделала х2 (+{m5_change}%) за 5 минут. Это уже казино, пропускаем.")
            return False
            
        if not (config.MIN_LIQUIDITY <= liq <= config.MAX_LIQUIDITY):
            return False
            
        created_at = pair_data.get("pairCreatedAt")
        if not created_at:
            return False
        age_ms = datetime.now(timezone.utc).timestamp() * 1000 - created_at
        age_mins = age_ms / 60000
        if not (config.MIN_AGE_MINUTES <= age_mins <= config.MAX_AGE_MINUTES):
            return False
            
        vol_1h = pair_data.get("volume", {}).get("h1", 0)
        max_vol = max(vol_24h, vol_1h)
        # Требуем, чтобы объем был как минимум равен ликвидности (отсев дохлых монет)
        if max_vol < liq * 1.0:
            return False
            
        # СПЕЦИАЛЬНАЯ ЗАЩИТА ДЛЯ НОВЫХ И МАЛЕНЬКИХ МОНЕТ (Microcaps < 10k или младше часа)
        if liq < 10000 or age_mins < 60:
            if buy_sell_ratio < 1.4:
                print(f"🚫 Отказ (Microcap): Для молодых/мелких монет нужен мощный перевес покупок (Ratio {buy_sell_ratio:.2f} < 1.4)")
                return False
            if buys_m5 < 20:
                print(f"🚫 Отказ (Microcap): Слишком мало покупок за 5 минут ({buys_m5} < 20). Скорее всего скам без активности.")
                return False

        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        
        if len(socials) + len(websites) < 1:
            print(f"🚫 Отказ: У {symbol} вообще нет соцсетей (полный мусор).")
            return False
            
        # 2. Базовая проверка безопасности кода
        if not await self.check_rugcheck(mint):
            print(f"🚫 Отказ: {symbol} не прошел RugCheck (скам/монополия).")
            return False
            
        pair_address = pair_data.get("pairAddress")
        
        # 3. Поиск упоминаний в Twitter/Reddit
        print(f"🔎 Сканируем инфополе для {symbol} ({mint}) (Возраст: {age_mins:.1f} мин)...")
        sentiment = await analyze_sentiment(mint, symbol)
        
        print(f"🗣 Настроение толпы: {sentiment['decision'].upper()} (Позитив: {sentiment['positive']} | Негатив: {sentiment['negative']} | Прочитано постов: {sentiment['texts']})")
        
        if sentiment['decision'] == "bearish":
            print(f"🚫 Отказ: Найдены предупреждения о скаме (Rug / Dump).")
            return False
            
        # 4. Подключаем математику (TA)
        if pair_address:
            print(f"📈 Загружаем свечи (OHLCV) и считаем RSI для {symbol}...")
            ohlcv = await TATools.fetch_ohlcv(pair_address, limit=20)
            
            if ohlcv and len(ohlcv) >= 6:
                rsi = TATools.calculate_rsi(ohlcv, periods=14)
                print(f"📊 Технический анализ: Индикатор RSI = {rsi:.2f}")
                
                if math.isnan(rsi):
                    print("⚠️ Недостаточно данных для ТА. Отказ.")
                    return False
                    
                # Защита от покупки на самом пике ("на хаях") или на жестком дампе
                if rsi > 85:
                    print(f"🚫 Отказ: Монета экстремально перегрета (RSI {rsi:.2f} > 85). Ждем откат.")
                    return False
                if rsi < 45:
                    print(f"🚫 Отказ: Монета в даунтренде (RSI {rsi:.2f} < 45). Слишком рано.")
                    return False
                    
                print(f"📊 Анализ транзакций (5м): Покупок {buys_m5}, Продаж {sells_m5} | Коэффициент: {buy_sell_ratio:.2f}")
                print(f"🧠 Alpha Agent Score: {alpha_score}/100 [Momentum: {momentum_score}, Safety: {safety_score}]")
                
                if buy_sell_ratio < 1.1:
                    print(f"🚫 Отказ: Слабый Momentum (Ratio {buy_sell_ratio:.2f} < 1.1). Тренд затухает.")
                    return False
                
                if alpha_score >= 60:
                    print(f"🚀 СИГНАЛ (Score {alpha_score})! Заходим в перспективную ракету!")
                    return True
                else:
                    print(f"🚫 Отказ: Alpha Score ({alpha_score}) ниже 60. Ждем более уверенный тренд.")
                    return False
            else:
                print("⚠️ Не удалось получить минутные свечи (или их меньше 6). Отказ.")
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
