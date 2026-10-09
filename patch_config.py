import re

with open("config.py", "r") as f:
    content = f.read()

# Update parameters
content = re.sub(r'EVM_MIN_M5_PCT = 7\.0', 'EVM_MIN_M5_PCT = 2.5', content)
content = re.sub(r'PULLBACK_MIN_M5_PCT = 0\.03', 'PULLBACK_MIN_M5_PCT = 0.02', content)
content = re.sub(r'VIP_MAX_M5_PCT = 0\.40', 'VIP_MAX_M5_PCT = 0.80', content)
content = re.sub(r'VIP_MIN_M5_PCT = 10\.0', 'VIP_MIN_M5_PCT = 5.0', content)
content = re.sub(r'VIP_MIN_TX_M5 = 100', 'VIP_MIN_TX_M5 = 40', content)
content = re.sub(r'VIP_MIN_VOL_M5 = 50000', 'VIP_MIN_VOL_M5 = 15000', content)

# Handle cases where the variables might not exist exactly like that or have different defaults
# Actually let's just make sure they are set if not present.
import ast
class ConfigModifier:
    def __init__(self, filename):
        self.filename = filename
        with open(filename, 'r') as file:
            self.lines = file.readlines()
            
    def set_var(self, name, value, comment=""):
        for i, line in enumerate(self.lines):
            if line.startswith(f"{name} =") or line.startswith(f"{name}="):
                self.lines[i] = f"{name} = {value}  # {comment}\n"
                return
        self.lines.append(f"{name} = {value}  # {comment}\n")

    def save(self):
        with open(self.filename, 'w') as file:
            file.writelines(self.lines)

mod = ConfigModifier("config.py")
mod.set_var("EVM_MIN_M5_PCT", "2.5", "Снижено с 7.0 по совету DeepSeek (иначе режет всё)")
mod.set_var("PULLBACK_MIN_M5_PCT", "0.02", "Снижено с 0.03")
mod.set_var("VIP_MAX_M5_PCT", "0.80", "Расширен коридор для VIP входов")
mod.set_var("VIP_MIN_M5_PCT", "5.0", "Расширен коридор для VIP входов")
mod.set_var("VIP_MIN_TX_M5", "40", "Меньше требований к транзам для VIP")
mod.set_var("VIP_MIN_VOL_M5", "15000", "Меньше требований к объему для VIP")
mod.save()

print("config.py patched")
