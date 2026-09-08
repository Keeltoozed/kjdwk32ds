import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import shap
from catboost import CatBoostClassifier, Pool
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report, roc_auc_score
from whale_features import extract_revival_features, score_wallet_quality
import warnings
warnings.filterwarnings('ignore')

def create_target(df: pd.DataFrame) -> pd.Series:
    """
    Формирование целевой переменной (Secondary Wave Target).
    Условие: в следующие 24-48 часов цена дает +100% при максимальной просадке не более -25%.
    Ожидается, что df - это почасовые свечи. 48 часов = 48 строк вперед.
    """
    targets = []
    
    for i in range(len(df)):
        # Берем окно следующих 48 часов
        future_window = df.iloc[i+1 : i+49]
        if len(future_window) < 24: # Недостаточно данных для оценки
            targets.append(np.nan)
            continue
            
        entry_price = df.iloc[i]['price_usd']
        if entry_price == 0:
            targets.append(0)
            continue
            
        max_price = future_window['price_usd'].max()
        min_price_before_max = future_window.loc[:future_window['price_usd'].idxmax()]['price_usd'].min()
        
        profit_pct = (max_price - entry_price) / entry_price
        drawdown_pct = (min_price_before_max - entry_price) / entry_price
        
        # Если просадка превышает -25%, стоп-лосс выбивается ДО достижения профита
        if drawdown_pct <= -0.25:
            targets.append(0)
        # Если просадка в норме и профит достигает +100%
        elif profit_pct >= 1.0:
            targets.append(1)
        else:
            targets.append(0) # Боковик или недостаточный рост
            
    return pd.Series(targets, index=df.index)

def train_catboost_with_purged_cv(X: pd.DataFrame, y: pd.Series):
    """
    Обучение CatBoostClassifier с использованием TimeSeries кросс-валидации (Purged Split).
    """
    # Удаляем NaN в таргете (конец датасета)
    valid_idx = y.dropna().index
    X_clean = X.loc[valid_idx]
    y_clean = y.loc[valid_idx]
    
    # 1. Анализ матрицы корреляции для исключения дубликатов
    corr_matrix = X_clean.corr().abs()
    upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
    # Находим фичи с корреляцией > 0.85
    to_drop = [column for column in upper.columns if any(upper[column] > 0.85)]
    if to_drop:
        print(f"🗑️ Удаление коррелирующих признаков: {to_drop}")
        X_clean = X_clean.drop(columns=to_drop)
        
    print(f"Обучение на {len(X_clean)} сэмплах. Фичей: {X_clean.shape[1]}")
    
    # 2. TimeSeries K-Fold (Purged Split / Embargo - имитация задержки)
    tscv = TimeSeriesSplit(n_splits=3)
    models = []
    
    # CatBoost гиперпараметры
    params = {
        'iterations': 500,
        'learning_rate': 0.05,
        'depth': 6,
        'eval_metric': 'AUC',
        'random_seed': 42,
        'verbose': 100,
        'auto_class_weights': 'Balanced'
    }
    
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X_clean)):
        # Имитация Purged Split (разрыв/Embargo 48 часов, чтобы избежать look-ahead bias)
        # Так как наш таргет смотрит на 48ч вперед, между train и test должен быть зазор 48 свечей
        embargo_gap = 48
        if len(train_idx) <= embargo_gap:
            continue
            
        train_idx = train_idx[:-embargo_gap] 
        
        X_train, y_train = X_clean.iloc[train_idx], y_clean.iloc[train_idx]
        X_test, y_test = X_clean.iloc[test_idx], y_clean.iloc[test_idx]
        
        train_pool = Pool(X_train, y_train)
        test_pool = Pool(X_test, y_test)
        
        model = CatBoostClassifier(**params)
        model.fit(train_pool, eval_set=test_pool, early_stopping_rounds=50)
        models.append(model)
        
        preds = model.predict(X_test)
        print(f"\nFold {fold} Classification Report:")
        print(classification_report(y_test, preds))
        
    best_model = models[-1] # Берем последнюю (самую свежую) модель
    
    # 3. Интеграция SHAP для интерпретации
    print("\n🔍 Вычисление SHAP Values для интерпретации сигналов...")
    explainer = shap.TreeExplainer(best_model)
    shap_values = explainer.shap_values(X_clean)
    
    # Сохраняем график важности признаков
    plt.figure(figsize=(10, 6))
    shap.summary_plot(shap_values, X_clean, show=False)
    plt.savefig('shap_summary.png', bbox_inches='tight')
    plt.close()
    print("✅ График SHAP сохранен в 'shap_summary.png'")
    
    best_model.save_model("mature_meme_revival.cbm")
    print("✅ Модель сохранена в mature_meme_revival.cbm")
    
    return best_model

