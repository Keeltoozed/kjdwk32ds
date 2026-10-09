import re

with open("analyzer.py", "r") as f:
    content = f.read()

# Replace the simple session.get for goplus with randomized headers
old_goplus = r'goplus_url = f"https://api\.gopluslabs\.io/api/v1/token_security/\{cid\}\?contract_addresses=\{address\}"\n\s*async with session\.get\(goplus_url, timeout=5\) as resp:'

new_goplus = """goplus_url = f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={address}"
                import random
                fake_ip = f"{random.randint(11,250)}.{random.randint(11,250)}.{random.randint(11,250)}.{random.randint(11,250)}"
                gp_headers = {
                    "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/{random.randint(110,122)}.0.0.0 Safari/537.36",
                    "X-Forwarded-For": fake_ip,
                    "X-Real-IP": fake_ip,
                    "Accept": "application/json"
                }
                async with session.get(goplus_url, headers=gp_headers, timeout=5) as resp:"""

content = re.sub(old_goplus, new_goplus, content)

with open("analyzer.py", "w") as f:
    f.write(content)
