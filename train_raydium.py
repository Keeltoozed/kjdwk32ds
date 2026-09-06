import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score
import joblib

def main():
    print("🧠 Запуск обучения Raydium модели (XGBoost)...")
    
    # Load dataset
    df = pd.read_csv("raydium_dataset.csv")
    
    # Features
    features = ['rsi', 'macd', 'macd_signal', 'macd_hist', 'bb_upper', 'bb_lower', 
                'bb_width_pct', 'dist_to_bb_lower', 'vol_sma_10', 'vol_spike']
    
    X = df[features]
    y = df['target']
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Train
    model = xgb.XGBClassifier(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric='logloss',
        random_state=42
    )
    
    print("⏳ Обучение на исторических свечах...")
    model.fit(X_train, y_train)
    
    # Evaluate
    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]
    
    accuracy = accuracy_score(y_test, preds)
    precision = precision_score(y_test, preds)
    
    print(f"📊 Accuracy: {accuracy*100:.1f}%")
    print(f"🎯 Precision: {precision*100:.1f}%")
    
    # Feature importance
    print("\nВажность индикаторов:")
    importances = pd.Series(model.feature_importances_, index=features).sort_values(ascending=False)
    for k, v in importances.items():
        print(f"- {k}: {v*100:.1f}%")
        
    # Save
    joblib.dump(model, "raydium_model.pkl")
    print("\n✅ Модель сохранена как raydium_model.pkl")

if __name__ == '__main__':
    main()
