import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class BinanceWhaleClient:
    """Client for Binance Public API to track Whale Volume"""
    
    BASE_URL = "https://api.binance.com/api/v3"
    
    def __init__(self, symbol: str = "BTCUSDT", min_value: int = 500000):
        self.symbol = symbol.upper()
        self.min_value = min_value
        self.session = requests.Session()

    def get_recent_trades(self, limit: int = 1000) -> List[Dict]:
        """
        Get recent trades (aggTrades)
        """
        try:
            params = {
                "symbol": self.symbol,
                "limit": limit
            }
            
            response = self.session.get(f"{self.BASE_URL}/aggTrades", params=params)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Binance API error: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching Binance data: {e}")
            return []

    def get_whale_sentiment(self) -> str:
        """
        Calculate sentiment based on Whale Volume (Taker Buy vs Sell).
        Logic:
        1. Fetch recent trades (last 1000).
        2. Filter for value > min_value.
        3. Sum volume for Buys (isBuyerMaker=False) and Sells (isBuyerMaker=True).
        4. Compare.
        """
        trades = self.get_recent_trades(limit=1000)
        
        buy_vol = 0.0
        sell_vol = 0.0
        whale_count = 0
        
        for trade in trades:
            # Trade format:
            # {
            #   "a": 26129,         // Aggregate tradeId
            #   "p": "0.01633102",  // Price
            #   "q": "4.70443515",  // Quantity
            #   "f": 27781,         // First tradeId
            #   "l": 27781,         // Last tradeId
            #   "T": 1498793709153, // Timestamp
            #   "m": true,          // Was the buyer the maker?
            #   "M": true           // Was the trade the best price match?
            # }
            
            price = float(trade['p'])
            qty = float(trade['q'])
            value = price * qty
            
            if value >= self.min_value:
                whale_count += 1
                is_buyer_maker = trade['m']
                
                # If buyer is maker, then Taker (aggressor) is Seller -> SELL
                # If buyer is NOT maker, then Taker (aggressor) is Buyer -> BUY
                if is_buyer_maker:
                    sell_vol += value
                else:
                    buy_vol += value
        
        net_vol = buy_vol - sell_vol
        
        logger.info(f"Binance Whale ({self.symbol}): {whale_count} trades > ${self.min_value:,.0f}")
        logger.info(f"  Buy Vol: ${buy_vol:,.0f} | Sell Vol: ${sell_vol:,.0f} | Net: ${net_vol:,.0f}")
        
        # Thresholds for sentiment (e.g., 1M net difference)
        threshold = 1_000_000 
        
        if net_vol > threshold:
            return "BULLISH"
        elif net_vol < -threshold:
            return "BEARISH"
        
        return "NEUTRAL"


class SmartMoneyStrategy(BaseStrategy):
    """
    Smart Money Strategy
    Combines:
    1. Time Window (London/NY Overlap)
    2. On-Chain Bias (Binance Whale Volume Proxy)
    3. Liquidity Hunter (Sweep & Reclaim)
    """

    def __init__(self, client, config, dependencies):
        super().__init__(client, config, dependencies)
        self.whale_client = BinanceWhaleClient(
            symbol=config.binance_symbol,
            min_value=config.whale_min_value
        )

    def is_time_window_active(self) -> bool:
        """Check if we are in the active trading window"""
        now = datetime.now()
        current_hour = now.hour
        return self.config.time_window_start <= current_hour < self.config.time_window_end

    def check_liquidity_sweep(self, ohlcv: List[List[float]]) -> Optional[str]:
        """
        Check for Liquidity Sweep pattern.
        """
        if len(ohlcv) < self.config.liquidity_lookback_periods + 1:
            return None
            
        current = ohlcv[-1]
        curr_low, curr_close = current[3], current[4]
        curr_high = current[2]
        
        lookback = ohlcv[-(self.config.liquidity_lookback_periods + 1):-1]
        
        lowest_low = min(c[3] for c in lookback)
        highest_high = max(c[2] for c in lookback)
        
        # Bullish Sweep (Long)
        if curr_low < lowest_low and curr_close > lowest_low:
            logger.info(f"Bullish Sweep detected! Low {curr_low} < PrevLow {lowest_low}, Close {curr_close} > PrevLow")
            return "LONG"
            
        # Bearish Sweep (Short)
        if curr_high > highest_high and curr_close < highest_high:
            logger.info(f"Bearish Sweep detected! High {curr_high} > PrevHigh {highest_high}, Close {curr_close} < PrevHigh")
            return "SHORT"
            
        return None

    def scan(self) -> List[Dict[str, Any]]:
        signals = []
        
        # 1. Time Window Check
        if not self.is_time_window_active():
            # logger.info("Outside trading window. Skipping.")
            return signals
            
        logger.info("Inside Trading Window (London/NY Overlap)")
        
        # 2. Whale Volume Bias
        sentiment = self.whale_client.get_whale_sentiment()
        if sentiment == "NEUTRAL":
            logger.info("Whale sentiment Neutral. No trade.")
            return signals
            
        # 3. Liquidity Hunter
        instrument = "BTC-PERPETUAL" # TODO: Map from config symbol
        
        ohlcv = self.client.get_ohlcv(instrument, timeframe=self.config.timeframe, limit=50)
        if not ohlcv:
            return signals
            
        sweep_direction = self.check_liquidity_sweep(ohlcv)
        
        if not sweep_direction:
            return signals
            
        # Confluence Check
        if sentiment == "BULLISH" and sweep_direction == "LONG":
            logger.info(">>> CONFLUENCE: Bullish Whale Volume + Bullish Sweep <<<")
            signals.append({
                "type": "smart_money",
                "direction": "buy",
                "instrument": instrument,
                "reason": "Bullish Sweep + Whale Buy Vol"
            })
            
        elif sentiment == "BEARISH" and sweep_direction == "SHORT":
            logger.info(">>> CONFLUENCE: Bearish Whale Volume + Bearish Sweep <<<")
            signals.append({
                "type": "smart_money",
                "direction": "sell",
                "instrument": instrument,
                "reason": "Bearish Sweep + Whale Sell Vol"
            })
            
        return signals

    def execute_entry(self, signal: Dict[str, Any]) -> bool:
        direction = signal["direction"]
        instrument = signal["instrument"]
        logger.info(f"Executing Smart Money {direction} on {instrument}")
        return True

    def manage_positions(self) -> Dict[str, Any]:
        return {"closed_tp": 0, "closed_sl": 0, "total_pnl": 0}
