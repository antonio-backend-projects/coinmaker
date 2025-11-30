import pandas as pd
import numpy as np
from typing import List, Dict, Any
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Backtester:
    """
    Simple Event-Driven Backtester for Smart Money Strategy.
    """
    
    def __init__(self, initial_capital: float = 1000.0):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        
    def load_data(self, csv_path: str) -> pd.DataFrame:
        """Load OHLCV data from CSV"""
        df = pd.read_csv(csv_path)
        # Ensure columns: timestamp, open, high, low, close, volume
        return df
        
    def run_strategy(self, data: pd.DataFrame, strategy_logic):
        """
        Run strategy logic over data.
        strategy_logic: function that takes (row, lookback_data) and returns signal
        """
        logger.info("Starting Backtest...")
        
        position = None # {entry_price, size, sl, tp, direction}
        
        for i in range(50, len(data)):
            row = data.iloc[i]
            lookback = data.iloc[i-50:i]
            
            # 1. Check Exit if in position
            if position:
                self._check_exit(position, row)
                if position['status'] == 'closed':
                    self.trades.append(position)
                    self.capital += position['pnl']
                    position = None
                continue
                
            # 2. Check Entry
            signal = strategy_logic(row, lookback)
            if signal:
                position = self._open_position(signal, row)
                
            self.equity_curve.append(self.capital)
            
        self._generate_report()
        
    def _open_position(self, signal: Dict, row) -> Dict:
        """Simulate opening a position"""
        entry_price = row['close'] # Assume close execution
        sl = signal['sl']
        
        # Sizing (1.5% Risk)
        risk_pct = 0.015
        risk_amt = self.capital * risk_pct
        dist = abs(entry_price - sl)
        size = risk_amt / dist
        
        tp = entry_price + (dist * 2.5) if signal['direction'] == 'long' else entry_price - (dist * 2.5)
        
        return {
            "entry_time": row['timestamp'],
            "entry_price": entry_price,
            "direction": signal['direction'],
            "size": size,
            "sl": sl,
            "tp": tp,
            "status": "open",
            "pnl": 0
        }

    def _check_exit(self, position: Dict, row):
        """Check TP/SL hit"""
        high = row['high']
        low = row['low']
        
        if position['direction'] == 'long':
            if low <= position['sl']:
                position['exit_price'] = position['sl']
                position['reason'] = 'SL'
                position['pnl'] = - (position['size'] * abs(position['entry_price'] - position['sl']))
                position['status'] = 'closed'
            elif high >= position['tp']:
                position['exit_price'] = position['tp']
                position['reason'] = 'TP'
                position['pnl'] = (position['size'] * abs(position['tp'] - position['entry_price']))
                position['status'] = 'closed'
                
        else: # Short
            if high >= position['sl']:
                position['exit_price'] = position['sl']
                position['reason'] = 'SL'
                position['pnl'] = - (position['size'] * abs(position['sl'] - position['entry_price']))
                position['status'] = 'closed'
            elif low <= position['tp']:
                position['exit_price'] = position['tp']
                position['reason'] = 'TP'
                position['pnl'] = (position['size'] * abs(position['entry_price'] - position['tp']))
                position['status'] = 'closed'

    def _generate_report(self):
        """Generate performance report"""
        if not self.trades:
            logger.info("No trades generated.")
            return
            
        df = pd.DataFrame(self.trades)
        total_trades = len(df)
        wins = len(df[df['pnl'] > 0])
        losses = len(df[df['pnl'] <= 0])
        win_rate = (wins / total_trades) * 100
        total_pnl = df['pnl'].sum()
        
        print("\n" + "="*40)
        print("BACKTEST RESULTS")
        print("="*40)
        print(f"Initial Capital: ${self.initial_capital:.2f}")
        print(f"Final Capital:   ${self.capital:.2f}")
        print(f"Total Return:    {((self.capital - self.initial_capital)/self.initial_capital)*100:.2f}%")
        print(f"Total Trades:    {total_trades}")
        print(f"Win Rate:        {win_rate:.1f}%")
        print(f"Wins: {wins} | Losses: {losses}")
        print("="*40 + "\n")

if __name__ == "__main__":
    # Example usage
    bt = Backtester()
    # bt.run_strategy(data, my_strategy_logic)
