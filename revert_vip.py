import re

with open("analyzer.py", "r") as f:
    analyzer_data = f.read()

# Revert clone check
analyzer_data = analyzer_data.replace(
    'if not is_vip and await self.is_clone(symbol, mint, current_created_at, current_fdv):',
    'if await self.is_clone(symbol, mint, current_created_at, current_fdv):'
)

# Revert social check
analyzer_data = analyzer_data.replace(
    'if not is_vip and not (has_twitter or has_tg or has_website):',
    'if not (has_twitter or has_tg or has_website):'
)

with open("analyzer.py", "w") as f:
    f.write(analyzer_data)
