import re

with open('main.py', 'r') as f:
    content = f.read()

# Если мы запускаемся на сервере Render (где нет графического интерфейса), 
# лучше вообще отключить Streamlit, чтобы он не жрал память и порты.

old_logic = '''# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===
st.set_page_config(page_title="PhantBot Dashboard", layout="wide")'''

new_logic = '''import os
# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===
if os.environ.get("RENDER"):
    print("🚀 Запуск на сервере Render. Веб-интерфейс отключен для экономии памяти.")
    asyncio.run(async_main())
    import sys
    sys.exit(0)

st.set_page_config(page_title="PhantBot Dashboard", layout="wide")'''

content = content.replace(old_logic, new_logic)

with open('main.py', 'w') as f:
    f.write(content)
