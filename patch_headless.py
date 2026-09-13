import re

with open('main.py', 'r') as f:
    content = f.read()

# Мы ищем '# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ==='
old_logic = '''# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ==='''

new_logic = '''import os
# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===
if os.environ.get("RENDER"):
    print("🚀 Запуск на сервере Render. Веб-интерфейс Streamlit отключен из-за нехватки ОЗУ (512MB).")
    
    from aiohttp import web
    async def health_check(request):
        return web.Response(text="Bot is running! Web dashboard is disabled on free tier to save RAM.")
        
    async def start_render_bot():
        app = web.Application()
        app.router.add_get('/', health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 10000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        try:
            await site.start()
            print(f"✅ Упрощенный веб-сервер запущен на порту {port}")
        except OSError as e:
            print(f"⚠️ Ошибка порта: {e}")
            
        await async_main()
        
    asyncio.run(start_render_bot())
    import sys
    sys.exit(0)
'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
