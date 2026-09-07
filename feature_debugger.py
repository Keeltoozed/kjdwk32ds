import pandas as pd
import numpy as np
import json
import sys

def load_training_stats(csv_path: str):
    """
    Рассчитывает средние значения фичей из обучающего датасета бота (Pump/Raydium).
    """
    print(f"Loading training data from {csv_path}...")
    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"Error: Could not find training dataset at {csv_path}")
        sys.exit(1)
        
    # Выбираем числовые колонки, исключая технические (метки и id)
    exclude_cols = ['label', 'target', 'id', 'token', 'mint', 'timestamp']
    features = [col for col in df.columns if df[col].dtype in ['float64', 'int64'] and col not in exclude_cols]
    
    means = df[features].mean().to_dict()
    return means

def analyze_data_drift(logs_path: str, training_csv: str):
    """
    Анализирует Data Drift (Смещение Данных):
    Сравнивает фичи последних убыточных сделок бота с теми, на которых он обучался.
    """
    print("\n🔍 Starting Data Drift Diagnostics...")
    
    train_means = load_training_stats(training_csv)

    # Пытаемся загрузить логи торгов (scanned_tokens.json или подобное)
    recent_trades = []
    try:
        with open(logs_path, 'r') as f:
            data = json.load(f)
            # Адаптация под структуру логов. Предполагаем список словарей
            # Если это dict с токенами как ключи:
            if isinstance(data, dict):
                recent_trades = list(data.values())
            else:
                recent_trades = data
    except FileNotFoundError:
        print(f"⚠️ Could not find real trades logs at {logs_path}.")
        print("MOCKING DATA for demonstration purposes (replace with real logs in production).")
        # Моковые данные для примера (адаптировано под твои колонки)
        recent_trades = [
            {"token": "T1", "features": {"dev_holding_pct": 12.5, "top_10_holding_pct": 80.0, "tx_velocity_1m": 4.5, "has_socials": 1, "funded_from_cex": 0}, "profit_pct": -15.5},
            {"token": "T2", "features": {"dev_holding_pct": 15.0, "top_10_holding_pct": 85.0, "tx_velocity_1m": 2.1, "has_socials": 0, "funded_from_cex": 1}, "profit_pct": -22.1},
            {"token": "T3", "features": {"dev_holding_pct": 8.0, "top_10_holding_pct": 92.0, "tx_velocity_1m": 8.5, "has_socials": 1, "funded_from_cex": 1}, "profit_pct": -8.0},
            {"token": "T4", "features": {"dev_holding_pct": 2.0, "top_10_holding_pct": 30.0, "tx_velocity_1m": 25.0, "has_socials": 1, "funded_from_cex": 1}, "profit_pct": 45.0} # Profitable
        ]

    # Фильтруем убыточные сделки (предполагаем, что поле profit или pnl < 0)
    # Попробуем найти поле с профитом, или берем всё, если это чистые логи 'ошибок'
    losing_trades = []
    for t in recent_trades:
        profit = t.get("profit", t.get("profit_pct", t.get("pnl", 0)))
        # Если мы не можем найти профит, предполагаем, что это список ошибочных сделок
        if profit < 0 or "features" in t: 
            if "features" in t:
                losing_trades.append(t["features"])

    if not losing_trades:
        print("No losing trades found in the logs with 'features' extracted.")
        sys.exit(0)

    # Извлекаем средние показатели недавних плохих сделок
    recent_features = pd.DataFrame(losing_trades)
    recent_means = recent_features.mean().to_dict()

    print(f"\n📊 Analysed {len(losing_trades)} recent losing trades.")
    print("=" * 70)
    print(f"{'Feature':<25} | {'Train Mean':<12} | {'Real-time Mean':<15} | {'Drift (%)':<10}")
    print("=" * 70)

    drift_detected = False
    for feature in recent_means.keys():
        if feature in train_means:
            train_val = train_means[feature]
            real_val = recent_means[feature]
            
            if train_val != 0 and not pd.isna(train_val) and not pd.isna(real_val):
                diff_pct = ((real_val - train_val) / abs(train_val)) * 100
                
                # Триггер: если фича отклонилась больше чем на 30% от обучающей выборки
                flag = "⚠️ DRIFT" if abs(diff_pct) > 30 else ""
                if flag: 
                    drift_detected = True
                
                print(f"{feature:<25} | {train_val:<12.2f} | {real_val:<15.2f} | {diff_pct:>+8.2f}% {flag}")

    print("=" * 70)
    
    if drift_detected:
        print("\n🚨 CONCLUSION: Significant Data Drift Detected!")
        print("Модель обучалась на одних рыночных условиях (Train Mean), но сейчас бот торгует в совершенно других (Real-time Mean).")
        print("Рекомендации:")
        print("1. Обучи модель заново (retrain) на свежих данных последних 48 часов.")
        print("2. Добавь хард-фильтры (hard-filters) на вход, чтобы бот пропускал токены, которые слишком сильно отличаются от 'идеальных' метрик из трейна.")
    else:
        print("\n✅ CONCLUSION: No significant data drift detected.")
        print("Характеристики текущих токенов совпадают с обучающей выборкой. Проблема убытков чисто в Исполнении (Execution/Latency) или MEV.")

if __name__ == "__main__":
    # В реальном сценарии замени 'recent_trades.json' на путь к твоим реальным логам
    # А 'pump_dataset.csv' на актуальный датасет (например raydium_dataset.csv)
    analyze_data_drift(logs_path="recent_trades.json", training_csv="pump_dataset.csv")
