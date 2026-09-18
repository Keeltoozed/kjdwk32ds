import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

response = supabase.table("trades_pump").select("mint, pnl, entry_time").order("pnl", desc=True).limit(5).execute()
print("=== ЛУЧШИЕ СДЕЛКИ БОТА ЗА ВСЕ ВРЕМЯ ===")
for t in response.data:
    print(f"Time: {t['entry_time']}, Mint: {t['mint']}, PNL: {t['pnl']}%")
