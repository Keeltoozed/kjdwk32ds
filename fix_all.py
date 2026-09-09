import os

print("🛠️ Запуск автоматического патча...")

# 1. Исправляем конфиг (снижаем порог до 1.2)
config_path = "config.py"
if os.path.exists(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Ищем стандартные названия порогов и заменяем
    replaced = False
    for target in ["SCORE_THRESHOLD", "MIN_SCORE", "AI_CONFIDENCE", "THRESHOLD"]:
        if target in content:
            # Заменяем строку вида TARGET = 75 или TARGET = 0.8 на 1.2
            lines = content.split("\n")
            new_lines = []
            for line in lines:
                if line.strip().startswith(target):
                    new_lines.append(f"{target} = 1.2  # Авто-патч под низкий скор")
                    replaced = True
                else:
                    new_lines.append(line)
            content = "\n" + "\n".join(new_lines)
            
    if not replaced:
        content += "\nSCORE_THRESHOLD = 1.2\n"
        
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ config.py успешно пропатчен!")

# 2. Исправляем скам-фильтр в analyzer.py или pump_fun_sniper.py
for filename in ["analyzer.py", "pump_fun_sniper.py"]:
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            code = f.read()
        
        # Добавляем обход проверки mint_authority для pump токенов
        patch_snippet = """
    # --- АВТО-ПАТЧ ДЛЯ PUMP.FUN ---
    if 'token_address' in locals() and str(token_address).endswith("pump"):
        is_safe = True  # Пропускаем MintAuthority для pump
    elif 'mint' in locals() and str(mint).endswith("pump"):
        is_safe = True
    # -----------------------------
"""
        if "endswlswith(\"pump\")" not in code and "endswith('pump')" not in code:
            code = patch_snippet + code
            with open(filename, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"✅ Файл {filename} пропатчен от ложных RugCheck блокировок!")

print("🎉 Все патчи применены. Перезапусти бота!")
