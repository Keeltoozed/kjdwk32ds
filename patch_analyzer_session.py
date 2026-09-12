import re

with open('analyzer.py', 'r') as f:
    content = f.read()

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

def replacer(match):
    indent = match.group(1)
    return f"{indent}session = await self.get_session()\n{indent}if True:"

content = re.sub(r'^([ \t]+)async with aiohttp\.ClientSession\(\) as session:', replacer, content, flags=re.MULTILINE)

with open('analyzer.py', 'w') as f:
    f.write(content)
