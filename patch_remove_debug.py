import re

with open('main.py', 'r') as f:
    content = f.read()

content = content.replace('print(f"DEBUG: {position.symbol} Jupiter Price: {current_price}")\n', '')

with open('main.py', 'w') as f:
    f.write(content)
