import asyncio
import aiohttp
import pandas as pd
import numpy as np
import time

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    return macd, sig, hist

def calc_bollinger(series, period=20, std_dev=2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, lower

async def fetch_pools(session):
    # Fetch trending pools on Solana
    url = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            return [pool['attributes']['address'] for pool in data.get('data', [])]
    return []

async def fetch_ohlcv(session, pool):
    url = f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute?limit=1000"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get('data', {}).get('attributes', {}).get('ohlcv_list', [])
    return []

async def main():
    print("🚀 Старт сбора данных для Raydium модели...")
    async with aiohttp.ClientSession() as session:
        pools = await fetch_pools(session)
        print(f"✅ Найдено {len(pools)} трендовых пулов. Скачиваем свечи...")
        
        all_features = []
        for i, pool in enumerate(pools):
            print(f"📥 Скачивание пула {i+1}/{len(pools)}: {pool}")
            ohlcv = await fetch_ohlcv(session, pool)
            if not ohlcv or len(ohlcv) < 100:
                continue
                
            # GeckoTerminal returns newest first, so we reverse it to chronological order
            ohlcv.reverse()
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Calculate Indicators
            df['rsi'] = calc_rsi(df['close'])
            df['macd'], df['macd_signal'], df['macd_hist'] = calc_macd(df['close'])
            df['bb_upper'], df['bb_lower'] = calc_bollinger(df['close'])
            
            # Additional features
            df['bb_width_pct'] = (df['bb_upper'] - df['bb_lower']) / df['close']
            df['dist_to_bb_lower'] = (df['close'] - df['bb_lower']) / df['close']
            df['vol_sma_10'] = df['volume'].rolling(window=10).mean()
            df['vol_spike'] = df['volume'] / df['vol_sma_10']
            
            # Target generation: Look ahead 30 minutes
            # Buy condition: Price goes up 10% before it goes down 5%
            targets = []
            for j in range(len(df)):
                if j + 30 >= len(df):
                    targets.append(None)
                    continue
                
                entry_price = df['close'].iloc[j]
                future_window = df.iloc[j+1 : j+31]
                
                success = 0
                for _, row in future_window.iterrows():
                    if row['low'] < entry_price * 0.95: # Hit stop loss first
                        break
                    if row['high'] > entry_price * 1.10: # Hit take profit
                        success = 1
                        break
                targets.append(success)
                
            df['target'] = targets
            df = df.dropna()
            
            all_features.append(df)
            await asyncio.sleep(1) # rate limit respect
            
        if all_features:
            final_df = pd.concat(all_features)
            final_df.to_csv("raydium_dataset.csv", index=False)
            print(f"🎉 Датасет готов! Собрано {len(final_df)} примеров свечных паттернов.")
            print(f"Успешных сделок (рост 10%+): {final_df['target'].sum()} из {len(final_df)}")
        else:
            print("Ошибка сбора данных.")

if __name__ == '__main__':
    asyncio.run(main())
