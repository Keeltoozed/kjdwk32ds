import re

with open('main.py', 'r') as f:
    content = f.read()

# 1. Находим и удаляем весь блок if os.environ.get("RENDER"):
pattern = r'import os\n# === 2\. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===\nif os\.environ\.get\("RENDER"\):.*?\nsys\.exit\(0\)'
content = re.sub(pattern, '# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===', content, flags=re.DOTALL)

# 2. Добавляем keep_alive прямо внутрь async_main
old_async_main = '''async def async_main():
    print("🤖 Запуск PhantBot...")
    tracker = PaperTracker()'''

new_async_main = '''async def async_main():
    print("🤖 Запуск PhantBot...")
    
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
                    
    asyncio.create_task(keep_alive())
    
    tracker = PaperTracker()'''

content = content.replace(old_async_main, new_async_main)

with open('main.py', 'w') as f:
    f.write(content)
