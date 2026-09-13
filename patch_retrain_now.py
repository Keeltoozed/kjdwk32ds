import re

with open('retrain_now.py', 'r') as f:
    content = f.read()

# Replace hardcoded features extraction to be dynamic
old_features_list = '''            features_list = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
            for col in features_list:
                if col not in df_rockets.columns:
                    df_rockets[col] = 1.0 if col == "has_socials" else 0.0'''

new_features_list = '''            # Динамическое дополнение (все фичи будут извлечены при слиянии)'''

content = content.replace(old_features_list, new_features_list)

old_features_list_rugs = '''            features_list = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
            for col in features_list:
                if col not in df_rugs.columns:
                    df_rugs[col] = 0.0'''

new_features_list_rugs = '''            pass'''

content = content.replace(old_features_list_rugs, new_features_list_rugs)

old_concat = '''    # 4. Склеиваем всё вместе
    features = ["dev_holding_pct", "top_10_holding_pct", "tx_velocity_1m", "has_socials", "funded_from_cex"]
    
    frames_to_concat = [df_base]
    if not df_rockets.empty:
        frames_to_concat.append(df_rockets[features + ['target']])
    if not df_rugs.empty:
        frames_to_concat.append(df_rugs[features + ['target']])
        
    df_combined = pd.concat(frames_to_concat, ignore_index=True)'''

new_concat = '''    # 4. Склеиваем всё вместе и ДИНАМИЧЕСКИ находим все новые фичи (Micro-structure)
    frames_to_concat = [df_base]
    if not df_rockets.empty:
        frames_to_concat.append(df_rockets)
    if not df_rugs.empty:
        frames_to_concat.append(df_rugs)
        
    df_combined = pd.concat(frames_to_concat, ignore_index=True)
    
    # Заполняем пропуски нулями (если старые сделки не имели новых фичей)
    df_combined = df_combined.fillna(0)
    
    # Динамически получаем все колонки, кроме системных
    ignore_cols = ['target', 'is_success', 'mint', 'symbol', 'entry_price', 'min_price_5m', 'max_price_1h', 'timestamp', 'status', 'features', 'entry_time', 'exit_time', 'exit_reason', 'pnl', 'pnl_usd', 'confidence', 'id', 'check_24h_done', 'check_1h_done', 'check_4h_done', 'missed_pnl', 'post_exit_ath']
    features = [c for c in df_combined.columns if c not in ignore_cols]
    
    print(f"\\n📊 Итоговый список фичей ({len(features)} шт.): {features}")'''

content = content.replace(old_concat, new_concat)

with open('retrain_now.py', 'w') as f:
    f.write(content)
