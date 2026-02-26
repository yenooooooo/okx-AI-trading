import ccxt
import json
import os
from dotenv import load_dotenv

load_dotenv()

exchange = ccxt.okx({
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET_KEY'),
    'password': os.getenv('OKX_PASSWORD'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

try:
    positions = exchange.fetch_positions(['BTC/USDT:USDT'])
    for p in positions:
        print(json.dumps(p, indent=2))
except Exception as e:
    print(f"Error: {e}")
