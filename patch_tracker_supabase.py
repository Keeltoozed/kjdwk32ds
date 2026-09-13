import re

with open('tracker.py', 'r') as f:
    content = f.read()

# Add imports
if 'from supabase import create_client, Client' not in content:
    content = content.replace('import json', 'import json\nfrom supabase import create_client, Client\nimport logging')

# Replace load_portfolio
old_load = '''    def load_portfolio(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    data = json.load(f)
                    for k, v in data.items():
                        # Поддержка старых записей
                        if "max_price_usd" not in v:
                            v["max_price_usd"] = v["entry_price_usd"]
                        if "is_mature" not in v:
                            v["is_mature"] = False
                            
                        self.positions[k] = VirtualPosition(**v)
            except Exception as e:
                print(f"Error loading portfolio: {e}")'''

new_load = '''    def load_portfolio(self):
        # 1. Пытаемся загрузить из Supabase (чтобы не терять данные при перезагрузке Render)
        try:
            if hasattr(config, 'SUPABASE_URL') and hasattr(config, 'SUPABASE_KEY'):
                supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
                res = supabase.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE").execute()
                if res.data:
                    data = json.loads(res.data[0]["features"])
                    print("✅ Портфель успешно загружен из Supabase!")
                    self._parse_portfolio_data(data)
                    return
        except Exception as e:
            print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}")
            
        # 2. Фолбэк на локальный файл
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    data = json.load(f)
                    print("📁 Портфель загружен из локального файла")
                    self._parse_portfolio_data(data)
            except Exception as e:
                print(f"Error loading local portfolio: {e}")

    def _parse_portfolio_data(self, data):
        for k, v in data.items():
            # Поддержка старых записей
            if "max_price_usd" not in v:
                v["max_price_usd"] = v["entry_price_usd"]
            if "is_mature" not in v:
                v["is_mature"] = False
            self.positions[k] = VirtualPosition(**v)'''

# Replace save_portfolio
old_save = '''    def save_portfolio(self):
        with open(self.filename, 'w') as f:
            json.dump({k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}, f, indent=4)'''

new_save = '''    def save_portfolio(self):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем локально
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"⚠️ Ошибка локального сохранения: {e}")
            
        # 2. Сохраняем в Supabase (Render-proof)
        try:
            if hasattr(config, 'SUPABASE_URL') and hasattr(config, 'SUPABASE_KEY'):
                supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
                supabase.table("trades_pump").upsert({
                    "mint": "PORTFOLIO_STATE",
                    "features": json.dumps(data),
                    "confidence": 0,
                    "status": "SYSTEM"
                }).execute()
        except Exception as e:
            print(f"⚠️ Ошибка сохранения портфеля в Supabase: {e}")'''

content = content.replace(old_load, new_load)
content = content.replace(old_save, new_save)

with open('tracker.py', 'w') as f:
    f.write(content)
