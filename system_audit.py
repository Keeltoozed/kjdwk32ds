import asyncio
import json
import pandas as pd
import numpy as np
import xgboost as xgb
import os
import config
from ai_brain import AIBrainML

try:
    from supabase import create_client, Client
except ImportError:
    pass

class SystemAuditor:
    def __init__(self):
        print("🕵️‍♂️ [MLOps Auditor] Инициализация системного аудита...")
        self.use_supabase = bool(getattr(config, 'SUPABASE_URL', None) and getattr(config, 'SUPABASE_KEY', None))
        if self.use_supabase:
            self.db: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        else:
            import sqlite3
            self.db = sqlite3.connect("trade_journal.db")
            
        self.brain = AIBrainML()

    def get_recent_trades(self, limit=10):
        if self.use_supabase:
            res = self.db.table("trades").select("*").limit(limit).execute()
            return res.data
        else:
            return pd.read_sql_query(f"SELECT * FROM trades LIMIT {limit}", self.db).to_dict('records')

    async def test_signal_integrity(self):
        print("\n🧪 ТЕСТ 1: Signal Integrity & Data Leakage")
        trades = self.get_recent_trades(5)
        
        if not trades:
            print("⚠️ Нет данных для теста. Пропуск.")
            return

        passed = 0
        for t in trades:
            original_conf = float(t.get('confidence', 0))
            features = json.loads(t.get('features', '{}'))
            
            if not features:
                continue
                
            df = pd.DataFrame([features])
            
            try:
                expected_cols = ['dev_holding_pct', 'top_10_holding_pct', 'tx_velocity_1m', 'has_socials', 'funded_from_cex']
                for col in expected_cols:
                    if col not in df.columns:
                        df[col] = 0.0
                df = df[expected_cols]
                
                new_conf = self.brain.model.predict_proba(df)[0][1] * 100
                diff = abs(original_conf - new_conf)
                
                if diff < 1.0:
                    passed += 1
                else:
                    print(f"❌ DATA DRIFT ОБНАРУЖЕН! Токен {t['mint']}: Исторический скор {original_conf:.1f}%, сейчас {new_conf:.1f}%")
            except Exception as e:
                print(f"❌ Ошибка инференса: {e}")
                
        print(f"✅ Пройдено {passed}/{len(trades)} тестов на целостность данных.")

    async def test_learning_loop(self):
        print("\n🧪 ТЕСТ 2: Continuous Learning Validation (Retrain Penalty)")
        fake_features = {
            'dev_holding_pct': 5.0, 
            'top_10_holding_pct': 30.0, 
            'tx_velocity_1m': 15.0,
            'has_socials': 1.0, 
            'funded_from_cex': 1.0
        }
        df = pd.DataFrame([fake_features])
        
        initial_score = self.brain.model.predict_proba(df)[0][1] * 100
        print(f"➡️ Исходный Score для паттерна: {initial_score:.2f}%")
        
        print("🔄 Симуляция Auto-Labeling (Target=0, Weight=5.0) и переобучения...")
        try:
            X_fake = df
            y_fake = pd.Series([0])
            w_fake = pd.Series([5.0])
            
            temp_model = xgb.XGBClassifier(n_estimators=1)
            temp_model.fit(X_fake, y_fake, sample_weight=w_fake, xgb_model=self.brain.model)
            
            new_score = temp_model.predict_proba(df)[0][1] * 100
            print(f"➡️ Новый Score после обучения на ошибке: {new_score:.2f}%")
            
            if new_score < initial_score:
                print(f"✅ УСПЕХ: Модель снизила уверенность на {initial_score - new_score:.2f}%. Пайплайн работает.")
            else:
                print("❌ ПРОВАЛ: Модель не отреагировала на штрафной вес!")
        except Exception as e:
            print(f"⚠️ Модель не поддерживает инкрементальное обучение напрямую: {e}")
            print("Рекомендация: переписать auto_trainer.py для поддержки sample_weights и DMatrix.")

    def test_risk_management(self):
        print("\n🧪 ТЕСТ 3: Risk Management (PnL Math & Kill-Switch)")
        print("Проверка математики PnL в трекере...")
        
        entry_sol = 1.0
        exit_sol = 1.0
        bot_pnl = (exit_sol - entry_sol) / entry_sol * 100
        
        real_entry = entry_sol * 1.01 # 1% Pump.fun fee
        real_exit = exit_sol * 0.99  # 1% Pump.fun fee
        real_pnl = ((real_exit - real_entry - 0.003) / real_entry) * 100
        
        print(f"Бот записывает PnL: {bot_pnl:.2f}%")
        print(f"Реальный PnL (с учетом Fees): {real_pnl:.2f}%")
        
        if abs(bot_pnl - real_pnl) > 0.5:
            print("❌ ПРОВАЛ: Бот обучает нейросеть на 'грязных' метках! Околонулевые сделки — это на самом деле убытки.")
            
        print("Проверка Daily Drawdown Kill-Switch...")
        if not hasattr(config, "MAX_DAILY_LOSS_USD"):
            print("❌ ПРОВАЛ: Отсутствует глобальный предохранитель `MAX_DAILY_LOSS_USD` в config.py!")

if __name__ == "__main__":
    auditor = SystemAuditor()
    asyncio.run(auditor.test_signal_integrity())
    asyncio.run(auditor.test_learning_loop())
    auditor.test_risk_management()
