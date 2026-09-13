import re

with open('tracker.py', 'r') as f:
    content = f.read()

# Change PORTFOLIO_STATE to PORTFOLIO_STATE_V3
content = content.replace('"PORTFOLIO_STATE"', '"PORTFOLIO_STATE_V3"')
content = content.replace('"PORTFOLIO_STATE_V2"', '"PORTFOLIO_STATE_V3"') # Just in case

# Make fallback ignore local file and just create an empty one!
old_load = '''        except Exception as e:
            print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}")
            
        # 2. Фолбэк на локальный файл
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    data = json.load(f)
                    print("📁 Портфель загружен из локального файла")
                    self._parse_portfolio_data(data)
            except Exception as e:
                print(f"Error loading local portfolio: {e}")'''

new_load = '''        except Exception as e:
            print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}")
            
        # 2. Игнорируем локальный файл при старте, чтобы гитхаб-кэш не ломал дашборд!
        print("🧹 Начинаем с чистого листа (локальный файл игнорируется)")
        self.save_portfolio() # Перезаписываем локальный файл пустим словарем, чтобы дашборд тоже очистился!'''

content = content.replace(old_load, new_load)

with open('tracker.py', 'w') as f:
    f.write(content)
