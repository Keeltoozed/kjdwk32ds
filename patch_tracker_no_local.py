import re

with open('tracker.py', 'r') as f:
    content = f.read()

# Remove the fallback to local file
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
            print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}")'''

content = content.replace(old_load, new_load)

# Remove saving to local file
old_save = '''    def save_portfolio(self):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем локально
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            pass
            
        # 2. Сохраняем в Supabase (Render-proof)
        try:'''

new_save = '''    def save_portfolio(self):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем в Supabase (Render-proof)
        try:'''

content = content.replace(old_save, new_save)

with open('tracker.py', 'w') as f:
    f.write(content)
