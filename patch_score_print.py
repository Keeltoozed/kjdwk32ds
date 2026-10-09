import re
with open("analyzer.py", "r") as f:
    content = f.read()

content = re.sub(
    r'print\(f"🔵 \[\{tag\}\] \{symbol\}: m5 \{m5:\+\.1f\}% b/s \{b\}/\{s\} liq \$\{liq:,\.0f\} → score \{score:\.0f\}"\)',
    r'print(f"📊 [{tag}] {symbol}: финальный score {score:.1f} (порог 60), m5 {m5:+.1f}%, m1 {m1:+.1f}%")',
    content
)

with open("analyzer.py", "w") as f:
    f.write(content)
