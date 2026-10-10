import asyncio
import aiohttp

async def test_fallback():
    address = "0xdac17f958d2ee523a2206206994597c13d831ec7" # USDT on ETH (safe)
    cid = "1"
    symbol = "USDT"
    
    async with aiohttp.ClientSession() as session:
        # Mock GoPlus failure by using a dead URL
        goplus_url = "http://localhost:12345/fail"
        try:
            async with session.get(goplus_url, timeout=1.5) as resp:
                pass
        except Exception as e:
            print(f"GoPlus failed as expected: {type(e).__name__}")
            # Fallback block
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
                        print(f"✅ [HONEYPOT.IS] {symbol} проверен успешно! Налог {sell_tax}%, Honeypot: {is_hp}")
                        return True
                    else:
                        print(f"⚠️ Ошибка Антискама (GoPlus + Honeypot.is): HTTP {hp_resp.status}")
            except Exception as hp_e:
                print(f"⚠️ Ошибка Антискама (GoPlus + Honeypot.is): {hp_e}")

asyncio.run(test_fallback())
