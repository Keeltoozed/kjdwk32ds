import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

response = supabase.table("trades_pump").select("*").execute()
trades = response.data

worst_trades = sorted([t for t in trades if t.get('pnl_pct', 0) < -10], key=lambda x: x['pnl_pct'])
for t in worst_trades[:5]:
    print(f"Token: {t['symbol']}, PNL: {t['pnl_pct']}%, Reason: {t.get('exit_reason')}")
