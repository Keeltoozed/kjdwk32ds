"""Обучение Scam Filter — AI для фильтрации скама вместо предсказания 100x."""
import pandas as pd, numpy as np, joblib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report

# Загружаем moonshot dataset и добавляем синтетические фичи для демонстрации
print("📂 Загружаем moonshot_dataset.csv...")
df = pd.read_csv("moonshot_dataset.csv")

# Добавляем отсутствующие фичи (для демонстрации — в реальном боте они придут из on-chain данных)
df['top_10_holders_pct'] = np.random.uniform(5, 45, len(df))
df['is_liquidity_locked'] = np.random.choice([0, 1], len(df), p=[0.3, 0.7])
df['dev_wallet_age_days'] = np.random.randint(1, 365, len(df))

# Разметка: is_moonshot = не скам (0), остальные = скам (1) — упрощённая эвристика для демо
# В реальном боте разметка придёт из анализа реальных раг-пулов
# Для честной демонстрации: считаем скамом всё, что НЕ архетип И имеет низкую ликвидность/объём

df['is_scam'] = np.where(
    (df['is_moonshot'] == True) | 
    ((df['liquidity_usd'] > 5000) & (df['volume_24h'] > 2000) & (df['market_cap'] < 500000)),
    0, 1
)

print(f"📊 Всего записей: {len(df)}")
print(f"📊 Скам (1): {df['is_scam'].sum()}, Безопасно (0): {(df['is_scam']==0).sum()}")

# Фичи
features = [
    'age_hours', 'liquidity_usd', 'volume_24h', 'market_cap', 'holders',
    'top_10_holders_pct', 'is_liquidity_locked'
]
df['liquidity_to_mc_ratio'] = df['liquidity_usd'] / (df['market_cap'] + 1)
df['volume_to_liq_ratio'] = df['volume_24h'] / (df['liquidity_usd'] + 1)
features += ['liquidity_to_mc_ratio', 'volume_to_liq_ratio']

X = df[features].fillna(0)
y = df['is_scam']

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

print("🧠 Обучаем RandomForestClassifier...")
model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42, class_weight='balanced')
model.fit(X_train, y_train)

print("📊 Оценка модели (Scam Filter):")
y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=['Safe (0)', 'Scam (1)']))

print("📊 Важность признаков:")
for feat, imp in zip(features, model.feature_importances_):
    print(f"  - {feat}: {imp:.2%}")

joblib.dump(model, "scam_filter_model.pkl")
print("✅ Модель сохранена: scam_filter_model.pkl")
