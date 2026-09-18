import re
with open("analyzer.py", "r") as f:
    code = f.read()

# 1. Add global Top 10 Total
old_block = """                            if counts.most_common(1)[0][1] >= 3:
                                print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака) у {mint}. Блокируем.")
                                return False
            else:
                print(f"⚠️ Не удалось проверить Jito-бандлы (RPC недоступны). Блокируем вход.")"""

new_block = """                            if counts.most_common(1)[0][1] >= 3:
                                print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака) у {mint}. Блокируем.")
                                return False
                    
                    top_10_sum_pct = (sum(top_10_amounts) / 1_000_000_000.0) * 100
                    dev_holding_pct = (top_10_amounts[0] / 1_000_000_000.0) * 100 if top_10_amounts else 0.0
                    self._last_top10 = top_10_sum_pct
                    self._last_dev = dev_holding_pct
                    
                    is_pump = pair_data and pair_data.get("dexId") == "pump"
                    max_allowed_pct = 20.0 if is_pump else 45.0
                    if top_10_sum_pct > max_allowed_pct:
                        print(f"🚫 [АНТИСКАМ] Топ-10 держат {top_10_sum_pct:.1f}% (Лимит {max_allowed_pct}%). Блокируем.")
                        return False
            else:
                print(f"⚠️ Не удалось проверить Jito-бандлы (RPC недоступны). Блокируем вход.")"""
code = code.replace(old_block, new_block)

# 2. Remove duplicate from xgboost using regex
pattern = re.compile(r'# Get holders via Helius RPC.*?funded_from_cex = 0', re.DOTALL)
new_xgboost = """top_10_holding_pct = getattr(self, '_last_top10', 0.0)
        dev_holding_pct = getattr(self, '_last_dev', 0.0)
        
        funded_from_cex = 0"""
code = pattern.sub(new_xgboost, code)

with open("analyzer.py", "w") as f:
    f.write(code)
