import pandas as pd
import numpy as np
import joblib
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

def train():
    print("🚀 Загрузка датасета pump_dataset.csv...")
    try:
        df = pd.read_csv("pump_dataset.csv")
    except FileNotFoundError:
        print("❌ Датасет не найден. Сначала запусти data_collector.py")
        return

    # Определение фичей и таргета
    features = [
        "dev_holding_pct",
        "top_10_holding_pct",
        "tx_velocity_1m",
        "has_socials",
        "funded_from_cex"
    ]
    target = "is_success"

    X = df[features]
    y = df[target]

    print("✂️ Разбиение на Train/Test (80/20)...")
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    print("🧠 Обучение модели XGBoost...")
    model = XGBClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=5,
        random_state=42,
        use_label_encoder=False,
        eval_metric='logloss'
    )
    
    model.fit(X_train, y_train)

    print("📊 Оценка модели на тестовой выборке:")
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"Accuracy: {acc * 100:.2f}%")
    print(classification_report(y_test, y_pred))

    # Feature Importance
    print("\n🔍 Важность признаков (Feature Importance):")
    importances = model.feature_importances_
    for name, imp in zip(features, importances):
        print(f" - {name}: {imp * 100:.1f}%")

    # Сохранение модели
    model_filename = "pump_model.pkl"
    joblib.dump(model, model_filename)
    print(f"\n💾 Модель успешно сохранена в {model_filename}!")
    
if __name__ == "__main__":
    train()
