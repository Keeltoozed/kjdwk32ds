import re

with open('analyzer.py', 'r') as f:
    content = f.read()

old_logic = '''    async def fetch_token_data(self, mint: str) -> dict:
        url = f"{config.DEXSCREENER_SEARCH}{mint}"
        session = await self.get_session()
        if True:
            try:
                async with session.get(url, timeout=10) as response:'''

new_logic = '''    async def fetch_token_data(self, mint: str) -> dict:
        url = f"{config.DEXSCREENER_SEARCH}{mint}"
        session = await self.get_session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if True:
            try:
                async with session.get(url, headers=headers, timeout=10) as response:'''

content = content.replace(old_logic, new_logic)

with open('analyzer.py', 'w') as f:
    f.write(content)
