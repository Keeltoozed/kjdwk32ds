import re
with open("analyzer.py", "r") as f:
    content = f.read()

old_fetch = r'url = f"\{config\.DEXSCREENER_SEARCH\}\{mint\}"\n\s*from http_client import fetch_json\n\s*headers = \{"User-Agent": "Mozilla/5\.0 \(Windows NT 10\.0; Win64; x64\) Chrome/120\.0\.0\.0"\}\n\s*if True:\n\s*try:\n\s*status, data = await fetch_json\(url, headers=headers, timeout=8, retries=1\)\n\s*if status == 200 and data:'

new_fetch = r'data = await self._fetch_dex_search(mint)\n        if True:\n            try:\n                if data:'

content = re.sub(old_fetch, new_fetch, content)

with open("analyzer.py", "w") as f:
    f.write(content)
