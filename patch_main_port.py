import re

with open('main.py', 'r') as f:
    content = f.read()

old_logic = '''        port = int(os.environ.get("PORT", 10000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Фиктивный сервер запущен на порту {port} для Render Health Check")'''

new_logic = '''        port = int(os.environ.get("PORT", 10000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        try:
            await site.start()
            print(f"✅ Фиктивный сервер запущен на порту {port} для Render Health Check")
        except OSError as e:
            if e.errno == 98:
                print(f"⚠️ Порт {port} уже занят (вероятно, Streamlit уже запущен). Пропускаем запуск фиктивного сервера.")
            else:
                raise'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
