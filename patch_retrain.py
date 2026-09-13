import re

with open('retrain_model.py', 'r') as f:
    content = f.read()

old_logic = '''        # Строгое правило: если монета после покупки сразу упала на 15% (наш стоп-лосс), 
        # то это 100% плохая сделка, ДАЖЕ ЕСЛИ потом она выросла на 500%.
        if drawdown < -0.15:
            labels.append(0) # Bad Trade (Hit Stop Loss)
        elif roi > 0.30:
            labels.append(1) # Good Trade (30%+ Profit without hitting stop)
        else:
            labels.append(0) # Choppy / Boring'''

new_logic = '''        # ЛОГИКА ДЛЯ ПОИСКА "СКРЫТЫХ РАКЕТ" (Hidden Rockets)
        # Если токен дал гигантский ROI (>100%), мы даем ему Label 1, даже если он сначала упал.
        # Это научит модель распознавать паттерны накопления и выноса "слабых рук" (Shakeout).
        if roi > 1.0:
            labels.append(1) # Супер-ракета (Даже со скрытой просадкой)
        elif drawdown < -0.25: 
            labels.append(0) # Обычный дамп (Упало и не восстановилось)
        elif roi > 0.30:
            labels.append(1) # Хороший трейд без сильных нервов
        else:
            labels.append(0) # Мусор / Флэт'''

content = content.replace(old_logic, new_logic)

with open('retrain_model.py', 'w') as f:
    f.write(content)
