import re

with open('ai_brain.py', 'r') as f:
    content = f.read()

# Увеличиваем порог Pump.fun ML
content = content.replace("score >= 70:", "score >= 85:  # Усилено для защиты от минусов")
content = content.replace("Уверенность снижена до 70%", "Уверенность повышена до 85%")

# Увеличиваем порог PRO ML
content = content.replace("score >= 50:", "score >= 75:")

with open('ai_brain.py', 'w') as f:
    f.write(content)
