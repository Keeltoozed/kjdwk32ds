import re

with open('tracker.py', 'r') as f:
    content = f.read()

# Add imports
if 'from supabase import create_client, Client' not in content:
    content = content.replace('import json', 'import json\nfrom supabase import create_client, Client')

# Regex replace load_portfolio
content = re.sub(r'    def load_portfolio\(self\):.*?    def save_portfolio', '''    def load_portfolio(self):
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
            if isinstance(v, dict):
                if "max_price_usd" not in v:
                    v["max_price_usd"] = v.get("entry_price_usd", 0)
                if "is_mature" not in v:
                    v["is_mature"] = False
                if "is_moonbag" not in v:
                    v["is_moonbag"] = False
                self.positions[k] = VirtualPosition(**v)

    def save_portfolio''', content, flags=re.DOTALL)

# Regex replace save_portfolio
content = re.sub(r'    def save_portfolio\(self\):.*?    def get_open_positions', '''    def save_portfolio(self):
        data = {k: getattr(v, "model_dump", v.dict)() for k, v in self.positions.items()}
        
        # 1. Сохраняем локально
        try:
            with open(self.filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            pass
            
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
            print(f"⚠️ Ошибка сохранения портфеля в Supabase: {e}")

    def get_open_positions''', content, flags=re.DOTALL)

with open('tracker.py', 'w') as f:
    f.write(content)
