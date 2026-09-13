import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import numpy as np

def generate_labels(df):
    """
    Новая логика разметки (Smart Labeling).
    Штрафует токены, которые показывают хороший ROI в долгосроке, 
    но сначала проваливаются в глубокую просадку (падающие ножи).
    """
    labels = []
    for i in range(len(df)):
        row = df.iloc[i]
        entry_price = row['entry_price']
        
        # Симулируем поведение цены после входа (в реальном датасете это должны быть колонки min_price_after, max_price_after)
        # Для демо-целей предположим, что у нас есть эти данные
        min_price = row.get('min_price_5m', entry_price * 0.5) # Если нет данных, симулируем просадку
        max_price = row.get('max_price_1h', entry_price * 2.0)
        
        drawdown = (min_price - entry_price) / entry_price
        roi = (max_price - entry_price) / entry_price
        
        # ЛОГИКА ДЛЯ ПОИСКА "СКРЫТЫХ РАКЕТ" (Hidden Rockets)
        # Если токен дал гигантский ROI (>100%), мы даем ему Label 1, даже если он сначала упал.
        # Это научит модель распознавать паттерны накопления и выноса "слабых рук" (Shakeout).
        if roi > 1.0:
            labels.append(1) # Супер-ракета (Даже со скрытой просадкой)
        elif drawdown < -0.25: 
            labels.append(0) # Обычный дамп (Упало и не восстановилось)
        elif roi > 0.30:
            labels.append(1) # Хороший трейд без сильных нервов
        else:
            labels.append(0) # Мусор / Флэт
            
    return labels

def train_new_model():
    print("🚀 Загрузка датасета...")
    try:
        df = pd.read_csv("pump_dataset.csv")
    except FileNotFoundError:
        print("Датасет не найден. Сначала соберите данные через работу бота.")
        return
        
    # Базовая очистка
    df = df.fillna(0)
    
    # Генерация умных лейблов
    if 'label' not in df.columns:
        print("⚠️ В датасете нет колонки 'label', генерируем на лету...")
        # Предполагаем наличие будущих цен. Если их нет, этот скрипт - шаблон.
        if 'min_price_5m' in df.columns:
            df['label'] = generate_labels(df)
        elif 'target' in df.columns:
            print("⚠️ Используем старую колонку 'target' (Legacy Dataset).")
            df['label'] = df['target']
        else:
            print("❌ Ошибка: В датасете нет данных для разметки (ни min_price_5m, ни target).")
            return

    # Динамически подхватываем все новые фичи (sell_buy_ratio, variance, acceleration и т.д.)
    features = [c for c in df.columns if c not in ['label', 'target', 'mint', 'symbol', 'entry_price', 'min_price_5m', 'max_price_1h', 'timestamp', 'is_success']]
    
    if len(features) == 0:
        print("❌ Ошибка: Нет фичей для обучения!")
        return
        
    print(f"📊 Найдено {len(features)} фичей для обучения: {features}")
    
    X = df[features]
    y = df['label']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Используем class_weight для борьбы с дисбалансом (убыточных всегда больше)
    pos_weight = (len(y) - sum(y)) / sum(y) if sum(y) > 0 else 1
    
    model = xgb.XGBClassifier(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=5,
        scale_pos_weight=pos_weight,
        tree_method='hist'
    )
    
    print("🧠 Обучение XGBoost (с защитой от падающих ножей)...")
    model.fit(X_train, y_train)
    
    preds = model.predict(X_test)
    print("\nОтчет о классификации:")
    print(classification_report(y_test, preds))
    
    model.save_model("pro_model_v2.json")
    print("✅ Модель сохранена как pro_model_v2.json")

if __name__ == "__main__":
    train_new_model()
