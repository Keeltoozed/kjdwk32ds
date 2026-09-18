import re

with open("analyzer.py", "r") as f:
    code = f.read()

# Fix the Pullback logic
old_pullback = """            # Строгий Pullback: ищем импульс на m5, но микро-откат на m1 (не падающий кинжал!)
            if _m5 > 0.5 and _m1 <= 0.0 and _m1 > -5.0:
                print(f"✅ [ENTRY-CANDIDATE] {mint}: PULLBACK — импульс m5 +{_m5}%, откат m1 {_m1}%, h1 {_h1}%, b/s {_buys}/{_sells}. Кандидат на вход.")
                pass
            else:
                print(f"🚫 [ENTRY] {mint}: m5 {_m5}% < импульса не было, пропуск.")
                return False"""

new_pullback = """            # Строгий Pullback: ищем настоящий импульс на m5 (> 15%), и здоровый микро-откат на m1 (от 0% до -8%)
            if _m5 > 15.0 and _m1 <= 0.0 and _m1 > -8.0:
                print(f"✅ [ENTRY-CANDIDATE] {mint}: PULLBACK — импульс m5 +{_m5}%, откат m1 {_m1}%, h1 {_h1}%, b/s {_buys}/{_sells}. Кандидат на вход.")
                pass
            else:
                reason = f"m5 {_m5}% < 15% (нет импульса)" if _m5 <= 15.0 else f"m1 {_m1}% (откат слишком глубокий или его нет)"
                print(f"🚫 [ENTRY] {mint}: {reason}. Пропуск.")
                return False"""

code = code.replace(old_pullback, new_pullback)

# Fix VIP overheat
old_overheat = """        # === VIP OVERHEAT GUARD: не покупаем вершину вертикали ===
        if is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            if _m5 > 250: # Если токен дал +250% за 5 минут, мы опоздали
                print(f"🔥 [OVERHEAT] {symbol}: Токен перегрет (+{_m5}% за m5). Риск покупки на хаях. Скипаем VIP.")
                return False"""

new_overheat = """        # === VIP OVERHEAT GUARD: не покупаем вершину вертикали ===
        if is_vip and pair_data:
            _pc = pair_data.get("priceChange") or {}
            _m5 = _pc.get("m5", 0) or 0
            if _m5 > 100: # Если токен дал +100% за 5 минут без отката, это вершина
                print(f"🔥 [OVERHEAT] {symbol}: Токен перегрет (+{_m5}% за m5). Риск покупки на хаях. Скипаем VIP.")
                return False
                
            # Для VIP всё равно требуем хотя бы малейший откат, чтобы не брать зеленую свечу
            _m1 = _pc.get("m1", 0) or 0
            if _m1 > 0:
                print(f"🔥 [FOMO PEAK] {symbol}: VIP токен растет прямо сейчас (m1 +{_m1}%). Ждем отката, не покупаем хай.")
                return False"""

code = code.replace(old_overheat, new_overheat)

# Fix VIP AI bypass
old_xgb_vip = """        threshold = 15.0
        if is_vip:
            threshold = 0.0 # Полностью отключаем фильтр ИИ для VIP ракет!
            print(f"🔥 [VIP] Порог XGBoost снижен до {threshold}% (Вход без оглядки на ИИ)")"""

new_xgb_vip = """        threshold = 20.0
        if is_vip:
            threshold = 10.0 # Для VIP ракет снижаем порог, но НЕ отключаем ИИ полностью! Скам ИИ должен фильтровать
            print(f"🔥 [VIP] Порог XGBoost снижен до {threshold}%")"""

code = code.replace(old_xgb_vip, new_xgb_vip)

with open("analyzer.py", "w") as f:
    f.write(code)
