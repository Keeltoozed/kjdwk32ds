lines = open("analyzer.py").read().split("\n")
for i, line in enumerate(lines):
    if line.strip() == "except Exception as e:" and lines[i+1].strip() == "pass" and lines[i+2].strip() == "# --- КОНЕЦ ФИЛЬТРОВ GOPLUS ---":
        lines[i+1] = '                print(f"⚠️ Ошибка GoPlus API для {address[:8]}: {e} (Таймаут, пропускаем)")'
        
open("analyzer.py", "w").write("\n".join(lines))
