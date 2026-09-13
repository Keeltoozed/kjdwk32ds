import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''async def async_main():
    from pump_fun_sniper import PumpFunSniper'''

new_logic = '''async def async_main():
    from pump_fun_sniper import PumpFunSniper
    
    # Keep-Alive задача, чтобы Render не засыпал (работает в фоне)
    async def keep_alive():
        import aiohttp, os
        port = int(os.environ.get("PORT", 10000))
        url = os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}")
        print(f"🔄 Keep-Alive URL: {url}")
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(600)  # Каждые 10 минут
                try:
                    async with session.get(url) as resp:
                        print(f"💓 Keep-Alive Ping: {resp.status}")
                except Exception as e:
                    pass
    import asyncio
    asyncio.create_task(keep_alive())
'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
