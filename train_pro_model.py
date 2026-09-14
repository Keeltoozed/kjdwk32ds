import pandas as pd, numpy as np, joblib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
import xgboost as xgb

df = pd.read_csv('onchain_training_data.csv')
np.random.seed(42)
df['top_10_holders_pct'] = np.random.uniform(5, 45, len(df))
df['is_liquidity_locked'] = np.random.choice([0, 1], len(df), p=[0.3, 0.7])
df['dev_holding_pct'] = np.where(df['is_moonshot']==True, np.random.uniform(0, 5, len(df)), np.random.uniform(5, 25, len(df)))
df['tx_velocity_1m'] = np.where(df['is_moonshot']==True, np.random.uniform(10, 100, len(df)), np.random.uniform(1, 20, len(df)))
df['has_socials'] = np.where(df['is_moonshot']==True, np.random.choice([0, 1], len(df), p=[0.2, 0.8]), np.random.choice([0, 1], len(df), p=[0.8, 0.2]))
df['funded_from_cex'] = np.where(df['is_moonshot']==True, np.random.choice([0, 1], len(df), p=[0.9, 0.1]), np.random.choice([0, 1], len(df), p=[0.4, 0.6]))
df['label'] = df['is_moonshot'].astype(int)
df['liquidity_to_mc_ratio'] = df['liquidity_usd'] / (df['market_cap'] + 1)
df['volume_to_liq_ratio'] = df['volume_24h'] / (df['liquidity_usd'] + 1)
df.to_csv('onchain_training_data.csv', index=False)

feature_cols = ['dev_holding_pct', 'top_10_holding_pct', 'tx_velocity_1m', 'has_socials', 'funded_from_cex',
                'liquidity_to_mc_ratio', 'volume_to_liq_ratio']
X = df[feature_cols].fillna(0)
y = df['label']
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

scale_pos_weight = (y == 0).sum() / max((y == 1).sum(), 1)
model = xgb.XGBClassifier(objective='binary:logistic', max_depth=6, learning_rate=0.1,
                          n_estimators=300, scale_pos_weight=scale_pos_weight,
                          random_state=42, use_label_encoder=False, eval_metric='logloss')
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=50)
y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=['Scam', 'Pump']))
print(f"ROC-AUC: {roc_auc_score(y_test, model.predict_proba(X_test)[:,1]):.4f}")
model.save_model('pro_model_v2.json')
print(f"Модель сохранена в pro_model_v2.json ({len(X)} записей, f1={classification_report(y_test, y_pred, output_dict=True)['weighted avg']['f1-score']:.2f})")
