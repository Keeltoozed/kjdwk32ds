import re

with open("analyzer.py", "r") as f:
    content = f.read()

# Add _clone_cache to Analyzer class if not exists
if "_clone_cache" not in content:
    content = re.sub(
        r'class Analyzer:\n\s*def __init__\(self\):',
        r'class Analyzer:\n    def __init__(self):\n        self._clone_cache = {}\n        self._clone_cache_time = {}',
        content
    )

# Patch is_clone to use cache
old_is_clone_start = r'    async def is_clone\(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float\) -> bool:\n\s*"""Проверяет.*"""\n\s*if not symbol or len\(symbol\) <= 2:\n\s*return False'

new_is_clone_start = """    async def is_clone(self, symbol: str, current_mint: str, current_created_at: int, current_fdv: float) -> bool:
        \"\"\"Проверяет, является ли этот токен дешевой копией (клоном) более старого или крупного оригинала.\"\"\"
        if not symbol or len(symbol) <= 2:
            return False
            
        import time
        now = time.time()
        cache_key = symbol.upper()
        if cache_key in getattr(self, '_clone_cache', {}) and now - self._clone_cache_time.get(cache_key, 0) < 600:
            return self._clone_cache[cache_key]"""

content = re.sub(old_is_clone_start, new_is_clone_start, content)

# Need to cache the result before returning True or False
# Since there are multiple returns in is_clone, we can't easily patch all of them via regex.
# Actually we can just rename the old is_clone to _is_clone_internal and create a wrapper!
