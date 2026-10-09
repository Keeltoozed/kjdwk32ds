import re
with open("analyzer.py", "r") as f:
    content = f.read()

# restore print log and change timeout from 5 to 1.5
content = content.replace("            except Exception as e:\n                pass\n        # --- КОНЕЦ ФИЛЬТРОВ GOPLUS ---",
                          "            except Exception as e:\n                print(f\"⚠️ Ошибка GoPlus API для {address[:8]}: {e} (Таймаут, пропускаем)\")\n        # --- КОНЕЦ ФИЛЬТРОВ GOPLUS ---")

content = content.replace("async with session.get(goplus_url, headers=gp_headers, timeout=5) as resp:",
                          "async with session.get(goplus_url, headers=gp_headers, timeout=1.5) as resp:")

with open("analyzer.py", "w") as f:
    f.write(content)
