import requests
try:
    res = requests.get("https://api.solanatracker.io/tokens/trending", timeout=5)
    print(res.status_code)
    print(res.text[:200])
except Exception as e:
    print(e)
