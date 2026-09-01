import aiohttp
from datetime import datetime, timezone
import config
from sentiment import analyze_sentiment
from ta_tools import TATools
import math

class Analyzer:
    async def fetch_latest_tokens(self) -> list:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(config.DEXSCREENER_LATEST, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    return []
            except Exception as e:
                print(f"Dexscreener fetch error: {e}")
                return []
                
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
            
        liq = pair_data.get("liquidity", {}).get("usd", 0)
        if not (config.MIN_LIQUIDITY <= liq <= config.MAX_LIQUIDITY):
            return False
            
        created_at = pair_data.get("pairCreatedAt")
        if not created_at:
            return False
        age_ms = datetime.now(timezone.utc).timestamp() * 1000 - created_at
        age_mins = age_ms / 60000
        if not (config.MIN_AGE_MINUTES <= age_mins <= config.MAX_AGE_MINUTES):
            return False
            
        vol_24h = pair_data.get("volume", {}).get("h24", 0)
        vol_1h = pair_data.get("volume", {}).get("h1", 0)
        max_vol = max(vol_24h, vol_1h)
        if max_vol < liq * 0.5:
            return False
            
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        if len(socials) + len(websites) < 2:
            return False
            
        # 1. Базовая проверка безопасности кода
        if not await self.check_rugcheck(mint):
            return False
            
        symbol = pair_data.get("baseToken", {}).get("symbol", "UNKNOWN")
        pair_address = pair_data.get("pairAddress")
        
        # 2. Поиск упоминаний в Twitter/Reddit
        print(f"🔎 Сканируем инфополе для {symbol} ({mint}) (Возраст: {age_mins:.1f} мин)...")
        sentiment = await analyze_sentiment(mint, symbol)
        
        print(f"🗣 Настроение толпы: {sentiment['decision'].upper()} (Позитив: {sentiment['positive']} | Негатив: {sentiment['negative']} | Прочитано постов: {sentiment['texts']})")
        
        # Разделяем логику в зависимости от возраста монеты
        if sentiment['decision'] == "bearish":
            print(f"🚫 Отказ: Найдены предупреждения о скаме (Rug / Dump).")
            return False
            
        # Подключаем математику (TA), так как уже есть история торгов
        if pair_address:
            print(f"📈 Монета достаточно взрослая. Загружаем свечи (OHLCV) и считаем RSI для {symbol}...")
            ohlcv = await TATools.fetch_ohlcv(pair_address, limit=20)
            
            if ohlcv:
                rsi = TATools.calculate_rsi(ohlcv, periods=14)
                print(f"📊 Технический анализ: Индикатор RSI = {rsi:.2f}")
                
                if math.isnan(rsi):
                    print("⚠️ Недостаточно данных для ТА. Отказ.")
                    return False
                    
                # Стратегия: Покупаем только если есть живой интерес (RSI 45-70)
                # Если < 45, значит монета медленно умирает (падающий нож)
                # Если > 70, значит мы запрыгиваем на самых хаях
                if 45 <= rsi <= 70:
                    print(f"🟢 Сигнал: Здоровый растущий тренд (RSI {rsi:.2f}). Входим!")
                    return True
                else:
                    print(f"🚫 Отказ: Неподходящий RSI ({rsi:.2f}). Ищем тренд 45-70.")
                    return False
            else:
                print("⚠️ Не удалось получить минутные свечи. Отказ.")
                return False
        else:
            return False
