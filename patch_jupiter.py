import re

with open('jupiter.py', 'r') as f:
    content = f.read()

# Add import
if 'from http_client import get_session' not in content:
    content = content.replace('import aiohttp\n', 'import aiohttp\nfrom http_client import get_session\n')

# Replace async with aiohttp.ClientSession() as session:
content = re.sub(r'async with aiohttp\.ClientSession\(\) as session:', r'session = await get_session()', content)

# Adjust indentation of the try/except block if needed. Actually we can just leave it since Python allows extra indent.
# Wait, replacing `async with` changes indentation block.
# If I replace `async with ... as session:` with `session = await get_session()`, the next lines are indented 4 spaces too much. Python doesn't mind as long as it's consistent.

with open('jupiter.py', 'w') as f:
    f.write(content)
