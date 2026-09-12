import re

with open('main.py', 'r') as f:
    content = f.read()

# Replace empty symbols in Open Positions
old_html_open = "<h3 style='margin:0; color: #FFF;'>{row['symbol']}</h3>"
new_html_open = "<h3 style='margin:0; color: #FFF;'>{row['symbol'] if str(row['symbol']).strip() else row['mint'][:6] + '...'}</h3>"
content = content.replace(old_html_open, new_html_open)

# Replace empty symbols in Closed Positions
old_html_closed = "<div style='color: #FFF; font-weight: bold;'>{row['symbol']}</div>"
new_html_closed = "<div style='color: #FFF; font-weight: bold;'>{row['symbol'] if str(row['symbol']).strip() else row.name[:6] + '...'}</div>"
content = content.replace(old_html_closed, new_html_closed)

with open('main.py', 'w') as f:
    f.write(content)
