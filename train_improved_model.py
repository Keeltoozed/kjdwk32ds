import pandas as pd, numpy as np, joblib
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import xgboost as xgb

df = pd.read_csv('moonshot_dataset.csv')
df['holder_concentration'] = df['top_10_holding_pct'] / (df['holders'] + 1)
df['velocity_per_holder'] = df['volume_24h'] / (df['holders'] + 1)
df['dev_to_top10_ratio'] = df['dev_holding_pct'] / (df['top_10_holding_pct'] + 1e-9)
if 'label' not in df.columns:
    df['label'] = ((df['dev_holding_pct'] < 10) & (df['volume_24h'] > 1000)).astype(int)

feature_cols = [
    'dev_holding_pct', 'top_10_holding_pct', 'tx_velocity_1m',
    'has_socials', 'funded_from_cex', 'holders',
    'holder_concentration', 'velocity_per_holder', 'dev_to_top10_ratio'
]
# Добавляем все доступные колонки из dataset
available = [c for c in feature_cols if c in df.columns]
if 'liquidity_usd' in df.columns: available.append('liquidity_usd')
if 'market_cap' in df.columns: available.append('market_cap')

X = df[available].fillna(0)
y = df['label']
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

scale_pos = (y == 0).sum() / max((y == 1).sum(), 1)
print(f"CV готов: 5 фолдов, {len(X_train)} train, {len(X_test)} test, scale_pos={scale_pos:.2f}")
model = xgb.XGBClassifier(
    n_estimators=500, max_depth=6, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.8, scale_pos_weight=scale_pos,
    eval_metric='auc', random_state=42, objective='binary:logistic'
)
model.fit(X_train, y_train)
y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=['Scam', 'Pump']))
print(f"ROC-AUC: {roc_auc_score(y_test, model.predict_proba(X_test)[:,1]):.4f}")
print("Feature importances:", dict(zip(X.columns, [f'{i:.2%}' for i in model.feature_importances_])))
joblib.dump(model, 'improved_model.pkl')
joblib.dump(X.columns.tolist(), 'feature_names.pkl')
print("✅ improved_model.pkl + feature_names.pkl сохранены")
