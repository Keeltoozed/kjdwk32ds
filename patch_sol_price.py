import re

with open('sol_price.py', 'r') as f:
    content = f.read()

content = content.replace('{"Accept": "application/json"}', '{"Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}')

with open('sol_price.py', 'w') as f:
    f.write(content)
