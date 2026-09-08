from tracker import PaperTracker
import json

with open("paper_portfolio.json", "w") as f:
    json.dump({
        "OLD_MINT": {
            "symbol": "OLD",
            "mint": "OLD_MINT",
            "entry_price_usd": 1.0,
            "amount_usd": 10.0,
            "entry_time": 1000.0
        }
    }, f)

tracker = PaperTracker()
print("Positions loaded:", len(tracker.positions))
