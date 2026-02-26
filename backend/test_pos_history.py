import ccxt
import os
import json
from dotenv import load_dotenv

load_dotenv()
exchange = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET_KEY'),
    'password': os.getenv('OKX_PASSWORD'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})
exchange.set_sandbox_mode(True)

print("Has fetch_positions_history: ", exchange.has.get('fetchPositionsHistory'))
try:
    history = exchange.fetch_positions_history('BTC/USDT:USDT', limit=5)
    print("Found history:", len(history))
    if history:
        print(json.dumps(history[0], indent=2))
except Exception as e:
    print("Error:", e)
