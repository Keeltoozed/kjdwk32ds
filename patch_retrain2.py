import re

with open('retrain_model.py', 'r') as f:
    content = f.read()

content = content.replace("    features = [c for c in df.columns", "    # Динамически подхватываем все новые фичи (sell_buy_ratio, variance, acceleration и т.д.)\n    features = [c for c in df.columns")

with open('retrain_model.py', 'w') as f:
    f.write(content)
