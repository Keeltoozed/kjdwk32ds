import re

with open("http_client.py", "r") as f:
    content = f.read()

content = content.replace(
    'print(f"🔌 API fail {host}{urlparse(url).path[:50]}: {last_err} (status {last_status})")',
    'if last_status != 429:\n        print(f"🔌 API fail {host}{urlparse(url).path[:50]}: {last_err} (status {last_status})")'
)
with open("http_client.py", "w") as f:
    f.write(content)

with open("market_data.py", "r") as f:
    content = f.read()

content = content.replace(
    'print(f"🔎 GT fail {path.split(\'?\')[0][:60]}: {last_err}")',
    'if status != 429:\n        print(f"🔎 GT fail {path.split(\'?\')[0][:60]}: {last_err}")'
)
with open("market_data.py", "w") as f:
    f.write(content)

