import re

with open('retrain_model.py', 'r') as f:
    content = f.read()

old_logic = '''        if 'min_price_5m' in df.columns:
            df['label'] = generate_labels(df)
        else:
            print("❌ Ошибка: В датасете нет данных о будущем движении цены (min_price_5m). Невозможно применить Smart Labeling.")
            return

    # Динамически подхватываем все новые фичи (sell_buy_ratio, variance, acceleration и т.д.)
    features = [c for c in df.columns if c not in ['label', 'mint', 'symbol', 'entry_price', 'min_price_5m', 'max_price_1h', 'timestamp']]'''

new_logic = '''        if 'min_price_5m' in df.columns:
            df['label'] = generate_labels(df)
        elif 'target' in df.columns:
            print("⚠️ Используем старую колонку 'target' (Legacy Dataset).")
            df['label'] = df['target']
        else:
            print("❌ Ошибка: В датасете нет данных для разметки (ни min_price_5m, ни target).")
            return

    # Динамически подхватываем все новые фичи (sell_buy_ratio, variance, acceleration и т.д.)
    features = [c for c in df.columns if c not in ['label', 'target', 'mint', 'symbol', 'entry_price', 'min_price_5m', 'max_price_1h', 'timestamp', 'is_success']]
    
    if len(features) == 0:
        print("❌ Ошибка: Нет фичей для обучения!")
        return
        
    print(f"📊 Найдено {len(features)} фичей для обучения: {features}")'''

content = content.replace(old_logic, new_logic)

with open('retrain_model.py', 'w') as f:
    f.write(content)
