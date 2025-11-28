"""
Configuration module for the trading bot
Loads all settings from environment variables with defaults
"""

import os
from typing import List, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class StrategyConfig:
    """Base configuration for any strategy"""
    name: str
    enabled: bool = True


@dataclass
class IronCondorConfig(StrategyConfig):
    """Configuration for Iron Condor strategy"""
    initial_equity: float = 10000.0
    risk_per_condor: float = 0.01
    max_portfolio_risk: float = 0.03
    tp_ratio: float = 0.55
    sl_mult: float = 1.2
    close_before_expiry_hours: int = 24
    min_dte: int = 7
    max_dte: int = 10
    min_iv_percentile: float = 30.0
    short_delta_target: float = 0.12
    wing_width_percent: float = 0.05
    currencies: List[str] = None

    def __post_init__(self):
        if self.currencies is None:
            self.currencies = ["BTC", "ETH"]


@dataclass
class SmartMoneyConfig(StrategyConfig):
    """Configuration for Smart Money strategy"""
    time_window_start: int = 14  # 14:00
    time_window_end: int = 17    # 17:00
    whale_min_value: int = 500000  # $500k
    binance_symbol: str = "BTCUSDT"
    liquidity_lookback_periods: int = 20
    timeframe: str = "15m"
    symbol: str = "BTC/USDT"


class Config:
    """Global configuration class"""

    # Deribit API
    DERIBIT_API_KEY = os.getenv("DERIBIT_API_KEY", "")
    DERIBIT_API_SECRET = os.getenv("DERIBIT_API_SECRET", "")
    DERIBIT_ENV = os.getenv("DERIBIT_ENV", "test")

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = "logs/trading_bot.log"

    # Schedule
    DAILY_SCAN_TIME = os.getenv("DAILY_SCAN_TIME", "10:00")
    MONITORING_INTERVAL_MINUTES = int(os.getenv("MONITORING_INTERVAL_MINUTES", 5))

    # Strategies
    STRATEGIES: List[StrategyConfig] = []

    @classmethod
    def load_strategies(cls):
        """Load enabled strategies"""
        cls.STRATEGIES = []

        # Iron Condor
        if os.getenv("STRATEGY_IRON_CONDOR_ENABLED", "true").lower() == "true":
            cls.STRATEGIES.append(IronCondorConfig(
                name="Iron Condor",
                initial_equity=float(os.getenv("INITIAL_EQUITY", 10000)),
                risk_per_condor=float(os.getenv("RISK_PER_CONDOR", 0.01)),
                max_portfolio_risk=float(os.getenv("MAX_PORTFOLIO_RISK", 0.03)),
                tp_ratio=float(os.getenv("TP_RATIO", 0.55)),
                sl_mult=float(os.getenv("SL_MULT", 1.2)),
                close_before_expiry_hours=int(os.getenv("CLOSE_BEFORE_EXPIRY_HOURS", 24)),
                min_dte=int(os.getenv("MIN_DTE", 7)),
                max_dte=int(os.getenv("MAX_DTE", 10)),
                min_iv_percentile=float(os.getenv("MIN_IV_PERCENTILE", 30)),
                short_delta_target=float(os.getenv("SHORT_DELTA_TARGET", 0.12)),
                wing_width_percent=float(os.getenv("WING_WIDTH_PERCENT", 0.05))
            ))

        # Smart Money
        if os.getenv("STRATEGY_SMART_MONEY_ENABLED", "true").lower() == "true":
            cls.STRATEGIES.append(SmartMoneyConfig(
                name="Smart Money",
                time_window_start=int(os.getenv("SM_TIME_WINDOW_START", 14)),
                time_window_end=int(os.getenv("SM_TIME_WINDOW_END", 17)),
                whale_min_value=int(os.getenv("SM_WHALE_MIN_VALUE", 500000)),
                binance_symbol=os.getenv("SM_BINANCE_SYMBOL", "BTCUSDT")
            ))

    @classmethod
    def validate(cls) -> bool:
        """Validate configuration"""
        errors = []

        if not cls.DERIBIT_API_KEY:
            errors.append("DERIBIT_API_KEY is required")
        if not cls.DERIBIT_API_SECRET:
            errors.append("DERIBIT_API_SECRET is required")
        if cls.DERIBIT_ENV not in ["test", "prod"]:
            errors.append("DERIBIT_ENV must be 'test' or 'prod'")

        # Validate strategies
        cls.load_strategies()
        if not cls.STRATEGIES:
            errors.append("No strategies enabled")

        for strategy in cls.STRATEGIES:
            if isinstance(strategy, IronCondorConfig):
                if strategy.initial_equity <= 0:
                    errors.append("Iron Condor: INITIAL_EQUITY must be positive")
            elif isinstance(strategy, SmartMoneyConfig):
                pass

        if errors:
            for error in errors:
                print(f"❌ Configuration error: {error}")
            return False

        return True

    @classmethod
    def display(cls):
        """Display current configuration"""
        print("=" * 60)
        print("CONFIGURATION")
        print("=" * 60)
        print(f"Environment: {cls.DERIBIT_ENV}")
        print(f"Active Strategies: {len(cls.STRATEGIES)}")
        
        for strategy in cls.STRATEGIES:
            print(f"\n[{strategy.name}]")
            if isinstance(strategy, IronCondorConfig):
                print(f"  Risk per Condor: {strategy.risk_per_condor:.1%}")
                print(f"  Max Portfolio Risk: {strategy.max_portfolio_risk:.1%}")
            elif isinstance(strategy, SmartMoneyConfig):
                print(f"  Time Window: {strategy.time_window_start}:00 - {strategy.time_window_end}:00")
                print(f"  Whale Min Value: ${strategy.whale_min_value:,.0f}")
                print(f"  Binance Symbol: {strategy.binance_symbol}")
        print("=" * 60)

if __name__ == "__main__":
    if Config.validate():
        Config.display()
