import re
with open("analyzer.py", "r") as f:
    code = f.read()

# Replace the fallback_rpcs list
old_fallbacks = """        fallback_rpcs = [
            
            "https://solana-rpc.publicnode.com",
            "https://api.mainnet-beta.solana.com"
        ]"""

new_fallbacks = """        fallback_rpcs = [
            "https://api.mainnet-beta.solana.com",
            "https://solana-api.projectserum.com",
            "https://rpc.solscan.com",
            "https://free.rpcpool.com",
            "https://api.mainnet.solana.com",
            "https://solana-rpc.publicnode.com"
        ]"""

code = code.replace(old_fallbacks, new_fallbacks)
with open("analyzer.py", "w") as f:
    f.write(code)
