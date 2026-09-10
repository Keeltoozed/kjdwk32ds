import pandas as pd
import json
import os
import xgboost as xgb
from shadow_tracker import ShadowTracker
from rugpull_feeder import feed_rugs_and_retrain
import config

try:
    from supabase import create_client, Client
except ImportError:
    pass

def full_retrain():
    print("🚀 НАЧИНАЕМ ПОЛНОЕ ПЕРЕОБУЧЕНИЕ МОДЕЛИ С УЧЕТОМ УПУЩЕННЫХ РАКЕТ...\n")
    
    # 1. Загружаем базовый датасет
    print("1️⃣ Чтение базового датасета...")
    df_base = pd.read_csv("pump_dataset.csv")
    if 'target' not in df_base.columns and 'is_success' in df_base.columns:
        df_base['target'] = df_base['is_success']
    
    # 2. Выгружаем упущенные ракеты из Shadow Tracker
    print("\n2️⃣ Поиск упущенных ракет (>100% PnL)...")
    shadow = ShadowTracker()
    shadow.export_for_retraining("false_negatives_dataset.csv")
    
    df_rockets = pd.DataFrame()
    if os.path.exists("false_negatives_dataset.csv"):
        df_rockets = pd.read_csv("false_negatives_dataset.csv")
        if not df_rockets.empty:
            print(f"Добавляем {len(df_rockets)} гемов для обучения!")
            # Убедимся что нужные фичи есть
            features_list = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
            for col in features_list:
                if col not in df_rockets.columns:
                    df_rockets[col] = 1.0 if col == "has_socials" else 0.0
            
            # Для ракет ставим target = 1
            df_rockets['target'] = 1
    
    # 3. Ищем рагпулы из trade_journal / supabase
    print("\n3️⃣ Поиск скамов (рагпулов) из прошлых торгов...")
    import sqlite3
    df_trades = pd.DataFrame()
    use_supabase = bool(getattr(config, 'SUPABASE_URL', None) and getattr(config, 'SUPABASE_KEY', None))
    
    if use_supabase:
        supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        res_pump = supabase.table("trades_pump").select("*").eq("status", "CLOSED").execute()
        df_pump = pd.DataFrame(res_pump.data) if res_pump.data else pd.DataFrame()
        res_raydium = supabase.table("trades_raydium").select("*").eq("status", "CLOSED").execute()
        df_raydium = pd.DataFrame(res_raydium.data) if res_raydium.data else pd.DataFrame()
        df_trades = pd.concat([df_pump, df_raydium], ignore_index=True)
    else:
        if os.path.exists("trade_journal.db"):
            with sqlite3.connect("trade_journal.db") as conn:
                df_trades = pd.read_sql_query("SELECT * FROM trades WHERE status='CLOSED'", conn)
                
    df_rugs = pd.DataFrame()
    if not df_trades.empty:
        if 'pnl_usd' in df_trades.columns:
            rugs_raw = df_trades[(df_trades['pnl_usd'] <= -3.0)].copy()
        elif 'pnl' in df_trades.columns:
            rugs_raw = df_trades[(df_trades['pnl'] <= -3.0) | (df_trades['pnl'] <= -0.75)].copy()
        else:
            rugs_raw = pd.DataFrame()
            
        if not rugs_raw.empty:
            f_list = []
            for f_str in rugs_raw['features']:
                try:
                    f_list.append(json.loads(f_str) if isinstance(f_str, str) else (f_str if isinstance(f_str, dict) else {}))
                except:
                    f_list.append({})
            df_rugs = pd.DataFrame(f_list)
            df_rugs['target'] = 0
            
            # Дополняем фичи
            features_list = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
            for col in features_list:
                if col not in df_rugs.columns:
                    df_rugs[col] = 0.0
                    
            print(f"Добавляем {len(df_rugs)} скамов для обучения!")

    # 4. Склеиваем всё вместе
    features = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
    
    frames_to_concat = [df_base]
    if not df_rockets.empty:
        frames_to_concat.append(df_rockets[features + ['target']])
    if not df_rugs.empty:
        frames_to_concat.append(df_rugs[features + ['target']])
        
    df_combined = pd.concat(frames_to_concat, ignore_index=True)
    
    # Сохраняем обновленный датасет
    df_combined.to_csv("pump_dataset.csv", index=False)
    print(f"\n✅ Датасет обновлен! Всего строк: {len(df_combined)}")
    
    # 5. ОБУЧЕНИЕ
    print("\n🧠 Начинаем обучение XGBoost...")
    # Удаляем строки с NaN в таргете (на всякий случай)
    df_combined = df_combined.dropna(subset=['target'])
    
    X = df_combined[features]
    y = df_combined['target'].astype(int)
    
    # Даем бОльший вес новым данным (ракетам и скамам)
    weights = [20.0 if idx >= len(df_base) else 1.0 for idx in range(len(df_combined))]
            
    model = xgb.XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=5, random_state=42)
    model.fit(X, y, sample_weight=weights)
    
    model.save_model("pump_model.json")
    print("🎉 УСПЕХ! Новая модель 'pump_model.json' сохранена и готова ловить иксы.")

if __name__ == "__main__":
    full_retrain()
