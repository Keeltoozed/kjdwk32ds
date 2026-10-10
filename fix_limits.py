import re

# 1. analyzer.py
with open("analyzer.py", "r") as f:
    analyzer_data = f.read()
analyzer_data = re.sub(r'max_allowed_pct = 20\.0 if is_pump else 25\.0', 'max_allowed_pct = 30.0 if is_pump else 35.0', analyzer_data)
with open("analyzer.py", "w") as f:
    f.write(analyzer_data)

# 2. pump_fun_sniper.py
with open("pump_fun_sniper.py", "r") as f:
    sniper_data = f.read()
sniper_data = re.sub(r'top_10_sum_pct > 25\.0', 'top_10_sum_pct > 35.0', sniper_data)
with open("pump_fun_sniper.py", "w") as f:
    f.write(sniper_data)

# 3. config.py
with open("config.py", "r") as f:
    config_data = f.read()
config_data = re.sub(r'ROBINHOOD_MIN_LIQUIDITY = 10000', 'ROBINHOOD_MIN_LIQUIDITY = 30000', config_data)
config_data = re.sub(r'BASE_MIN_LIQUIDITY = 10000', 'BASE_MIN_LIQUIDITY = 20000', config_data)
with open("config.py", "w") as f:
    f.write(config_data)

