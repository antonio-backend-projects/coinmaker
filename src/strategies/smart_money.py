import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class WhaleAlertClient:
    """Client for Whale Alert API (Free Tier)"""
    
    BASE_URL = "https://api.whale-alert.io/v1"
    
    def __init__(self, api_key: str, min_value: int = 500000):
        self.api_key = api_key
        self.min_value = min_value
        self.session = requests.Session()

    def get_transactions(self, start_time: int, currency: str = "btc") -> List[Dict]:
        """
        Get transactions since start_time
        Free tier limit: 10 requests per minute.
        """
        if not self.api_key:
            logger.warning("No Whale Alert API key provided. Returning empty list.")
            return []

        try:
            # End time is now
            end_time = int(time.time())
            
            params = {
                "api_key": self.api_key,
                "min_value": self.min_value,
                "start": start_time,
                "end": end_time,
                "currency": currency,
                "limit": 100 # Max limit per page
            }
            
            response = self.session.get(f"{self.BASE_URL}/transactions", params=params)
            
            if response.status_code == 200:
                data = response.json()
                return data.get("transactions", [])
            else:
                logger.error(f"Whale Alert API error: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error fetching Whale Alert data: {e}")
            return []

    def get_netflow_sentiment(self, lookback_minutes: int = 60) -> str:
        """
        Calculate sentiment based on netflow.
        Inflow (Wallet -> Exchange) = Bearish
        Outflow (Exchange -> Wallet) = Bullish
        """
        start_time = int(time.time()) - (lookback_minutes * 60)
        transactions = self.get_transactions(start_time)
        
        inflow_vol = 0.0
        outflow_vol = 0.0
        
        for tx in transactions:
            amount = float(tx.get("amount_usd", 0))
            from_type = tx.get("from", {}).get("owner_type", "unknown")
            to_type = tx.get("to", {}).get("owner_type", "unknown")
            
            # Inflow: Unknown/Wallet -> Exchange
            if from_type != "exchange" and to_type == "exchange":
                inflow_vol += amount
                
            # Outflow: Exchange -> Unknown/Wallet
            elif from_type == "exchange" and to_type != "exchange":
                outflow_vol += amount
        
        net_flow = inflow_vol - outflow_vol
        
        logger.info(f"Whale Alert (1h): Inflow ${inflow_vol:,.0f} | Outflow ${outflow_vol:,.0f} | Net ${net_flow:,.0f}")
        
        if net_flow > 5_000_000: # Net Inflow > 5M
            return "BEARISH"
        elif net_flow < -5_000_000: # Net Outflow > 5M (Negative net flow means more outflow)
            # Wait, Net Flow = In - Out. 
            # If Out > In, Net is Negative.
            # So Negative Net Flow = Bullish.
            return "BULLISH"
        
        return "NEUTRAL"


class SmartMoneyStrategy(BaseStrategy):
    """
    Smart Money Strategy
    Combines:
    1. Time Window (London/NY Overlap)
    2. On-Chain Bias (Whale Alert)
    3. Liquidity Hunter (Sweep & Reclaim)
    """

    def __init__(self, client, config, dependencies):
        super().__init__(client, config, dependencies)
        self.whale_client = WhaleAlertClient(
            api_key=config.whale_alert_api_key,
            min_value=config.whale_alert_min_value
        )

    def is_time_window_active(self) -> bool:
        """Check if we are in the active trading window"""
        now = datetime.now()
        current_hour = now.hour
        
        # Simple check: start_hour <= current < end_hour
        # e.g. 14 <= 15 < 17
        return self.config.time_window_start <= current_hour < self.config.time_window_end

    def check_liquidity_sweep(self, ohlcv: List[List[float]]) -> Optional[str]:
        """
        Check for Liquidity Sweep pattern.
        Bullish Sweep: Price dips below recent low but closes above it.
        Bearish Sweep: Price spikes above recent high but closes below it.
        
        Args:
            ohlcv: List of [timestamp, open, high, low, close, volume]
        """
        if len(ohlcv) < self.config.liquidity_lookback_periods + 1:
            return None
            
        # Current candle (last closed or current open? usually we look at closed candles)
        # Assuming ohlcv includes the latest completed candle.
        current = ohlcv[-1]
        curr_open, curr_high, curr_low, curr_close = current[1], current[2], current[3], current[4]
        
        # Previous N candles
        lookback = ohlcv[-(self.config.liquidity_lookback_periods + 1):-1]
        
        # Find lowest low and highest high of lookback
        lowest_low = min(c[3] for c in lookback)
        highest_high = max(c[2] for c in lookback)
        
        # Bullish Sweep (Long)
        # Low broke support, but Close reclaimed it
        if curr_low < lowest_low and curr_close > lowest_low:
            logger.info(f"Bullish Sweep detected! Low {curr_low} < PrevLow {lowest_low}, Close {curr_close} > PrevLow")
            return "LONG"
            
        # Bearish Sweep (Short)
        # High broke resistance, but Close fell back below
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
        
        # 2. On-Chain Bias
        sentiment = self.whale_client.get_netflow_sentiment()
        if sentiment == "NEUTRAL":
            logger.info("On-Chain sentiment Neutral. No trade.")
            return signals
            
        # 3. Liquidity Hunter
        # Get OHLCV data
        # Symbol format for Deribit: BTC-PERPETUAL or similar. 
        # Config has "BTC/USDT", we need to map to Deribit instrument.
        instrument = "BTC-PERPETUAL" # Hardcoded for now, or from config
        
        ohlcv = self.client.get_ohlcv(instrument, timeframe=self.config.timeframe, limit=50)
        if not ohlcv:
            return signals
            
        sweep_direction = self.check_liquidity_sweep(ohlcv)
        
        if not sweep_direction:
            return signals
            
        # Confluence Check
        if sentiment == "BULLISH" and sweep_direction == "LONG":
            logger.info(">>> CONFLUENCE: Bullish On-Chain + Bullish Sweep <<<")
            signals.append({
                "type": "smart_money",
                "direction": "buy",
                "instrument": instrument,
                "reason": "Bullish Sweep + Outflow"
            })
            
        elif sentiment == "BEARISH" and sweep_direction == "SHORT":
            logger.info(">>> CONFLUENCE: Bearish On-Chain + Bearish Sweep <<<")
            signals.append({
                "type": "smart_money",
                "direction": "sell",
                "instrument": instrument,
                "reason": "Bearish Sweep + Inflow"
            })
            
        return signals

    def execute_entry(self, signal: Dict[str, Any]) -> bool:
        # Placeholder for execution logic
        # For Smart Money, we might trade Perpetuals (Futures)
        # Deribit supports perps.
        
        direction = signal["direction"]
        instrument = signal["instrument"]
        
        logger.info(f"Executing Smart Money {direction} on {instrument}")
        
        # TODO: Implement actual order placement for Perps
        # This requires calculating position size based on risk, stop loss placement, etc.
        # For now, we just log it as the user asked for the "structure" and "logic".
        
        return True

    def manage_positions(self) -> Dict[str, Any]:
        # TODO: Implement management for Perp positions (Trailing stop?)
        return {"closed_tp": 0, "closed_sl": 0, "total_pnl": 0}
