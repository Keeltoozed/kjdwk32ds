import os
from supabase import create_client
import config

supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
resp = supabase.table("trades_pump").select("mint").like("mint", "PORTFOLIO_STATE%").execute()
print([r["mint"] for r in resp.data])
