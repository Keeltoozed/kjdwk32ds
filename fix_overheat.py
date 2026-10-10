import re

# 1. Update config.py
with open("config.py", "r") as f:
    config_data = f.read()

config_data = re.sub(r'VIP_MAX_M5_PCT = 0\.80', 'VIP_MAX_M5_PCT = 0.45', config_data)
with open("config.py", "w") as f:
    f.write(config_data)


# 2. Update analyzer.py to actually skip social checks for VIP, and use config for min m5
with open("analyzer.py", "r") as f:
    analyzer_data = f.read()

# Make VIP skip clones and socials
analyzer_data = analyzer_data.replace(
    'if await self.is_clone(symbol, mint, current_created_at, current_fdv):',
    'if not is_vip and await self.is_clone(symbol, mint, current_created_at, current_fdv):'
)

analyzer_data = analyzer_data.replace(
    'if not (has_twitter or has_tg or has_website):',
    'if not is_vip and not (has_twitter or has_tg or has_website):'
)

# Fix hardcoded 10.0 to use VIP_MIN_M5_PCT
analyzer_data = analyzer_data.replace(
    'if _m5 < 10.0:',
    'if _m5 < getattr(config, "VIP_MIN_M5_PCT", 5.0):'
)

with open("analyzer.py", "w") as f:
    f.write(analyzer_data)

