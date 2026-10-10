import re
with open("analyzer.py", "r") as f:
    content = f.read()

content = content.replace('sell_tax = float(hp_data.get("simulationResult", {}).get("sellTax", 0))',
                          'sim_res = hp_data.get("simulationResult") or {}\n                            sell_tax = float(sim_res.get("sellTax", 0) or 0)')

with open("analyzer.py", "w") as f:
    f.write(content)
