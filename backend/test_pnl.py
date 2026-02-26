import asyncio
from okx_engine import OKXEngine
import traceback

async def main():
    engine = OKXEngine()
    try:
        # ccxt exchange object
        # let's call fetch_positions_history
        res = engine.exchange.privateGetAccountPositionsHistory({'instType': 'SWAP'})
        print("Success fetching positions-history:")
        if res.get('data'):
            for pos in res['data'][:2]:
                print(f"InstId: {pos.get('instId')} RealizedPnl: {pos.get('realizedPnl')} UPL Ratio: {pos.get('uplRatio')}")
        else:
            print("No data.")
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
