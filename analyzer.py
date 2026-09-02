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
                        if score >= 500:
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
        txns_m5 = pair_data.get("txns", {}).get("m5", {})
        buys_m5 = txns_m5.get("buys", 0)
        sells_m5 = txns_m5.get("sells", 0)
        
        safe_sells = sells_m5 if sells_m5 > 0 else 1
        buy_sell_ratio = buys_m5 / safe_sells
        
        momentum_score = min(40, int((buy_sell_ratio - 1) * 20))
        safety_score = min(35, int((liq / 10000) * 5)) 
        alpha_score = min(100, max(0, 50 + momentum_score + safety_score))
        
        # Сохраняем в UI ВООБЩЕ ВСЕ найденные токены, чтобы радар безостановочно мерцал новыми щитками!
        self._save_scanned_token({
            "symbol": symbol,
            "mint": mint,
            "score": alpha_score,
            "liquidity": liq,
            "vol_24h": vol_24h,
            "buys": buys_m5,
            "sells": sells_m5,
            "time": time.time()
        })
            
        # 1. Базовые фильтры для ПОКУПКИ
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
        if max_vol < liq * 0.5:
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
