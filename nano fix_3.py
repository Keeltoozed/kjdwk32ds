import os
import re

print("🛠️ Запуск исправления порогов и WS...")

# 1. Снижаем порог до 70% в файлах сканеров и логики
files_to_check = ["config.py", "ai_brain.py", "pump_fun_sniper.py", "fomo_scanner.py"]
for file in files_to_check:
    if os.path.exists(file):
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Ищем жесткие лимиты 80 или 75 и меняем на 70.0
        content = re.sub(r'(THRESHOLD\s*=\s*)80', r'\g<1>70.0', content)
        content = re.sub(r'(MIN_SCORE\s*=\s*)80', r'\g<1>70.0', content)
        content = re.sub(r'(score\s*<\s*)80', r'\g<1>70.0', content)
        content = re.sub(r'(score\s*<\s*)75\.?[0-9]*', r'\g<1>70.0', content)
        
        with open(file, 'w', encoding='utf-8') as f:
            f.write(content)

# 2. Добавляем импорты для корректного перехвата ошибки WS
ws_file = "pump_fun_sniper.py"
if os.path.exists(ws_file):
    with open(ws_file, 'r', encoding='utf-8') as f:
        ws_code = f.read()
    
    patch = "import tornado.websocket\nimport tornado.iostream\n"
    if "tornado.websocket" not in ws_code:
        with open(ws_file, 'w', encoding='utf-8') as f:
            f.write(patch + ws_code)

print("✅ Порог снижен до 70%. Перезапусти бота в Render.")
