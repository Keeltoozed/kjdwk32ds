import config
from supabase import create_client
import json

supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
resp = supabase.table("trades_pump").select("mint, pnl, features, post_exit_ath").eq("status", "CLOSED").limit(5).execute()
print(json.dumps(resp.data, indent=2))
