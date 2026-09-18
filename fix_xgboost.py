import re
with open("analyzer.py", "r") as f:
    code = f.read()

old_xgb_init = """    def __init__(self):
        self.session = None"""

new_xgb_init = """    def __init__(self):
        self.session = None
        self.pump_model = None
        self.raydium_model = None
        
        # Предзагрузка моделей в память один раз при старте
        try:
            import xgboost as xgb
            self.pump_model = xgb.XGBClassifier()
            self.pump_model.load_model("pump_model.json")
        except: pass
        
        try:
            import xgboost as xgb
            self.raydium_model = xgb.XGBClassifier()
            self.raydium_model.load_model("raydium_model_dex.json")
        except: pass"""

code = code.replace(old_xgb_init, new_xgb_init)

old_pump = """        import xgboost as xgb
        model = xgb.XGBClassifier()
        model.load_model("pump_model.json")
        prob = model.predict_proba(features)[0][1]"""

new_pump = """        if self.pump_model is None:
            return False # Fail-safe если модель не загрузилась
        prob = self.pump_model.predict_proba(features)[0][1]"""

code = code.replace(old_pump, new_pump)

old_raydium = """            import xgboost as xgb
            model = xgb.XGBClassifier()
            model.load_model("raydium_model_dex.json")
            prob = model.predict_proba(features)[0][1]"""

new_raydium = """            if self.raydium_model is None:
                return False
            prob = self.raydium_model.predict_proba(features)[0][1]"""

code = code.replace(old_raydium, new_raydium)

with open("analyzer.py", "w") as f:
    f.write(code)
