import re

with open("analyzer.py", "r") as f:
    data = f.read()

# Replace hardcoded limits with config variables
old_str = "if buys_m5 >= 100 and volume_m5 >= 50000 and liquidity >= 10000 and sells_m5 > 0:"
new_str = """_min_tx = getattr(config, "VIP_MIN_TX_M5", 40)
        _min_vol = getattr(config, "VIP_MIN_VOL_M5", 15000)
        if buys_m5 >= _min_tx and volume_m5 >= _min_vol and liquidity >= 10000 and sells_m5 > 0:"""

data = data.replace(old_str, new_str)
with open("analyzer.py", "w") as f:
    f.write(data)
