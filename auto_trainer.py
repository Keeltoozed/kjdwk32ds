import sqlite3
import pandas as pd
import json
import os
import joblib
import xgboost as xgb
from sklearn.metrics import precision_score, accuracy_score
from sklearn.model_selection import train_test_split
import requests
import warnings
warnings.filterwarnings('ignore')

class ContinuousLearningPipeline:
    def __init__(self, db_path="trade_journal.db", base_dataset="pump_dataset.csv", model_path="pump_model.pkl"):
        self.db_path = db_path
        self.base_dataset = base_dataset
        self.model_path = model_path
        self.tg_bot_token = os.getenv("TG_BOT_TOKEN", "") # Задай в .env
        self.tg_chat_id = os.getenv("TG_CHAT_ID", "")

    def extract_and_label_new_data(self) -> pd.DataFrame:
        """
        Извлекает закрытые сделки из SQLite и применяет Auto-Labeling.
        """
        if not os.path.exists(self.db_path):
            print("База данных пуста, нет нового опыта.")
            return pd.DataFrame()

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("SELECT * FROM trades WHERE status = 'CLOSED'", conn)
            
        if df.empty:
            return pd.DataFrame()

        # Парсим JSON фичи в отдельные колонки
        features_df = df['features'].apply(json.loads).apply(pd.Series)
        df = pd.concat([df, features_df], axis=1)

        # ---------------- AUTO-LABELING ----------------
        # 1 - успех (ракета, PnL > 50%), 0 - провал (PnL < 0)
        # Сделки от 0 до 50% мы можем игнорировать (неоднозначные), либо настроить свой трешхолд
        def assign_label(pnl):
            if pnl > 50: return 1
            if pnl < 0: return 0
            return None 

        df['is_success'] = df['pnl'].apply(assign_label)
        df = df.dropna(subset=['is_success'])

        # Назначаем sample_weight
        # False Positives: Бот был уверен (>80%), но сделка ушла в минус. Штрафуем модель жестко (вес 2.0).
        def assign_weight(row):
            if row['confidence'] > 80 and row['is_success'] == 0:
                return 2.0
            return 1.0
            
        df['sample_weight'] = df.apply(assign_weight, axis=1)
        
        return df

    def prepare_training_data(self):
        """Объединяет базовый датасет с новым опытом."""
        print("📥 Загрузка базового датасета...")
        base_df = pd.read_csv(self.base_dataset)
        base_df['sample_weight'] = 1.0 # У старых данных стандартный вес
        
        print("📥 Извлечение опыта бота...")
        new_df = self.extract_and_label_new_data()
        
        if not new_df.empty:
            print(f"➕ Добавлено {len(new_df)} новых записей из реального опыта бота.")
            # Оставляем только те колонки, которые есть в базе
            common_cols = [c for c in base_df.columns if c in new_df.columns]
            new_df_filtered = new_df[common_cols + ['sample_weight']]
            
            combined_df = pd.concat([base_df, new_df_filtered], ignore_index=True)
            # Добавим в base_df целевую переменную если ее имя отличается (у тебя is_success)
        else:
            combined_df = base_df
            
        return combined_df

    def train_and_evaluate(self):
        """Retraining Engine & Shadow Deployment."""
        df = self.prepare_training_data()
        
        # Разделение на фичи и таргет
        target_col = 'is_success'
        exclude_cols = ['mint', 'token', 'label', 'target', 'id', target_col, 'sample_weight']
        features = [c for c in df.columns if c not in exclude_cols and df[c].dtype in ['float64', 'int64']]
        
        X = df[features]
        y = df[target_col]
        weights = df['sample_weight']
        
        # Оставляем последние 10% данных для валидации (чтобы проверять на самом свежем рынке)
        X_train, X_val, y_train, y_val, w_train, w_val = train_test_split(
            X, y, weights, test_size=0.1, shuffle=False
        )

        print(f"🧠 Обучение новой модели (XGBoost)... [Features: {len(features)}]")
        new_model = xgb.XGBClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=5,
            random_state=42
        )
        # Обучаем с учетом весов ошибок
        new_model.fit(X_train, y_train, sample_weight=w_train)
        
        # Оценка новой модели
        y_pred_new = new_model.predict(X_val)
        new_precision = precision_score(y_val, y_pred_new, zero_division=0)
        
        # ---------------- SHADOW DEPLOYMENT ----------------
        print("\n⚖️ Запуск ModelEvaluator (Shadow Deployment)...")
        if os.path.exists(self.model_path):
            old_model = joblib.load(self.model_path)
            y_pred_old = old_model.predict(X_val)
            old_precision = precision_score(y_val, y_pred_old, zero_division=0)
            
            print(f"Старая модель Precision: {old_precision:.4f}")
            print(f"Новая модель Precision:  {new_precision:.4f}")
            
            if new_precision > old_precision:
                print("✅ Новая модель лучше! Перезаписываем pump_model.pkl...")
                joblib.dump(new_model, self.model_path)
                self.send_tg_notification(f"🚀 AI Модель успешно обновлена!\nНовый Precision: {new_precision:.2f} (было {old_precision:.2f})\nУчтено новых ошибок из Trade Journal.")
            else:
                print("❌ Новая модель хуже или такая же. Откат (Rollback). Оставляем старую версию.")
        else:
            print("Первое обучение! Сохраняем модель.")
            joblib.dump(new_model, self.model_path)

    def send_tg_notification(self, text: str):
        """Отправка отчета в Telegram."""
        if not self.tg_bot_token or not self.tg_chat_id:
            return
        url = f"https://api.telegram.org/bot{self.tg_bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": self.tg_chat_id, "text": text}, timeout=5)
        except Exception as e:
            print(f"TG Error: {e}")

if __name__ == "__main__":
    print("🤖 Запуск Continuous Learning Pipeline...")
    pipeline = ContinuousLearningPipeline()
    pipeline.train_and_evaluate()
