with open("fomo_scanner.py", "r") as f:
    code = f.read()

code = code.replace("await asyncio.sleep(20)", "await asyncio.sleep(60)")

with open("fomo_scanner.py", "w") as f:
    f.write(code)
