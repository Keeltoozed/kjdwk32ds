import re
with open("analyzer.py", "r") as f:
    code = f.read()

# The old jito check block:
old_jito = """                    # Защита от Jito-бандлов (Sybil-атаки):
                    # Скаммеры часто раскидывают примерно одинаковые суммы по свежим кошелькам (рандомизируя копейки).
                    # Округляем до миллионов (-6), чтобы поймать кластер кошельков с одинаковой долей.
                    if len(top_10_amounts) >= 3:
                        rounded_amounts = [round(amt, -6) for amt in top_10_amounts if amt > 1000000]
                        if rounded_amounts:
                            from collections import Counter
                            counts = Counter(rounded_amounts)
                            if counts.most_common(1)[0][1] >= 3:
                                print(f"🚫 Мусор: Обнаружен Jito-бандл (Sybil attack) у {mint}.")
                                return False"""

code = code.replace(old_jito, "")
with open("analyzer.py", "w") as f:
    f.write(code)
