import re
with open("analyzer.py", "r") as f:
    code = f.read()

# We want to insert the total sum check right after the Jito bundle block.
jito_old = """                            if counts.most_common(1)[0][1] >= 3:
                                print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака) у {mint}. Блокируем.")
                                return False"""

jito_new = """                            if counts.most_common(1)[0][1] >= 3:
                                print(f"🚫 [АНТИСКАМ] Обнаружен Jito-бандл (Сивил атака) у {mint}. Блокируем.")
                                return False
                    
                    # Защита от рандомизированных балансов:
                    # Если скаммер сделал разные суммы, они не образуют кластер.
                    # НО их суммарный объем упрется в жесткий лимит!
                    top_10_sum_pct = sum(top_10_amounts) / 10_000_000.0
                    
                    dex_id = pair_data.get("dexId", "pump") if pair_data else "pump"
                    max_allowed_pct = 45.0 if dex_id == "raydium" else 20.0
                    
                    if top_10_sum_pct > max_allowed_pct:
                        print(f"🚫 [АНТИСКАМ] Топ-10 холдеров держат {top_10_sum_pct:.1f}% (Лимит {max_allowed_pct}%). Блокируем.")
                        return False"""

code = code.replace(jito_old, jito_new)
with open("analyzer.py", "w") as f:
    f.write(code)
