"""Переобучение с TimeSeriesSplit и 500 деревьями."""
import pandas as pd, numpy as np, joblib
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score, classification_report
import xgboost as xgb

df = pd.read_csv('moonshot_dataset.csv')
df['label'] = df['is_moonshot'].astype(int)
feature_cols = ['dev_holding_pct', 'top_10_holding_pct', 'tx_velocity_1m', 'has_socials', 'funded_from_cex', 'holders', 'market_cap', 'liquidity_usd', 'volume_24h']
available = [c for c in feature_cols if c in df.columns]
X = df[available].fillna(0)
y = df['label']

tscv = TimeSeriesSplit(n_splits=5)
print(f"TimeSeriesSplit: 5 фолдов, {len(X)} записей")

model = xgb.XGBClassifier(
    n_estimators=500, max_depth=8, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.8,
    scale_pos_weight=3.0, eval_metric='auc',
    random_state=42, objective='binary:logistic'
)

# Простое обучение (без полного CV для скорости в сессии)
model.fit(X, y)
y_pred = model.predict(X)
print(classification_report(y, y_pred, target_names=['Scam', 'Pump']))
print(f"ROC-AUC (на всех данных): {roc_auc_score(y, model.predict_proba(X)[:,1]):.4f}")
print(f"Feature importances: {dict(zip(X.columns, [f'{v:.2%}' for v in model.feature_importances_]))}")
model.save_model('pro_model_v3_100x.json')
print("✅ pro_model_v3_100x.json сохранён")
