import re

with open('ai_brain.py', 'r') as f:
    content = f.read()

# Изменяем порог Pump.fun ML на 60%
content = content.replace("score >= 85:  # Усилено для защиты от минусов", "score >= 60:")
content = content.replace("Уверенность повышена до 85%", "Порог пользователя 60%")

# Изменяем порог PRO ML на 60%
content = content.replace("score >= 75:", "score >= 60:")

with open('ai_brain.py', 'w') as f:
    f.write(content)
