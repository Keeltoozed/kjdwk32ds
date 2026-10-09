import re

with open("pump_fun_sniper.py", "r") as f:
    content = f.read()

content = re.sub(r'if top_10_sum_pct > 30\.0:', r'if top_10_sum_pct > 25.0:', content)
content = re.sub(r'\(>30%\)', r'(>25%)', content)

with open("pump_fun_sniper.py", "w") as f:
    f.write(content)
print("pump_fun_sniper.py updated")
