"""
Configuration module for the trading bot
Loads all settings from environment variables with defaults
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Configuration class for trading bot"""

    # Deribit API
    DERIBIT_API_KEY = os.getenv("DERIBIT_API_KEY", "")
    DERIBIT_API_SECRET = os.getenv("DERIBIT_API_SECRET", "")
    DERIBIT_ENV = os.getenv("DERIBIT_ENV", "test")

    # Trading parameters
    INITIAL_EQUITY = float(os.getenv("INITIAL_EQUITY", 10000))
    RISK_PER_CONDOR = float(os.getenv("RISK_PER_CONDOR", 0.01))
    MAX_PORTFOLIO_RISK = float(os.getenv("MAX_PORTFOLIO_RISK", 0.03))

    # Exit parameters
    TP_RATIO = float(os.getenv("TP_RATIO", 0.55))
    SL_MULT = float(os.getenv("SL_MULT", 1.2))
    CLOSE_BEFORE_EXPIRY_HOURS = int(os.getenv("CLOSE_BEFORE_EXPIRY_HOURS", 24))

    # Strategy parameters
    MIN_DTE = int(os.getenv("MIN_DTE", 7))
    MAX_DTE = int(os.getenv("MAX_DTE", 10))
    MIN_IV_PERCENTILE = float(os.getenv("MIN_IV_PERCENTILE", 30))
    SHORT_DELTA_TARGET = float(os.getenv("SHORT_DELTA_TARGET", 0.12))
    WING_WIDTH_PERCENT = float(os.getenv("WING_WIDTH_PERCENT", 0.05))

    # Currencies to trade
    CURRENCIES = ["BTC", "ETH"]

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = "logs/trading_bot.log"

    # Schedule
    DAILY_SCAN_TIME = os.getenv("DAILY_SCAN_TIME", "10:00")
    MONITORING_INTERVAL_MINUTES = int(os.getenv("MONITORING_INTERVAL_MINUTES", 5))

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
        if cls.INITIAL_EQUITY <= 0:
            errors.append("INITIAL_EQUITY must be positive")
        if not (0 < cls.RISK_PER_CONDOR < 1):
            errors.append("RISK_PER_CONDOR must be between 0 and 1")
        if not (0 < cls.MAX_PORTFOLIO_RISK < 1):
            errors.append("MAX_PORTFOLIO_RISK must be between 0 and 1")
        if cls.MAX_PORTFOLIO_RISK < cls.RISK_PER_CONDOR:
            errors.append("MAX_PORTFOLIO_RISK must be >= RISK_PER_CONDOR")

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
        print(f"API Key: {cls.DERIBIT_API_KEY[:8]}..." if cls.DERIBIT_API_KEY else "Not set")
        print(f"\nTrading Parameters:")
        print(f"  Initial Equity: ${cls.INITIAL_EQUITY:,.2f}")
        print(f"  Risk per Condor: {cls.RISK_PER_CONDOR:.1%}")
        print(f"  Max Portfolio Risk: {cls.MAX_PORTFOLIO_RISK:.1%}")
        print(f"\nExit Parameters:")
        print(f"  Take Profit: {cls.TP_RATIO:.1%} of credit")
        print(f"  Stop Loss: {cls.SL_MULT:.1f}x credit")
        print(f"  Close before expiry: {cls.CLOSE_BEFORE_EXPIRY_HOURS}h")
        print(f"\nStrategy Parameters:")
        print(f"  DTE Range: {cls.MIN_DTE}-{cls.MAX_DTE} days")
        print(f"  Min IV Percentile: {cls.MIN_IV_PERCENTILE:.0f}%")
        print(f"  Short Delta Target: {cls.SHORT_DELTA_TARGET:.2f}")
        print(f"  Wing Width: {cls.WING_WIDTH_PERCENT:.1%}")
        print(f"\nCurrencies: {', '.join(cls.CURRENCIES)}")
        print(f"\nSchedule:")
        print(f"  Daily Scan: {cls.DAILY_SCAN_TIME}")
        print(f"  Monitoring: Every {cls.MONITORING_INTERVAL_MINUTES} minutes")
        print("=" * 60)


if __name__ == "__main__":
    if Config.validate():
        Config.display()
    else:
        print("\n❌ Configuration validation failed")
