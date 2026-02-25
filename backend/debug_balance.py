
import os
import json
import ccxt
from dotenv import load_dotenv

# Load env from backend/.env
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(dotenv_path=env_path, override=True)

api_key = os.getenv("OKX_API_KEY")
secret_key = os.getenv("OKX_SECRET_KEY")
password = os.getenv("OKX_PASSWORD")

print(f"Key loaded: {bool(api_key)}")

try:
    exchange = ccxt.okx({
        'apiKey': api_key,
        'secret': secret_key,
        'password': password,
        'options': {'defaultType': 'swap', 'sandbox': True}
    })
    exchange.set_sandbox_mode(True)
    
    print("Fetching balance...")
    # Try different types
    bal_trading = exchange.fetch_balance({'type': 'trading'})
    bal_funding = exchange.fetch_balance({'type': 'funding'})
    
    output = {
        "trading_keys": list(bal_trading.keys()),
        "trading_USDT": bal_trading.get('USDT'),
        "total_usdt_trading": bal_trading.get('USDT', {}).get('total'),
        "free_usdt_trading": bal_trading.get('USDT', {}).get('free'),
        "full_trading_response": bal_trading,
        
        "funding_keys": list(bal_funding.keys()),
        "funding_USDT": bal_funding.get('USDT'),
        "full_funding_response": bal_funding
    }
    
    with open("balance_debug.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
        
    print("Debug info saved to balance_debug.json")

except Exception as e:
    print(f"Error: {e}")
