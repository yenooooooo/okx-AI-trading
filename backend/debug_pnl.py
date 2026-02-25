
import os
import json
import ccxt
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(dotenv_path=env_path, override=True)

try:
    exchange = ccxt.okx({
        'apiKey': os.getenv('OKX_API_KEY'),
        'secret': os.getenv('OKX_SECRET_KEY'),
        'password': os.getenv('OKX_PASSWORD'),
        'options': {'defaultType': 'swap', 'sandbox': True}
    })
    exchange.set_sandbox_mode(True)
    
    symbol = "BTC/USDT:USDT"
    
    # 1. Open a tiny LONG position
    print("Opening LONG...")
    exchange.set_leverage(1, symbol)
    open_res = exchange.create_market_buy_order(symbol, 1)
    
    # 2. Close it immediately
    print("Closing LONG...")
    close_res = exchange.create_market_sell_order(symbol, 1)
    
    # 3. Fetch trades to see if pnl is there
    print("Fetching trades...")
    trades = exchange.fetch_my_trades(symbol, limit=5)
    
    output = {
        "close_res": close_res,
        "recent_trades": trades
    }
    
    with open("pnl_debug.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
        
    print("pnl_debug.json saved.")
except Exception as e:
    print(f"Error: {e}")
