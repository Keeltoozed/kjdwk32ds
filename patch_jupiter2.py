import re

with open('jupiter.py', 'r') as f:
    lines = f.readlines()

new_lines = []
in_session = False
for line in lines:
    if "async with aiohttp.ClientSession() as session:" in line:
        new_lines.append(line.replace("async with aiohttp.ClientSession() as session:", "session = await get_session()"))
        in_session = True
    elif in_session and line.startswith("        "):
        # unindent 4 spaces
        new_lines.append(line[4:])
    else:
        new_lines.append(line)
        in_session = False

with open('jupiter.py', 'w') as f:
    f.writelines(new_lines)

