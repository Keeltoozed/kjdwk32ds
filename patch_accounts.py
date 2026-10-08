import re

with open("analyzer.py", "r") as f:
    content = f.read()

# Мы найдем блок "if bundle_data:" и заменим его на "if bundle_data and accounts:"
# Но проще сделать регуляркой.
new_code = """
            if bundle_data and "result" in bundle_data:
                accounts = bundle_data.get("result", {}).get("value", [])
                if not accounts:
                    print(f"⚠️ Ошибка RPC (пустой список аккаунтов). Блокируем вход.")
                    return False
                    
                non_curve_accounts = [float(acc["uiAmount"]) for acc in accounts[1:]] if len(accounts) > 1 else []
                top_10_amounts = non_curve_accounts[:10]
                if len(top_10_amounts) >= 3:
"""

content = re.sub(
    r'if bundle_data:\s+accounts = bundle_data\.get\("result", \{\}\)\.get\("value", \[\]\)\s+if accounts:\s+non_curve_accounts = \[float\(acc\["uiAmount"\]\) for acc in accounts\[1:\]\] if len\(accounts\) > 1 else \[\]\s+top_10_amounts = non_curve_accounts\[:10\]\s+if len\(top_10_amounts\) >= 3:',
    new_code.strip(),
    content
)

with open("analyzer.py", "w") as f:
    f.write(content)

print("Patched!")
