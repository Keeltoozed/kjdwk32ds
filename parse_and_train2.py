import json
import pandas as pd
import xgboost as xgb
import re

with open("/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/.system_generated/logs/transcript_full.jsonl", "r") as f:
    lines = f.readlines()

user_texts = []
for line in lines:
    try:
        data = json.loads(line)
        if data.get("type") == "USER_INPUT":
            user_texts.append(data.get("content", ""))
    except:
        pass

last_msg = user_texts[-2]  # The message with the JSON is the one before "дообучи мой ии" (which is -1)

# Extract JSON arrays from last_msg
json_arrays = re.findall(r'\[\s*\{.*?\}\s*\]', last_msg, re.DOTALL)

print(f"Found {len(json_arrays)} JSON arrays")
if len(json_arrays) >= 2:
    with open("rejected.json", "w") as f:
        f.write(json_arrays[0])
    with open("trades.json", "w") as f:
        f.write(json_arrays[1])
else:
    print("Could not find both arrays. Content snippet:")
    print(last_msg[:500])

