import config
from supabase import create_client

supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
resp = supabase.table("trades_pump").select("mint, status, pnl").execute()
print("Trades in trades_pump:", len(resp.data))
for row in resp.data[-5:]:
    print(row)
