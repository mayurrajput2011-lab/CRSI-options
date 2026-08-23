import time
import pyotp
from SmartApi.smartConnect import SmartConnect
from logzero import logger

class TradingBot:
    def __init__(self, api_key, client_code, password, totp_token):
        self.api_key = api_key
        self.client_code = client_code
        self.password = password
        self.totp_token = totp_token

        print(f"Initializing AngelOne SmartConnect with API key: {self.api_key[:4]}***")
        self.exchange = SmartConnect(api_key=self.api_key)

    def fetch_market_data(self, symbol, token):
        print(f"Fetching real market data for {symbol} ({token}) via AngelOne API...")
        try:
            # ltpData takes exchange, tradingsymbol, symboltoken
            response = self.exchange.ltpData("NSE", symbol, token)
            print(f"LTP Response: {response}")
            if response and response.get('status') == True:
                return response.get('data')
            else:
                print(f"Error in fetching LTP: {response}")
                return None
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def evaluate_strategy(self, data):
        if not data:
            return None

        last_price = data.get('ltp')
        print(f"Evaluating strategy for live price: {last_price}...")

        # Simple threshold strategy based on last traded price
        if last_price and last_price < 100000:
            return "BUY"
        else:
            return "SELL"

    def execute_trade(self, symbol, token, action, quantity, price):
        print(f"Attempting to execute {action} for {quantity} of {symbol} at {price}...")

        if getattr(self.exchange, 'access_token', None) is None:
            print(f"[TEST MODE] Simulated order execution: {action} {quantity} {symbol} at {price}")
            return True

        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": action,
            "exchange": "NSE",
            "ordertype": "LIMIT",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": str(price),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity)
        }

        try:
            orderid = self.exchange.placeOrder(orderparams)
            print(f"Order API called. Response/OrderID: {orderid}")
            if orderid:
                print(f"Successfully placed {action} order for {quantity} {symbol}.")
                return True
            else:
                print(f"Failed to place {action} order for {quantity} {symbol}.")
                return False
        except Exception as e:
            print(f"Error executing trade: {e}")
            return False

    def run(self):
        print("Starting AngelOne TradingBot...")

        try:
            totp = pyotp.TOTP(self.totp_token).now()
            print("Generated TOTP. Attempting session login...")
            session_data = self.exchange.generateSession(self.client_code, self.password, totp)

            if session_data.get('status') == False:
                print(f"Login failed: {session_data}. (Expected in test mode with dummy credentials)")
                # Continue execution in test mode to demonstrate the logic structure
            else:
                print("Successfully generated session.")

        except Exception as e:
            print(f"Exception during login: {e}")
            return

        symbol = "SBIN-EQ"
        token = "3045"
        quantity = 1
        limit_price = 19500 # Used from official test example

        # Test mode execution fallback
        if getattr(self.exchange, 'access_token', None) is None:
            print("[TEST MODE] Simulating data fetch...")
            data = {'ltp': 500.0} # Dummy LTP
        else:
            data = self.fetch_market_data(symbol, token)
        action = self.evaluate_strategy(data)

        if action:
            self.execute_trade(symbol, token, action, quantity, limit_price)
        else:
            print("No trade action recommended or data fetch failed.")

        print("TradingBot run complete.")

if __name__ == "__main__":
    # Dummy API credentials for AngelOne
    API_KEY = "dummy_api_key_123"
    CLIENT_CODE = "DUMMY_CLIENT_123"
    PASSWORD = "dummy_password"
    TOTP_TOKEN = "JBSWY3DPEHPK3PXP" # Base32 compliant dummy token

    bot = TradingBot(API_KEY, CLIENT_CODE, PASSWORD, TOTP_TOKEN)
    bot.run()
