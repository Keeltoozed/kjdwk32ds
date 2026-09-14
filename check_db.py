import config
from supabase import create_client
supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
resp = supabase.table("trades_pump").select("*").limit(3).execute()
import json
print(json.dumps(resp.data, indent=2))
