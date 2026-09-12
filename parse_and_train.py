import json
import pandas as pd
import xgboost as xgb

with open("/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/.system_generated/logs/transcript.jsonl", "r") as f:
    lines = f.readlines()

user_texts = []
for line in lines:
    try:
        data = json.loads(line)
        if data.get("type") == "USER_INPUT":
            user_texts.append(data.get("content", ""))
    except:
        pass

last_msg = user_texts[-1]

# Extract JSON arrays from last_msg
import re
json_arrays = re.findall(r'\[.*?\]', last_msg, re.DOTALL)

rejected = []
trades = []

for j in json_arrays:
    try:
        parsed = json.loads(j)
        if isinstance(parsed, list) and len(parsed) > 0:
            if "rejected_at" in parsed[0]:
                rejected = parsed
            elif "entry_time" in parsed[0]:
                trades = parsed
    except:
        pass

print(f"Found {len(rejected)} rejected tokens")
print(f"Found {len(trades)} trades")

with open("rejected.json", "w") as f:
    json.dump(rejected, f)
    
with open("trades.json", "w") as f:
    json.dump(trades, f)
