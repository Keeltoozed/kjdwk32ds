import config
from supabase import create_client

supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
resp = supabase.table("trades_pump").select("*").order("id", desc=True).limit(5).execute()
for row in resp.data:
    print(row['id'], row['mint'], row['status'], row['pnl'])
