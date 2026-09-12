import re

with open('main.py', 'r') as f:
    content = f.read()

# Изменяем стоп-лосс с -20% на -25%
content = content.replace("pnl_pct <= -0.20:", "pnl_pct <= -0.25:")
content = content.replace("Hard Stop Loss (-20%)", "Hard Stop Loss (-25%)")

with open('main.py', 'w') as f:
    f.write(content)
