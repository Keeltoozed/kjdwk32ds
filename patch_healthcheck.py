import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''if os.environ.get("RENDER"):
    print("🚀 Запуск на сервере Render. Веб-интерфейс отключен для экономии памяти.")
    asyncio.run(async_main())
    import sys
    sys.exit(0)'''

new_logic = '''if os.environ.get("RENDER"):
    print("🚀 Запуск на сервере Render. Веб-интерфейс Streamlit отключен.")
    
    # Запускаем фиктивный веб-сервер, чтобы Render не убивал бота (Health Check)
    from aiohttp import web
    async def health_check(request):
        return web.Response(text="Bot is running!")
        
    async def start_render_bot():
        app = web.Application()
        app.router.add_get('/', health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 10000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Фиктивный сервер запущен на порту {port} для Render Health Check")
        await async_main()
        
    asyncio.run(start_render_bot())
    import sys
    sys.exit(0)'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
