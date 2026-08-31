import pandas as pd
import aiohttp

class TATools:
    @staticmethod
    async def fetch_ohlcv(pool_address: str, limit: int = 30) -> list:
        """
        Запрашивает исторические минутные свечи (OHLCV) через GeckoTerminal API.
        Это решает задачу получения глубоких исторических данных.
        """
        url = f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute?limit={limit}"
        async with aiohttp.ClientSession() as session:
            try:
                # API часто требует User-Agent
                headers = {'User-Agent': 'Mozilla/5.0'}
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Формат: [timestamp, open, high, low, close, volume]
                        # GeckoTerminal возвращает новые свечи первыми
                        return data.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
                    else:
                        print(f"GeckoTerminal API Error: {response.status}")
                        return []
            except Exception as e:
                print(f"Error fetching OHLCV: {e}")
                return []

    @staticmethod
    def calculate_rsi(ohlcv_list: list, periods: int = 14) -> float:
        """
        Использует Pandas для локального расчета индикатора RSI (Индекс относительной силы).
        """
        if len(ohlcv_list) < periods + 1:
            return 50.0 # Недостаточно данных, возвращаем нейтральный RSI
            
        # Переворачиваем список, чтобы старые данные были в начале (для правильного расчета Pandas)
        ohlcv_list = ohlcv_list[::-1]
        
        # Загружаем данные в датафрейм Pandas
        df = pd.DataFrame(ohlcv_list, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['close'] = df['close'].astype(float)
        
        # Логика расчета RSI
        delta = df['close'].diff()
        
        # Получаем положительные и отрицательные движения
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/periods, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/periods, adjust=False).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        latest_rsi = float(rsi.iloc[-1])
        return latest_rsi
