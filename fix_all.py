import os

print("🛠️ Запуск исправленного авто-патча...")

# 1. Исправляем config.py (снижаем порог)
config_path = "config.py"
if os.path.exists(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    replaced = False
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if any(target in line for target in ["SCORE_THRESHOLD", "MIN_SCORE", "AI_CONFIDENCE", "THRESHOLD"]):
            if "=" in line:
                var_name = line.split("=")[0].strip()
                new_lines.append(f"{var_name} = 70.0  # Авто-патч")
                replaced = True
                continue
        new_lines.append(line)
            
    if not replaced:
        new_lines.append("\nSCORE_THRESHOLD = 70.0\n")
        
    with open(config_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))
    print("✅ config.py успешно пропатчен!")

# 2. Безопасно исправляем analyzer.py (без IndentationError)
analyzer_path = "analyzer.py"
if os.path.exists(analyzer_path):
    with open(analyzer_path, "r", encoding="utf-8") as f:
        code = f.read()
    
    # Ищем стандартный блок проверки RugCheck
    old_check = """                        if token_info.get("mintAuthority") is not None:
                            return False
                        if token_info.get("freezeAuthority") is not None:
                            return False"""

    new_check = """                        if token_info.get("mintAuthority") is not None and not mint.endswith("pump"):
                            return False
                        if token_info.get("freezeAuthority") is not None and not mint.endswith("pump"):
                            return False"""

    if old_check in code:
        code = code.replace(old_check, new_check)
        with open(analyzer_path, "w", encoding="utf-8") as f:
            f.write(code)
        print("✅ analyzer.py успешно пропатчен (добавлен обход pump-токенов)!")
    else:
        print("⚠️ Блок RugCheck не найден или уже изменен в analyzer.py.")

print("🎉 Готово! Ошибок отступов больше не будет.")
