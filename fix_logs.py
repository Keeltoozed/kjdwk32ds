import re

# 1. Remove GoPlus log
with open("analyzer.py", "r") as f:
    analyzer = f.read()

analyzer = re.sub(r'print\(f"⚠️ Ошибка GoPlus API для .*"\)', 'pass', analyzer)
with open("analyzer.py", "w") as f:
    f.write(analyzer)

# 2. Suppress GT fail in market_data.py
with open("market_data.py", "r") as f:
    md = f.read()

md = re.sub(r'print\(f"🔎 GT fail.*HTTP 429"\)', 'pass', md)
with open("market_data.py", "w") as f:
    f.write(md)

# 3. Suppress API fail 429 in http_client.py
with open("http_client.py", "r") as f:
    hc = f.read()

hc = hc.replace(
    'print(f"🔌 API fail {host}{urlparse(url).path[:50]}: {last_err} (status {last_status})")',
    'if last_status != 429:\n            print(f"🔌 API fail {host}{urlparse(url).path[:50]}: {last_err} (status {last_status})")'
)
with open("http_client.py", "w") as f:
    f.write(hc)
