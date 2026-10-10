import re
with open("market_data.py", "r") as f:
    content = f.read()

content = content.replace(
    'if status != 429:\n        print(f"🔎 GT fail',
    'if status != 429:\n            print(f"🔎 GT fail'
)

with open("market_data.py", "w") as f:
    f.write(content)
