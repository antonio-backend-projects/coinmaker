import time
import logging
import ccxt
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any, Optional

from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class AdvancedFlowAnalyzer:
    """
    Advanced Order Flow Analyzer using CCXT.
    Calculates CVD (Cumulative Volume Delta) and detects Absorption (Whale Walls).
    """
    
    def __init__(self, symbol: str = "BTC/USDT"):
        self.symbol = symbol
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'} # We analyze Spot flow for "Whale" activity
        })

    def analyze_market_structure(self, limit: int = 1000, 
                               min_vol_threshold: float = 10.0,
                               delta_ratio_threshold: float = 0.15,
                               price_change_threshold: float = 0.01) -> Optional[Dict[str, Any]]:
        """
        Downloads recent trades and checks for Price/Delta divergence (Absorption).
        """
        try:
            # 1. Download trades (Tick Data)
            trades = self.exchange.fetch_trades(self.symbol, limit=limit)
            if not trades:
                return None
                
            df = pd.DataFrame(trades)
            
            # 2. Price Data
            first_price = df['price'].iloc[0]
            last_price = df['price'].iloc[-1]
            
            # Calculate price change percentage
            price_change_pct = ((last_price - first_price) / first_price) * 100
            
            # 3. Delta Data (Aggressors)
            # CCXT normalizes taker side: 'buy' = Taker Buy (Green), 'sell' = Taker Sell (Red)
            buy_vol = df[df['side'] == 'buy']['amount'].sum()
            sell_vol = df[df['side'] == 'sell']['amount'].sum()
            
            delta = buy_vol - sell_vol
            total_vol = buy_vol + sell_vol

            # --- CORE LOGIC: ABSORPTION DETECTION ---
            signal = "NEUTRAL"
            reason = ""
            
            # SCENARIO 1: BULLISH ABSORPTION (Whale Wall)
            # Sellers are aggressive (Delta Very Negative) BUT price holds or rises
            if total_vol > min_vol_threshold:
                if delta < -(total_vol * delta_ratio_threshold): # Delta is negative (at least X% of total vol)
                    if price_change_pct >= -price_change_threshold: # BUT price is flat or green
                        signal = "ABSORPTION_BUY"
                        reason = f"Whale Wall: Strong selling (Delta {delta:.2f}) absorbed. Price change: {price_change_pct:.4f}%"

            # SCENARIO 2: BEARISH ABSORPTION (Iceberg)
            # Buyers are aggressive (Delta Very Positive) BUT price holds or falls
                elif delta > (total_vol * delta_ratio_threshold): # Delta positive
                    if price_change_pct <= price_change_threshold: # BUT price is flat or red
                        signal = "ABSORPTION_SELL"
                        reason = f"Iceberg Order: Strong buying (Delta {delta:.2f}) absorbed. Price change: {price_change_pct:.4f}%"

            return {
                "price_start": first_price,
                "price_end": last_price,
                "price_change_pct": price_change_pct,
                "delta": delta,
                "total_volume": total_vol,
                "signal": signal,
                "reason": reason
            }

        except Exception as e:
            logger.error(f"Error in AdvancedFlowAnalyzer: {e}")
            return None


