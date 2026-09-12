import json
import pandas as pd
import xgboost as xgb
import os

base_path = "/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/.user_uploaded/"
files = ["media_1789224459513.json", "media_1789224464194.json", "media_1789224466509.json"]

all_data = []

# Parse rejected tokens
with open(base_path + files[0], "r") as f:
    rejected = json.load(f)
    print("Parsing rejected:", len(rejected))
    for r in rejected:
        if r.get("hypothetical_pnl", 0) > 100: # Missed rocket! target = 1
            try:
                feat = json.loads(r["features"]) if isinstance(r["features"], str) else r["features"]
                if feat and "volume_h24" in feat:
                    feat["target"] = 1
                    all_data.append(feat)
            except: pass
        elif r.get("hypothetical_pnl", 0) < -30: # Correctly rejected! target = 0
            try:
                feat = json.loads(r["features"]) if isinstance(r["features"], str) else r["features"]
                if feat and "volume_h24" in feat:
                    feat["target"] = 0
                    all_data.append(feat)
            except: pass

# Parse actual trades
for file in files[1:]:
    with open(base_path + file, "r") as f:
        trades = json.load(f)
        print("Parsing trades from", file, ":", len(trades))
        for t in trades:
            if t.get("pnl") is not None:
                pnl = t["pnl"]
                if pnl > 50: # Good trade
                    try:
                        feat = json.loads(t["features"]) if isinstance(t["features"], str) else t["features"]
                        if feat and "volume_h24" in feat:
                            feat["target"] = 1
                            all_data.append(feat)
                    except: pass
                elif pnl < -30 or t.get("exit_reason") == "Rug Pull / No Liquidity": # Bad trade
                    try:
                        feat = json.loads(t["features"]) if isinstance(t["features"], str) else t["features"]
                        if feat and "volume_h24" in feat:
                            feat["target"] = 0
                            all_data.append(feat)
                    except: pass

print(f"Total new samples to add: {len(all_data)}")

if len(all_data) == 0:
    print("No valid data found.")
    exit(1)

df = pd.DataFrame(all_data)
features = ['price_change_h24', 'volume_h24', 'buys_h24', 'sells_h24', 'liquidity', 'fdv', 'buy_sell_ratio', 'vol_to_liq']

X = df[features]
y = df['target'].astype(int)

print("Target distribution:")
print(y.value_counts())

model_path = "raydium_model_dex.json"

if os.path.exists(model_path):
    print("Continuing training on existing model...")
    # For xgboost, we can load model and train further, but XGBClassifier API might need a booster
    # Actually, the simplest way in sklearn API is to pass xgb_model=model_path in fit
    model = xgb.XGBClassifier()
    model.load_model(model_path)
    
    # Train further
    # Note: using xgb_model continues training (adds more trees)
    model.fit(X, y, xgb_model=model_path)
    
    model.save_model(model_path)
    print("✅ Model updated and saved!")
else:
    print("Model not found!")
    
