import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase = create_client(url, key)

print("Checking trades_pump:")
res = supabase.table("trades_pump").select("*").limit(5).execute()
print(res.data)

print("\nChecking trades_raydium:")
res2 = supabase.table("trades_raydium").select("*").limit(5).execute()
print(res2.data)