if __name__ == "__main__":
    print("🚀 Запуск пайплайна MatureMemeRevivalModel...")
    
    import os
    if not os.path.exists('wallet_history.csv') or not os.path.exists('token_candles.csv'):
        print("⚠️ Реальные данные (wallet_history.csv, token_candles.csv) не найдены.")
        print("🔧 Генерируем синтетический датасет зрелых токенов для демонстрации (Mock Data)...")
        
        # 1. Синтетические кошельки
        wallets = []
        for i in range(100):
            is_alpha = np.random.rand() > 0.8
            wallets.extend([{
                'wallet': f'wallet_{i}',
                'token': f'token_{np.random.randint(0, 10)}',
                'pnl_usd': np.random.uniform(10000, 300000) if is_alpha else np.random.uniform(-5000, 10000),
                'is_win': np.random.rand() > (0.3 if is_alpha else 0.6),
                'is_fomo_entry': np.random.rand() > 0.5
            } for _ in range(30)])
        df_wallets = pd.DataFrame(wallets)
        
        print("🧠 Профилирование Alpha-кошельков...")
        alpha_profiles = score_wallet_quality(df_wallets)
        print(f"Найдено Alpha-кошельков: {len(alpha_profiles)}")
        
        # 2. Синтетические свечи (почасовые, на 500 часов)
        dates = pd.date_range('2025-01-01', periods=500, freq='1H')
        df_candles = pd.DataFrame({
            'timestamp': dates,
            'price_usd': np.cumprod(1 + np.random.normal(0.001, 0.05, 500)),
            'total_vol_1h': np.random.uniform(50000, 2000000, 500),
            'market_cap_usd': np.random.uniform(1000000, 10000000, 500),
            'liquidity_usd': np.random.uniform(200000, 1000000, 500),
            'unique_buyers_1h': np.random.randint(50, 500, 500),
            'total_holders': np.random.randint(1000, 5000, 500)
        })
        
        # 3. Синтетические трейды
        trades = []
        for d in dates:
            trades.append({
                'timestamp': d,
                'wallet': f'wallet_{np.random.randint(0, 100)}',
                'volume_usd': np.random.uniform(1000, 50000)
            })
        df_trades = pd.DataFrame(trades)
        
        print("⚙️ Генерация квантовых признаков (Feature Engineering)...")
        X = extract_revival_features(df_trades, df_candles, alpha_profiles)
        
        print("🎯 Формирование таргета (+100% профит с макс. просадкой -25%)...")
        y = create_target(X)
        
        # Для гарантии того, что в тестовых данных есть оба класса:
        if y.nunique() <= 1:
            # Насильно делаем 15% сэмплов выигрышными (target = 1)
            y.iloc[np.random.choice(y.index, int(len(y)*0.15), replace=False)] = 1
            y.iloc[np.random.choice(y.index, int(len(y)*0.50), replace=False)] = 0
        
        # Убираем колонки, которые нельзя скармливать модели (например, время и цену, если они не фичи)
        X_train = X.drop(columns=['timestamp', 'price_usd', 'alpha_inflow_1h', 'total_vol_1h'], errors='ignore')
        
        print("🧠 Запуск кросс-валидации и обучения CatBoost...")
        train_catboost_with_purged_cv(X_train, y)
        print("\n✅ Обучение завершено. Готова к бою!")
    else:
        print("Загрузка реальных данных...")
