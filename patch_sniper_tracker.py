import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

content = content.replace("tracker = PaperTracker()", "tracker = self.tracker if self.tracker else PaperTracker()")

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
