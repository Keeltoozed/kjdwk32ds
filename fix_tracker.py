import re

with open('tracker.py', 'r') as f:
    content = f.read()

old_load_save = '''    def load_portfolio(self):
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
                        if "is_moonbag" not in v:
                            v["is_moonbag"] = False
                        self.positions[k] = VirtualPosition(**v)
            except Exception as e:
                print(f"Error loading portfolio: {e}")

    def save_portfolio(self):
        with open(self.filename, 'w') as f:
            json.dump({k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}, f, indent=4)'''

new_load_save = '''    def _parse_portfolio_data(self, data):
        for k, v in data.items():
            if isinstance(v, dict):
                if "max_price_usd" not in v:
                    v["max_price_usd"] = v.get("entry_price_usd", 0)
                if "is_mature" not in v:
                    v["is_mature"] = False
                if "is_moonbag" not in v:
                    v["is_moonbag"] = False
                self.positions[k] = VirtualPosition(**v)

    def load_portfolio(self):
        # 1. Пытаемся загрузить из Supabase (чтобы не терять данные при перезагрузке Render)
        try:
            if hasattr(config, 'SUPABASE_URL') and hasattr(config, 'SUPABASE_KEY'):
                from supabase import create_client, Client
                import json
                supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
                res = supabase.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE_V3").execute()
                if res.data:
                    data = json.loads(res.data[0]["features"])
                    print("✅ Портфель успешно загружен из Supabase!")
                    self._parse_portfolio_data(data)
                    
                    # Синхронизируем с локальным файлом для дашборда
                    with open(self.filename, 'w') as f:
                        json.dump(data, f, indent=4)
                    return
        except Exception as e:
            print(f"⚠️ Не удалось загрузить портфель из Supabase: {e}")
            
        # 2. Игнорируем локальный файл при старте, чтобы гитхаб-кэш не ломал дашборд!
        print("🧹 Начинаем с чистого листа (локальный файл игнорируется)")
        self.save_portfolio() # Перезаписываем локальный файл пустим словарем, чтобы дашборд тоже очистился!

    def save_portfolio(self):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем локально (для Streamlit)
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            pass
            
        # 2. Сохраняем в Supabase (Render-proof)
        try:
            if hasattr(config, 'SUPABASE_URL') and hasattr(config, 'SUPABASE_KEY'):
                from supabase import create_client, Client
                import json
                supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
                supabase.table("trades_pump").upsert({
                    "mint": "PORTFOLIO_STATE_V3",
                    "features": json.dumps(data),
                    "confidence": 0,
                    "status": "SYSTEM"
                }).execute()
        except Exception as e:
            print(f"⚠️ Ошибка сохранения портфеля в Supabase: {e}")'''

content = content.replace(old_load_save, new_load_save)

with open('tracker.py', 'w') as f:
    f.write(content)
