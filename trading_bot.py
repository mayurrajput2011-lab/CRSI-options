import time
import ccxt

class TradingBot:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret

        # Initialize ccxt Kraken exchange (Binance is often region-restricted)
        if self.api_key == "dummy_api_key_123":
            # Public endpoints only for testing
            print("Initializing Kraken connection (Public endpoints only - Test Mode)...")
            self.exchange = ccxt.kraken({
                'enableRateLimit': True,
            })
        else:
            print(f"Initializing Kraken connection with API key: {self.api_key[:4]}***")
            self.exchange = ccxt.kraken({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'enableRateLimit': True,
            })

    def fetch_market_data(self, symbol):
        print(f"Fetching real market data for {symbol} via ccxt...")
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def evaluate_strategy(self, data):
        if not data:
            return None

        last_price = data.get('last')
        print(f"Evaluating strategy for live price: {last_price}...")

        # Simple threshold strategy
        if last_price and last_price < 100000:
            return "BUY"
        else:
            return "SELL"

    def execute_trade(self, symbol, action, quantity):
        print(f"Attempting to execute {action} for {quantity} of {symbol}...")

        if self.api_key == "dummy_api_key_123":
            print(f"[TEST MODE] Simulated execution: {action} {quantity} {symbol}")
            return True

        try:
            if action == "BUY":
                order = self.exchange.create_market_buy_order(symbol, quantity)
                print(f"Successfully bought {quantity} {symbol}. Order details: {order}")
                return True
            elif action == "SELL":
                order = self.exchange.create_market_sell_order(symbol, quantity)
                print(f"Successfully sold {quantity} {symbol}. Order details: {order}")
                return True
            return False
        except Exception as e:
            print(f"Error executing trade: {e}")
            return False

    def run(self):
        print("Starting CCXT TradingBot...")
        symbol = "BTC/USDT"
        quantity = 0.001

        data = self.fetch_market_data(symbol)
        action = self.evaluate_strategy(data)

        if action:
            self.execute_trade(symbol, action, quantity)
        else:
            print("No trade action recommended or data fetch failed.")

        print("TradingBot run complete.")

if __name__ == "__main__":
    # Dummy API credentials (will run in public/test mode)
    API_KEY = "dummy_api_key_123"
    API_SECRET = "dummy_api_secret_456"

    bot = TradingBot(API_KEY, API_SECRET)
    bot.run()
