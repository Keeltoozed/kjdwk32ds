import re

with open('main.py', 'r') as f:
    content = f.read()

# Мы ищем ВСЁ от "import os" до "sys.exit(0)" перед st.set_page_config
pattern = r'(import os\n)+# === 2\. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===.*?sys\.exit\(0\)\n\n\n?(if os\.environ\.get\("RENDER"\):.*?sys\.exit\(0\)\n\n)?'
content = re.sub(pattern, '# === 2. ВЕБ-ИНТЕРФЕЙС STREAMLIT ===\n', content, flags=re.DOTALL)

with open('main.py', 'w') as f:
    f.write(content)
