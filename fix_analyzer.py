import re

with open('analyzer.py', 'r') as f:
    content = f.read()

content = content.replace("async with aiohttp.ClientSession() as session:", "session = await self.get_session()\n        if True:")

with open('analyzer.py', 'w') as f:
    f.write(content)
