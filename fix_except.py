import re
with open("analyzer.py", "r") as f:
    content = f.read()

content = content.replace("            except Exception as e:\n                pass", "")

with open("analyzer.py", "w") as f:
    f.write(content)
