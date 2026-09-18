with open("analyzer.py", "r") as f:
    code = f.read()

old_gecko = """    async def fetch_token_data_gecko(self, mint: str) -> dict:
        session = await self.get_session()
        try:"""

new_gecko = """    async def fetch_token_data_gecko(self, mint: str) -> dict:
        import asyncio
        await asyncio.sleep(2)  # Жесткий лимит: не спамить GeckoTerminal (макс 30/мин)
        session = await self.get_session()
        try:"""

code = code.replace(old_gecko, new_gecko)
with open("analyzer.py", "w") as f:
    f.write(code)
