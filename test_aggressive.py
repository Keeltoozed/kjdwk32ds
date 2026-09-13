import subprocess
with open("backtest_ultimate.py", "r") as f:
    content = f.read()
# Replace starting capital
content = content.replace("capital = 20.0", "capital = 35.0")
# Replace prints
content = content.replace("Старт: $20", "Старт: $35")
content = content.replace("$   20.00", "$   35.00")
# Set TRADE_PCT to 0.15
content = content.replace("TRADE_PCT = 0.10", "TRADE_PCT = 0.15")

with open("backtest_35.py", "w") as f:
    f.write(content)
