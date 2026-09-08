import pandas as pd
import numpy as np

def generate_mock_data():
    np.random.seed(42)
    # 1. Mock Wallet History
    wallets = [f"Wallet_{i}" for i in range(50)]
    wallet_history = pd.DataFrame({
        'wallet': np.random.choice(wallets, 500),
        'token': 'MOCK_TOKEN',
        'pnl_usd': np.random.normal(5000, 200000, 500),
        'is_win': np.random.choice([0, 1], 500, p=[0.4, 0.6]),
        'is_fomo_entry': np.random.choice([0, 1], 500, p=[0.7, 0.3])
    })
    
    # 2. Mock Token Candles (1 year of hourly data = ~8760 rows)
    dates = pd.date_range(start="2024-01-01", periods=1000, freq="1H")
    price = np.cumprod(1 + np.random.normal(0.001, 0.05, 1000))
    candles = pd.DataFrame({
        'timestamp': dates,
        'price_usd': price,
        'liquidity_usd': np.random.uniform(10000, 500000, 1000),
        'market_cap_usd': price * 1_000_000_000,
        'unique_buyers_1h': np.random.poisson(50, 1000),
        'total_holders': np.cumsum(np.random.poisson(10, 1000)) + 100
    })
    candles.set_index('timestamp', inplace=True)
    
    # 3. Mock Trades
    trades = pd.DataFrame({
        'timestamp': np.random.choice(dates, 5000),
        'wallet': np.random.choice(wallets, 5000),
        'volume_usd': np.random.exponential(1000, 5000)
    })
    trades.sort_values('timestamp', inplace=True)
    
    return wallet_history, candles, trades

if __name__ == "__main__":
    import train_revival_model as trm
    from whale_features import score_wallet_quality, extract_revival_features
    
    print("Генерация синтетических данных для тестирования пайплайна...")
    w_hist, candles, trades = generate_mock_data()
    
    print("1. Профилирование кошельков...")
    alpha_wallets = score_wallet_quality(w_hist)
    print(f"Найдено {len(alpha_wallets)} Alpha-кошельков.")
    
    print("2. Экстракция фичей...")
    X = extract_revival_features(trades, candles, alpha_wallets)
    
    print("3. Создание Target переменной...")
    y = trm.create_target(candles)
    
    # Чтобы таргет имел единицы (т.к. это случайное блуждание, единиц может быть мало)
    # Искусственно добавим единиц
    y.iloc[100:150] = 1
    y.iloc[500:550] = 1
    
    print("4. Запуск обучения CatBoost...")
    model = trm.train_catboost_with_purged_cv(X, y)
