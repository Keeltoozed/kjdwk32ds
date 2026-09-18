import re
with open("analyzer.py", "r") as f:
    code = f.read()

code = code.replace("connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)", 
                    "connector = aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300, use_dns_cache=True)")

with open("analyzer.py", "w") as f:
    f.write(code)

with open("fomo_scanner.py", "r") as f:
    code = f.read()

code = code.replace("connector=aiohttp.TCPConnector(limit=100, limit_per_host=30)", 
                    "connector=aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300, use_dns_cache=True)")

with open("fomo_scanner.py", "w") as f:
    f.write(code)
