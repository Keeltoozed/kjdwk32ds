import aiohttp
import asyncio
import time

# Глобальный кеш цены SOL
_sol_price = 150.0
_last_update = 0.0
_UPDATE_INTERVAL = 300  # Обновляем каждые 5 минут

async def get_sol_price() -> float:
    """Получает актуальную цену SOL из Jupiter API с кешированием на 5 минут"""
    global _sol_price, _last_update
    
    now = time.time()
    if now - _last_update < _UPDATE_INTERVAL:
        return _sol_price
    
    try:
        async with aiohttp.ClientSession() as session:

            # Используем GeckoTerminal вместо закрытого Jupiter API v2
            url = "https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/So11111111111111111111111111111111111111112"
            headers = {"Accept": "application/json"}
            async with session.get(url, headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    prices = data.get("data", {}).get("attributes", {}).get("token_prices", {})
                    price = float(prices.get("So11111111111111111111111111111111111111112", 0))
                    if price > 0:
                        _sol_price = price
                        _last_update = now
                        print(f"💰 Цена SOL обновлена: ${_sol_price:.2f}")
    except Exception as e:
        print(f"⚠️ Ошибка получения цены SOL: {e}. Используем кеш: ${_sol_price:.2f}")
    
    return _sol_price

def get_sol_price_sync() -> float:
    """Синхронная версия — возвращает последнюю кешированную цену"""
    return _sol_price
