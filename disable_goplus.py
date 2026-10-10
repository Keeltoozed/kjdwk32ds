import re
with open("config.py", "r") as f:
    content = f.read()

content = re.sub(r'GOPLUS_ENABLED = True', 'GOPLUS_ENABLED = False  # Отключено: Cloudflare блокирует сервера Render', content)

with open("config.py", "w") as f:
    f.write(content)
