import asyncio
import aiohttp
import config
from analyzer import Analyzer

async def fetch_dexscreener_trending():
    """Получает топ трендовых и забущенных токенов с DexScreener"""
    tokens = []
    # Эндпоинты DexScreener для поиска самого горячего (FOMO)
    urls = [
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1"
    ]
    
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        for item in data:
                            # Извлекаем адрес токена на Solana
                            if item.get('chainId') == 'solana':
                                mint = item.get('tokenAddress')
                                if mint and mint not in tokens:
                                    tokens.append(mint)
            except Exception as e:
                print(f"Ошибка получения FOMO токенов: {e}")
    return tokens

async def fomo_loop(analyzer: Analyzer, tracker):
    """Цикл, который постоянно сканирует тренды (FOMO) и топ-токены"""
    print("🔥 FOMO Scanner запущен: отслеживаем ракеты и тренды DexScreener!")
    
    # Чтобы не спамить API
    processed_mints = set()
    
    while True:
        try:
            if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                await asyncio.sleep(10)
                continue
                
            trending_mints = await fetch_dexscreener_trending()
            
            for mint in trending_mints:
                if mint in processed_mints:
                    continue
                
                processed_mints.add(mint)
                
                # Если позиций уже максимум, прерываем проверку
                if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    break
                    
                print(f"👀 Найдена FOMO-монета: {mint}. Анализируем...")
                
                # Запускаем полный анализ через наш ИИ
                is_buy = await analyzer.analyze_token(mint)
                if is_buy:
                    # Получаем РЕАЛЬНОЕ имя и цену монеты перед "покупкой"
                    pair_data = await analyzer.fetch_token_data(mint)
                    if pair_data:
                        actual_price = float(pair_data.get("priceUsd", 0))
                        actual_symbol = pair_data.get("baseToken", {}).get("symbol", "FOMO")
                        
                        if actual_price > 0:
                            capital = tracker.get_total_capital()
                            position_size = max(4.0, min(100.0, capital * (config.REINVEST_PERCENT / 100.0)))
                            print(f"🚀 СНАЙП FOMO-РАКЕТЫ {actual_symbol} ({mint})! Входим на {position_size}$ по цене {actual_price}$")
                            
                            # Добавляем реальную сделку в трекер
                            tracker.add_position(actual_symbol, mint, actual_price, position_size)
                    
            # Держим память в чистоте
            if len(processed_mints) > 1000:
                processed_mints.clear()
                
        except Exception as e:
            print(f"Ошибка в FOMO Loop: {e}")
            
        await asyncio.sleep(60) # Проверяем тренды каждую минуту
