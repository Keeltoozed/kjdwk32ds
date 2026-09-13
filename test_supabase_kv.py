import json
import config
from supabase import create_client, Client

def test():
    supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    
    test_data = {"test_key": "test_value"}
    
    try:
        # Save
        supabase.table("trades_pump").upsert({
            "mint": "PORTFOLIO_STATE",
            "features": json.dumps(test_data),
            "confidence": 0,
            "status": "SYSTEM"
        }).execute()
        
        # Load
        res = supabase.table("trades_pump").select("features").eq("mint", "PORTFOLIO_STATE").execute()
        if res.data:
            loaded = json.loads(res.data[0]["features"])
            print("Loaded:", loaded)
            return True
    except Exception as e:
        print("Error:", e)
        return False
        
print("Success:", test())
