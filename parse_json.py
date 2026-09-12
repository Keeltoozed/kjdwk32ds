import json
import pandas as pd

base_path = "/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/.user_uploaded/"
files = ["media_1789224459513.json", "media_1789224464194.json", "media_1789224466509.json"]

all_data = []

with open(base_path + files[0], "r") as f:
    rejected = json.load(f)
    print("Rejected:", len(rejected))
    for r in rejected:
        if r.get("hypothetical_pnl", 0) > 100: # Missed rocket
            try:
                feat = json.loads(r["features"]) if isinstance(r["features"], str) else r["features"]
                if feat:
                    feat["target"] = 1
                    all_data.append(feat)
            except Exception as e: pass
        elif r.get("hypothetical_pnl", 0) < -50: # Correctly rejected rug
            try:
                feat = json.loads(r["features"]) if isinstance(r["features"], str) else r["features"]
                if feat:
                    feat["target"] = 0
                    all_data.append(feat)
            except Exception as e: pass

for file in files[1:]:
    with open(base_path + file, "r") as f:
        trades = json.load(f)
        print("Trades from", file, ":", len(trades))
        for t in trades:
            if t.get("pnl") is not None:
                pnl = t["pnl"]
                if pnl > 50: # Good trade
                    try:
                        feat = json.loads(t["features"]) if isinstance(t["features"], str) else t["features"]
                        if feat:
                            feat["target"] = 1
                            all_data.append(feat)
                    except: pass
                elif pnl < -30 or t.get("exit_reason") == "Rug Pull / No Liquidity": # Bad trade
                    try:
                        feat = json.loads(t["features"]) if isinstance(t["features"], str) else t["features"]
                        if feat:
                            feat["target"] = 0
                            all_data.append(feat)
                    except: pass

print(f"Total new samples to add: {len(all_data)}")
if all_data:
    print(all_data[0])

