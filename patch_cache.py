import re

with open("analyzer.py", "r") as f:
    content = f.read()

# Make sure we import time in Analyzer
if "def __init__(self):" in content and "self._clone_cache" not in content:
    content = content.replace("def __init__(self):", "def __init__(self):\n        self._clone_cache = {}\n        self._clone_cache_time = {}")

# Inject _fetch_dex_search
fetch_method = """    async def _fetch_dex_search(self, symbol: str):
        import time
        now = time.time()
        cache_key = symbol.upper()
        if cache_key in getattr(self, '_clone_cache', {}) and now - self._clone_cache_time.get(cache_key, 0) < 600:
            return self._clone_cache[cache_key]
            
        url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
        from http_client import fetch_json
        try:
            status, data = await fetch_json(url, timeout=8, retries=1)
            if status == 200 and data:
                if not hasattr(self, '_clone_cache'):
                    self._clone_cache = {}
                    self._clone_cache_time = {}
                self._clone_cache[cache_key] = data
                self._clone_cache_time[cache_key] = now
                return data
        except Exception:
            pass
        return None\n\n"""

# Insert right before is_clone
content = re.sub(r'(\s+async def is_clone\()', fetch_method + r'\1', content, count=1)

# Modify is_clone to use _fetch_dex_search
old_is_clone = r'url = f"https://api\.dexscreener\.com/latest/dex/search\?q=\{symbol\}"\s+from http_client import fetch_json\s+if True:\s+try:\s+status, data = await fetch_json\(url, timeout=8, retries=1\)\s+if status == 200 and data:'
new_is_clone = r'data = await self._fetch_dex_search(symbol)\n        if data:'
content = re.sub(old_is_clone, new_is_clone, content)

# Modify _evm_clone_ok to use _fetch_dex_search
old_evm_clone = r'url = f"https://api\.dexscreener\.com/latest/dex/search\?q=\{symbol\}"\s+status, data = await _fj\(url, timeout=5, retries=1\)\s+if status == 200 and data:'
new_evm_clone = r'data = await self._fetch_dex_search(symbol)\n            if data:'
content = re.sub(old_evm_clone, new_evm_clone, content)

with open("analyzer.py", "w") as f:
    f.write(content)
print("Analyzer patched with search cache!")
