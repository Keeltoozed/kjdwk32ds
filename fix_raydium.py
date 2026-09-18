import re
with open("analyzer.py", "r") as f:
    code = f.read()

old_ray = """        try:
            import xgboost as xgb
            model = xgb.XGBClassifier()
            model.load_model("raydium_model_dex.json")
            prob = model.predict_proba(df)[0][1]"""

new_ray = """        try:
            if self.raydium_model is None: return False
            prob = self.raydium_model.predict_proba(df)[0][1]"""

code = code.replace(old_ray, new_ray)
with open("analyzer.py", "w") as f:
    f.write(code)
