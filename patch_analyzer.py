import re

with open('analyzer.py', 'r') as f:
    content = f.read()

# Add get_session
session_func = '''class Analyzer:
    def __init__(self):
        self.session = None
        
    async def get_session(self):
        import aiohttp
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session
'''
content = re.sub(r'class Analyzer:', session_func, content, count=1)

# Now, replace ALL `async with aiohttp.ClientSession() as session:` 
# with `session = await self.get_session()` and dedent the block.
# Since dedenting via regex is hard, I will write a small python parser.
