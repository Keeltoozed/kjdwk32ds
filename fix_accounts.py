import re

with open("analyzer.py", "r") as f:
    code = f.read()

# Replace the flawed non_curve_accounts logic
old_logic = 'non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts if float(acc["uiAmount"]) < 800_000_000]'
new_logic = 'non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts[1:]] if len(accounts) > 1 else []'

if old_logic in code:
    code = code.replace(old_logic, new_logic)
    with open("analyzer.py", "w") as f:
        f.write(code)
    print("Fixed non_curve_accounts logic!")
else:
    print("Could not find old_logic.")
