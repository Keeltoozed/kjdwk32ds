import re

with open('main.py', 'r') as f:
    content = f.read()

content = content.replace("sniper = PumpFunSniper()", "sniper = PumpFunSniper(tracker)")

with open('main.py', 'w') as f:
    f.write(content)
