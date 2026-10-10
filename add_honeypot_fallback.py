import re

with open("analyzer.py", "r") as f:
    content = f.read()

fallback_code = """            except Exception as e:
                # Fallback on honeypot.is if GoPlus fails
                try:
                    hp_url = f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID={cid}"
                    async with session.get(hp_url, timeout=2.5) as hp_resp:
                        if hp_resp.status == 200:
                            hp_data = await hp_resp.json()
                            is_hp = hp_data.get("honeypotResult", {}).get("isHoneypot", False)
                            if is_hp:
                                print(f"🚫 [HONEYPOT.IS] {symbol} — Это Honeypot! Блокируем.")
                                return False
                            sell_tax = float(hp_data.get("simulationResult", {}).get("sellTax", 0))
                            if sell_tax > 10.0:
                                print(f"🚫 [HONEYPOT.IS] {symbol} — Скрытый налог {sell_tax}% (>10%). Блокируем.")
                                return False
                        else:
                            print(f"⚠️ Ошибка Антискама (GoPlus + Honeypot.is) для {address[:8]}: {e} (Таймаут обеих систем, пропускаем)")
                except Exception as hp_e:
                    print(f"⚠️ Ошибка Антискама (GoPlus + Honeypot.is) для {address[:8]}: {e} (Таймаут обеих систем, пропускаем)")"""

content = re.sub(r'            except Exception as e:\n                print\(f"⚠️ Ошибка GoPlus API.*? пропускаем\)"\)', fallback_code, content, flags=re.DOTALL)

with open("analyzer.py", "w") as f:
    f.write(content)
