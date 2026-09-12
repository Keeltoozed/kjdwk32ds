import re

with open('main.py', 'r') as f:
    content = f.read()

content = content.replace("if current_price <= 0.0:", "if current_price is None or current_price <= 0.0:")

with open('main.py', 'w') as f:
    f.write(content)
