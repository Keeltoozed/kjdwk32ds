import re

with open("analyzer.py", "r") as f:
    content = f.read()

# Replace max_allowed_pct
content = re.sub(r'max_allowed_pct = 20\.0 if is_pump else 45\.0', r'max_allowed_pct = 20.0 if is_pump else 25.0', content)

# Replace dev_holding_pct limit
content = re.sub(r'elif dev_holding_pct > 15\.0:', r'elif dev_holding_pct > 10.0:', content)
content = re.sub(r'\(Лимит 15%\)', r'(Лимит 10%)', content)

with open("analyzer.py", "w") as f:
    f.write(content)
print("analyzer.py updated")
