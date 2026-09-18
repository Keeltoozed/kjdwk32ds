import re
with open("analyzer.py", "r") as f:
    code = f.read()

# We want to extract the entire Helius block (from Mint Authority to end of Jito)
helius_start = "# 🔴 ГЛОБАЛЬНЫЙ АНТИСКАМ БЛОК: MINT + FREEZE AUTHORITY"
helius_end = "print(f\"⚠️ Ошибка Jito-bundle: {e}\")\n            return False\n"
start_idx = code.find(helius_start)
end_idx = code.find(helius_end) + len(helius_end)

if start_idx != -1 and end_idx != -1:
    helius_block = code[start_idx:end_idx]
    
    # Remove it from current location
    # Note: there's a comment right above it: "# ══════════════════════════════════════════════════════"
    # We will just remove helius_block
    new_code = code[:start_idx] + code[end_idx:]
    
    # Find where to insert it. Let's insert it right before VIP Fast Track bypass
    # which is around: "if is_vip or _lottery:"
    target_str = "if is_vip or _lottery:\n            print(f\"🚀 [FAST TRACK] {symbol}: гейты пройдены, передаем на проверку холдеров"
    target_idx = new_code.find(target_str)
    
    if target_idx != -1:
        new_code = new_code[:target_idx] + helius_block + "\n        " + new_code[target_idx:]
        with open("analyzer.py", "w") as f:
            f.write(new_code)
        print("Success!")
    else:
        print("Target not found.")
else:
    print("Helius block not found.")