class SmartMoneyStrategy(BaseStrategy):
    """
    Smart Money Strategy 2.0 🐋
    Combines:
    1. Time Window (London/NY Overlap)
    2. Liquidity Hunter (Sweep & Reclaim)
    3. Order Flow Confirmation (CVD & Absorption)
    """

    def __init__(self, client, config, dependencies):
        super().__init__(client, config, dependencies)
        self.flow_analyzer = AdvancedFlowAnalyzer(symbol=config.binance_symbol)

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
            # logger.debug("Outside trading window. Skipping.")
            return signals
            
        # logger.info("Inside Trading Window (London/NY Overlap)")
        
        # 2. Liquidity Hunter (Price Action)
        # We use the instrument from config or default to BTC-PERPETUAL for Deribit
        # Note: self.config.symbol is likely "BTC/USDT" for Binance, but for Deribit we need "BTC-PERPETUAL"
        # Let's assume we are trading BTC-PERPETUAL on Deribit based on BTCUSDT spot signals.
        instrument = "BTC-PERPETUAL" 
        
        ohlcv = self.client.get_ohlcv(instrument, timeframe=self.config.timeframe, limit=50)
        if not ohlcv:
            return signals
            
        sweep_direction = self.check_liquidity_sweep(ohlcv)
        
        if not sweep_direction:
            return signals
            
        logger.info(f"Liquidity Sweep detected ({sweep_direction}). Checking Order Flow...")
        
        # 3. Order Flow Confirmation (CVD & Absorption)
        flow_analysis = self.flow_analyzer.analyze_market_structure(
            min_vol_threshold=self.config.absorption_min_vol,
            delta_ratio_threshold=self.config.absorption_delta_ratio,
            price_change_threshold=self.config.absorption_price_threshold
        )
        
        if not flow_analysis:
            logger.warning("Could not fetch Order Flow data.")
            return signals
            
        logger.info(f"Order Flow Analysis: {flow_analysis['signal']} | Delta: {flow_analysis['delta']:.2f} | Price Chg: {flow_analysis['price_change_pct']:.4f}%")
        
        # Confluence Check
        if sweep_direction == "LONG":
            if flow_analysis['signal'] == "ABSORPTION_BUY":
                logger.info(">>> CONFLUENCE: Bullish Sweep + Bullish Absorption <<<")
                signals.append({
                    "type": "smart_money",
                    "direction": "buy",
                    "instrument": instrument,
                    "reason": f"Bullish Sweep + Absorption ({flow_analysis['reason']})",
                    "stop_loss_price": ohlcv[-1][3] # Low of the sweep candle
                })
            else:
                logger.info("No Absorption confirmation for Long.")
                
        elif sweep_direction == "SHORT":
            if flow_analysis['signal'] == "ABSORPTION_SELL":
                logger.info(">>> CONFLUENCE: Bearish Sweep + Bearish Absorption <<<")
                signals.append({
                    "type": "smart_money",
                    "direction": "sell",
                    "instrument": instrument,
                    "reason": f"Bearish Sweep + Absorption ({flow_analysis['reason']})",
                    "stop_loss_price": ohlcv[-1][2] # High of the sweep candle
                })
            else:
                logger.info("No Absorption confirmation for Short.")
            
        return signals

    def execute_entry(self, signal: Dict[str, Any]) -> bool:
        direction = signal["direction"]
        instrument = signal["instrument"]
        reason = signal["reason"]
        sl_price = signal.get("stop_loss_price")
        
        logger.info(f"Executing Smart Money {direction} on {instrument}")
        logger.info(f"Reason: {reason}")
        
        if not sl_price:
            logger.error("No Stop Loss price provided, cannot execute.")
            return False
            
        # 1. Get current price (approximate, for sizing)
        # We can use the last close from OHLCV or fetch ticker
        ticker = self.client.get_ticker(instrument)
        current_price = ticker.get('last_price') if ticker else None
        
        if not current_price:
            logger.error("Could not get current price for sizing")
            return False
            
        # 2. Calculate Size
        # Risk 1% (0.01) - TODO: Make configurable in Config
        risk_pct = 0.01 
        
        # Calculate quantity in BTC (Base Currency)
        qty_btc = self.dependencies['risk_manager'].calculate_futures_quantity(current_price, sl_price, risk_pct)
        
        if qty_btc <= 0:
            logger.error("Calculated quantity is 0, aborting.")
            return False
            
        # 3. Convert to Contracts (USD) for Deribit Inverse
        # Qty (USD) = Qty (BTC) * Price
        # Round to nearest 10 USD (Deribit BTC contract size)
        contract_size_usd = 10.0 # Standard Deribit BTC contract
        qty_usd_raw = qty_btc * current_price
        qty_contracts = int(round(qty_usd_raw / contract_size_usd) * contract_size_usd)
        
        if qty_contracts < contract_size_usd:
            logger.warning(f"Quantity {qty_usd_raw} USD too small for min contract {contract_size_usd}")
            return False
            
        logger.info(f"Sizing: {qty_btc:.4f} BTC -> ${qty_contracts} Contracts")

        # 4. Execute Trade
        return self.dependencies['order_manager'].execute_smart_money_trade(
            instrument, direction, qty_contracts, sl_price
        )

    def manage_positions(self) -> Dict[str, Any]:
        return {"closed_tp": 0, "closed_sl": 0, "total_pnl": 0}
