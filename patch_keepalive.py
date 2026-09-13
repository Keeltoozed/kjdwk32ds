import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''        print(f"✅ Фиктивный сервер запущен на порту {port} для Render Health Check")
        await async_main()'''

new_logic = '''        print(f"✅ Фиктивный сервер запущен на порту {port} для Render Health Check")
        
        # Keep-Alive задача, чтобы Render не засыпал
        async def keep_alive():
            import aiohttp
            url = os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}")
            print(f"🔄 Keep-Alive URL установлен на: {url}")
            async with aiohttp.ClientSession() as session:
                while True:
                    await asyncio.sleep(600)  # Каждые 10 минут
                    try:
                        async with session.get(url) as resp:
                            print(f"💓 Keep-Alive Ping: {resp.status}")
                    except Exception as e:
                        print(f"⚠️ Keep-Alive Ping Error: {e}")
                        
        asyncio.create_task(keep_alive())
        
        await async_main()'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
