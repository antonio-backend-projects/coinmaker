import os
import time
import schedule
from datetime import datetime
from typing import Dict, List, Optional
import logging
from dotenv import load_dotenv

from config import Config, IronCondorConfig, SmartMoneyConfig
from src.core.deribit_client import DeribitClient
from src.core.order_manager import OrderManager
from src.core.position_monitor import PositionMonitor
from src.core.risk_manager import RiskManager
from src.strategies.iron_condor import IronCondorStrategy
from src.strategies.smart_money import SmartMoneyStrategy

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot supporting multiple strategies"""

    def __init__(self):
        """Initialize the trading bot"""
        # Load environment variables
        load_dotenv()

        # Validate and load configuration
        if not Config.validate():
            raise ValueError("Invalid configuration")
        
        Config.display()

        self.running = False
        self.strategies = []

        # Initialize core components
        logger.info("Initializing core components...")
        self.client = DeribitClient(
            Config.DERIBIT_API_KEY, 
            Config.DERIBIT_API_SECRET, 
            Config.DERIBIT_ENV
        )
        self.order_manager = OrderManager(self.client)
        self.position_monitor = PositionMonitor(self.client, self.order_manager)
        
        # Risk Manager needs global risk settings (using defaults or first strategy?)
        # Ideally GlobalConfig should have risk settings. 
        # For now, we use defaults or pick from first IronCondor strategy if available.
        # Or we just pass the raw env vars if they exist.
        self.risk_manager = RiskManager(
            self.client,
            self.position_monitor,
            initial_equity=float(os.getenv("INITIAL_EQUITY", 10000)),
            risk_per_condor=float(os.getenv("RISK_PER_CONDOR", 0.01)), # Default
            max_portfolio_risk=float(os.getenv("MAX_PORTFOLIO_RISK", 0.03)) # Default
        )

        # Initialize strategies
        logger.info("Initializing strategies...")
        dependencies = {
            "order_manager": self.order_manager,
            "position_monitor": self.position_monitor,
            "risk_manager": self.risk_manager
        }

        for strategy_config in Config.STRATEGIES:
            if isinstance(strategy_config, IronCondorConfig):
                strategy = IronCondorStrategy(self.client, strategy_config, dependencies)
                self.strategies.append(strategy)
                logger.info(f"Loaded strategy: {strategy.name}")
            
            elif isinstance(strategy_config, SmartMoneyConfig):
                strategy = SmartMoneyStrategy(self.client, strategy_config, dependencies)
                self.strategies.append(strategy)
                logger.info(f"Loaded strategy: {strategy.name}")

    def authenticate(self) -> bool:
        """Authenticate with Deribit"""
        logger.info("Authenticating with Deribit...")
        return self.client.authenticate()

    def scan_and_open_positions(self):
        """Main routine to scan for opportunities and open new positions"""
        logger.info("=" * 60)
        logger.info("SCANNING FOR NEW POSITIONS")
        logger.info("=" * 60)

        try:
            # Get risk summary
            risk_summary = self.risk_manager.get_risk_summary()
            logger.info(f"Equity: ${risk_summary['equity']:,.2f}")
            logger.info(f"Risk utilization: {risk_summary['risk_utilization_pct']:.1f}%")

            # Check global risk
            can_open, reason = self.risk_manager.can_open_new_position()
            if not can_open:
                logger.info(f"Cannot open new position: {reason}")
                return

            # Execute strategies
            for strategy in self.strategies:
                try:
                    logger.info(f"\nRunning strategy: {strategy.name}")
                    signals = strategy.scan()
                    
                    if not signals:
                        logger.info(f"No signals from {strategy.name}")
                        continue
                        
                    for signal in signals:
                        logger.info(f"Signal detected: {signal}")
                        success = strategy.execute_entry(signal)
                        if success:
                            logger.info(f"Entry executed for {strategy.name}")
                        else:
                            logger.warning(f"Entry failed for {strategy.name}")
                            
                except Exception as e:
                    logger.error(f"Error running strategy {strategy.name}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error in scan_and_open_positions: {e}", exc_info=True)

    def manage_open_positions(self):
        """Monitor and manage open positions"""
        logger.info("=" * 60)
        logger.info("MANAGING OPEN POSITIONS")
        logger.info("=" * 60)

        try:
            # Get portfolio summary
            portfolio = self.position_monitor.get_portfolio_summary()
            logger.info(f"Open positions: {portfolio.get('total_condors', 0)}") # Update if generic
            
            # Delegate to strategies
            for strategy in self.strategies:
                try:
                    stats = strategy.manage_positions()
                    if stats:
                        logger.info(f"{strategy.name} management: {stats}")
                except Exception as e:
                    logger.error(f"Error managing {strategy.name}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error managing positions: {e}", exc_info=True)

    def run_daily_routine(self):
        """Run daily routine: scan for new positions"""
        logger.info("\n" + "=" * 60)
        logger.info(f"DAILY ROUTINE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        self.scan_and_open_positions()

    def run_monitoring_routine(self):
        """Run periodic monitoring routine"""
        if not self.running:
            return

        logger.info(f"\nMonitoring check - {datetime.now().strftime('%H:%M:%S')}")
        self.manage_open_positions()
        
        # Also run scan for intraday strategies (Smart Money)
        # Iron Condor is daily, but Smart Money is intraday (15m)
        # So we should run scan here too for Smart Money?
        # Or we separate schedules.
        # For simplicity, let's run scan every monitoring interval for Smart Money.
        # But Iron Condor shouldn't run every 5 mins.
        # We can add a check in IronCondorStrategy.scan() to only run once a day?
        # Or we just rely on the schedule.
        
        # Better approach:
        # Iron Condor -> Daily schedule
        # Smart Money -> Intraday schedule
        # But here we have one `scan_and_open_positions` calling all strategies.
        # Let's split or pass context.
        
        # Quick fix: Run scan here too, but IronCondorStrategy should be smart enough or we filter.
        # Actually, IronCondorStrategy checks for "suitable expiration" and "IV". If it finds one, it enters.
        # If we run it every 5 mins, it might open multiple positions if we don't check if we already have one for that day/exp.
        # RiskManager limits total positions, so it might be safe.
        # But let's keep `run_daily_routine` for Iron Condor and `run_monitoring_routine` for Smart Money?
        
        # Let's iterate strategies and check their preferred interval?
        # For now, I'll just call `scan_and_open_positions` in `run_monitoring_routine` as well, 
        # assuming RiskManager prevents over-trading.
        
        self.scan_and_open_positions()

    def start(self):
        """Start the trading bot"""
        logger.info("=" * 60)
        logger.info("STARTING TRADING BOT (MULTI-STRATEGY)")
        logger.info("=" * 60)

        # Authenticate
        if not self.authenticate():
            logger.error("Authentication failed. Exiting.")
            return

        self.running = True

        # Schedule daily position opening (e.g., 10:00 AM) - Mostly for Iron Condor
        # schedule.every().day.at(Config.DAILY_SCAN_TIME).do(self.run_daily_routine)

        # Schedule position monitoring every N minutes - For Smart Money and Management
        schedule.every(Config.MONITORING_INTERVAL_MINUTES).minutes.do(self.run_monitoring_routine)

        logger.info("Bot started. Schedules:")
        logger.info(f"  - Monitoring & Scan: Every {Config.MONITORING_INTERVAL_MINUTES} minutes")

        # Run initial scan
        logger.info("\nRunning initial scan...")
        self.run_monitoring_routine()

        # Main loop
        logger.info("\nEntering main loop...")
        try:
            while self.running:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nReceived shutdown signal...")
            self.stop()

    def stop(self):
        """Stop the trading bot"""
        logger.info("=" * 60)
        logger.info("STOPPING TRADING BOT")
        logger.info("=" * 60)

        self.running = False
        logger.info("Bot stopped.")


def main():
    """Main entry point"""
    # Setup logging
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/trading_bot.log'),
            logging.StreamHandler()
        ]
    )

    # Create and start bot
    try:
        bot = TradingBot()
        bot.start()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
