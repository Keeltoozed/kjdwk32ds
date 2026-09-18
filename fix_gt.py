with open("market_data.py", "r") as f:
    code = f.read()

code = code.replace("for page in range(1, 6):", "for page in range(1, 3):")

with open("market_data.py", "w") as f:
    f.write(code)
