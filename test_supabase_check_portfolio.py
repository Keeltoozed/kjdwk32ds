import config
from supabase import create_client

supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
resp = supabase.table("trades_pump").select("mint, status, pnl").eq("mint", "PORTFOLIO_STATE").execute()
print("PORTFOLIO_STATE rows:", resp.data)

resp2 = supabase.table("trades_pump").select("mint, status, pnl").eq("status", "CLOSED").order("id", desc=True).limit(5).execute()
print("CLOSED trades:", resp2.data)
