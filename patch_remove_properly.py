import re

with open('main.py', 'r') as f:
    content = f.read()

# 1. Удаляем весь if os.environ.get("RENDER"):
# Мы ищем от "import os" до "st.set_page_config"
pattern = r'import os\n# === 2\. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===.*?sys\.exit\(0\)'
content = re.sub(pattern, '# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===', content, flags=re.DOTALL)

with open('main.py', 'w') as f:
    f.write(content)
