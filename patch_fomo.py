import re

with open('fomo_scanner.py', 'r') as f:
    content = f.read()

# Add import
if 'from http_client import get_session' not in content:
    content = content.replace('import aiohttp\n', 'import aiohttp\nfrom http_client import get_session\n')

# Replace async with aiohttp.ClientSession() as session: with session = await get_session()
content = re.sub(r'async with aiohttp\.ClientSession\(\) as session:\s+try:\s+async with session\.get',
                 r'session = await get_session()\n    try:\n        async with session.get', content)

content = re.sub(r'async with aiohttp\.ClientSession\(\) as session:\s+for url in urls:\s+try:\s+async with session\.get',
                 r'session = await get_session()\n    for url in urls:\n        try:\n            async with session.get', content)

# Change timeout=5 to timeout=15
content = content.replace('timeout=5', 'timeout=15')

with open('fomo_scanner.py', 'w') as f:
    f.write(content)
