import pandas as pd, numpy as np, joblib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report

df = pd.read_csv('onchain_training_data.csv')
feature_cols = ['dev_holding_pct', 'top_10_holders_pct', 'tx_velocity_1m', 'has_socials', 'funded_from_cex', 'liquidity_to_mc_ratio', 'volume_to_liq_ratio']
X = df[feature_cols].fillna(0)
y = df['label']
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42, class_weight='balanced')
model.fit(X_train, y_train)
y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=['Scam', 'Pump']))
print(f"Feature importances: {dict(zip(feature_cols, [f'{v:.2%}' for v in model.feature_importances_]))}")
joblib.dump(model, 'scam_filter_model.pkl')
print("✅ scam_filter_model.pkl сохранён")
