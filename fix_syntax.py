import re
with open("analyzer.py", "r") as f:
    content = f.read()

content = content.replace("return {}    async def _fetch_dex_search", "return {}\n\n    async def _fetch_dex_search")

with open("analyzer.py", "w") as f:
    f.write(content)
