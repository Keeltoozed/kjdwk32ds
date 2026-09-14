import json

log_path = "/Users/taya/.gemini/antigravity/brain/d56580b8-bab3-4066-bdf2-e531ed910224/.system_generated/logs/transcript_full.jsonl"
for line in reversed(open(log_path).readlines()):
    data = json.loads(line)
    if data.get("type") == "USER_INPUT":
        content = data.get("content", "")
        if "minute,mint,price_usd" in content:
            idx = content.find("minute,mint,price_usd")
            csv_str = content[idx:]
            with open('dune_data.csv', 'w') as f:
                f.write(csv_str)
            print(f"CSV saved! Lines: {len(csv_str.splitlines())}")
            break
