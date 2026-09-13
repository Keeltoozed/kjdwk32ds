import re

with open('config.py', 'r') as f:
    content = f.read()

content = content.replace('USE_JITO_EXECUTION = False', 'USE_JITO_EXECUTION = True')

with open('config.py', 'w') as f:
    f.write(content)
