import asyncio
import aiohttp
import config
from analyzer import Analyzer

async def fetch_birdeye_trending():
    """Получает трендовые токены Solana через Birdeye API"""
    if not hasattr(config, 'BIRDEYE_API_KEY') or not config.BIRDEYE_API_KEY:
        print("⚠️ Birdeye API ключ не настроен в config.py!")
        await asyncio.sleep(60)
        return []
        
    tokens = []
    # API эндпоинт Birdeye для получения трендов (сортировка по объему/популярности)
    url = "https://public-api.birdeye.so/defi/token_trending?sort_by=rank&sort_type=asc&offset=0&limit=20"
    
    headers = {
        "X-API-KEY": config.BIRDEYE_API_KEY,
        "x-chain": "solana"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("data", {}).get("tokens", [])
                    for item in items:
                        mint = item.get("address")
                        if mint and mint not in tokens:
                            tokens.append(mint)
                else:
                    print(f"Ошибка Birdeye API: {response.status}")
        except Exception as e:
            print(f"Ошибка подключения к Birdeye: {e}")
            
    return tokens

async def birdeye_loop(analyzer: Analyzer, tracker):
    """Цикл сканирования глобальных трендов через Birdeye"""
    if not hasattr(config, 'BIRDEYE_API_KEY') or not config.BIRDEYE_API_KEY:
        return
        
    print("🦅 Birdeye Scanner запущен: отслеживаем глобальные тренды Solana!")
    processed_mints = set()
    
    while True:
        try:
            if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                await asyncio.sleep(10)
                continue
                
            trending_mints = await fetch_birdeye_trending()
            
            for mint in trending_mints:
                if mint in processed_mints:
                    continue
                
                processed_mints.add(mint)
                if len(tracker.get_open_positions()) >= config.MAX_CONCURRENT_POSITIONS:
                    break
                    
                print(f"👀 Найдена Birdeye-ракета: {mint}. Анализируем...")
                
                is_buy = await analyzer.analyze_token(mint)
                if is_buy:
                    from jupiter import JupiterAPI
                    import time
                    price = await JupiterAPI.get_price(mint)
                    if price > 0:
                        position_size = config.VIRTUAL_POSITION_SIZE_USD
                        tracker.add_position(
                            mint=mint,
                            symbol=mint[:4],
                            entry_price=price,
                            amount_usd=position_size,
                            ml_confidence=90.0,
                            features={"source": "Birdeye Trending"},
                            is_mature=True
                        )
                        print(f"✅ Успешный ВХОД (Birdeye) в {mint} по цене ${price:.6f}")
                        break
                        
        except Exception as e:
            print(f"Ошибка в birdeye_loop: {e}")
            
        await asyncio.sleep(30) # Опрашиваем раз в 30 секунд
