import re

with open('main.py', 'r') as f:
    content = f.read()

# Уменьшаем хард стоп-лосс с -35% до -20%
content = content.replace("pnl_pct <= -0.35:", "pnl_pct <= -0.20:")
content = content.replace("Hard Stop Loss (-35%)", "Hard Stop Loss (-20%)")

with open('main.py', 'w') as f:
    f.write(content)
