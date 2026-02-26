import ccxt
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
exchange.set_sandbox_mode(True)

try:
    order = exchange.create_market_buy_order('BTC/USDT:USDT', 1)
    print(order)
except Exception as e:
    print(e)
