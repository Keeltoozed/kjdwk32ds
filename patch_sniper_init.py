import re

with open('pump_fun_sniper.py', 'r') as f:
    content = f.read()

content = content.replace("def __init__(self):", "def __init__(self, tracker=None):\n        self.tracker = tracker")

content = content.replace("p_tracker = PaperTracker()", "p_tracker = self.tracker if self.tracker else PaperTracker()")

with open('pump_fun_sniper.py', 'w') as f:
    f.write(content)
