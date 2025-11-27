import os
import time
import schedule
from datetime import datetime
from typing import Dict, List, Optional
import logging
from dotenv import load_dotenv

from src.core.deribit_client import DeribitClient
from src.core.order_manager import OrderManager
from src.core.position_monitor import PositionMonitor
from src.core.risk_manager import RiskManager
from src.strategies.iron_condor import IronCondorBuilder
from src.utils.volatility import VolatilityAnalyzer

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot for Iron Condor strategy on Deribit"""

    def __init__(self):
        """Initialize the trading bot"""
        # Load environment variables
        load_dotenv()

        # Configuration
        self.api_key = os.getenv("DERIBIT_API_KEY")
        self.api_secret = os.getenv("DERIBIT_API_SECRET")
        self.env = os.getenv("DERIBIT_ENV", "test")

        self.initial_equity = float(os.getenv("INITIAL_EQUITY", 10000))
        self.risk_per_condor = float(os.getenv("RISK_PER_CONDOR", 0.01))
        self.max_portfolio_risk = float(os.getenv("MAX_PORTFOLIO_RISK", 0.03))
        self.tp_ratio = float(os.getenv("TP_RATIO", 0.55))
        self.sl_mult = float(os.getenv("SL_MULT", 1.2))
        self.min_dte = int(os.getenv("MIN_DTE", 7))
        self.max_dte = int(os.getenv("MAX_DTE", 10))
        self.min_iv_percentile = float(os.getenv("MIN_IV_PERCENTILE", 30))
        self.close_before_expiry_hours = int(os.getenv("CLOSE_BEFORE_EXPIRY_HOURS", 24))
        self.short_delta_target = float(os.getenv("SHORT_DELTA_TARGET", 0.12))
        self.wing_width_percent = float(os.getenv("WING_WIDTH_PERCENT", 0.05))

        self.currencies = ["BTC", "ETH"]
        self.running = False

        # Initialize components
        logger.info("Initializing trading bot...")
        self.client = DeribitClient(self.api_key, self.api_secret, self.env)
        self.order_manager = OrderManager(self.client)
        self.position_monitor = PositionMonitor(self.client, self.order_manager)
        self.risk_manager = RiskManager(
            self.client,
            self.position_monitor,
            self.initial_equity,
            self.risk_per_condor,
            self.max_portfolio_risk
        )
        self.condor_builder = IronCondorBuilder(
            self.short_delta_target,
            self.wing_width_percent
        )
        self.volatility_analyzer = VolatilityAnalyzer(lookback_days=30)

        logger.info(f"Bot initialized for {self.env} environment")
        logger.info(f"Currencies: {', '.join(self.currencies)}")
        logger.info(f"Risk per condor: {self.risk_per_condor:.1%}")
        logger.info(f"Max portfolio risk: {self.max_portfolio_risk:.1%}")

    def authenticate(self) -> bool:
        """Authenticate with Deribit"""
        logger.info("Authenticating with Deribit...")
        return self.client.authenticate()

    def find_suitable_expiration(self, currency: str) -> Optional[str]:
        """
        Find suitable expiration date within DTE range

        Args:
            currency: BTC or ETH

        Returns:
            Expiration date string or None
        """
        try:
            instruments = self.client.get_instruments(currency, kind="option")

            # Get unique expiration dates
            expirations = {}
            for inst in instruments:
                exp_date = inst.get("expiration_timestamp")
                if exp_date:
                    exp_dt = datetime.fromtimestamp(exp_date / 1000)
                    days = (exp_dt - datetime.now()).days

                    if self.min_dte <= days <= self.max_dte:
                        exp_str = exp_dt.strftime("%d%b%y").upper()
                        if exp_str not in expirations:
                            expirations[exp_str] = days

            if not expirations:
                logger.warning(f"No suitable expirations found for {currency}")
                return None

            # Return closest to min_dte
            best_exp = min(expirations.items(), key=lambda x: abs(x[1] - self.min_dte))
            logger.info(f"Selected expiration for {currency}: {best_exp[0]} ({best_exp[1]} DTE)")
            return best_exp[0]

        except Exception as e:
            logger.error(f"Error finding expiration: {e}")
            return None

    def get_options_chain_with_greeks(self, currency: str, expiration: str) -> List[Dict]:
        """
        Get options chain with greeks for a specific expiration

        Args:
            currency: BTC or ETH
            expiration: Expiration date string

        Returns:
            List of options with greeks
        """
        try:
            instruments = self.client.get_instruments(currency, kind="option")

            # Filter by expiration
            options = []
            for inst in instruments:
                exp_timestamp = inst.get("expiration_timestamp")
                if exp_timestamp:
                    exp_dt = datetime.fromtimestamp(exp_timestamp / 1000)
                    exp_str = exp_dt.strftime("%d%b%y").upper()

                    if exp_str == expiration:
                        # Get order book for mark price and greeks
                        book = self.client.get_order_book(inst["instrument_name"])
                        if book:
                            inst["mark_price"] = book.get("mark_price", 0)
                            inst["mark_iv"] = book.get("mark_iv", 0)
                            inst["greeks"] = book.get("greeks", {})
                            options.append(inst)

                        # Small delay to avoid rate limiting
                        time.sleep(0.05)

            logger.info(f"Retrieved {len(options)} options for {currency} {expiration}")
            return options

        except Exception as e:
            logger.error(f"Error getting options chain: {e}")
            return []

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
            logger.info(f"Open condors: {risk_summary['current_condors']}/{risk_summary['max_condors_allowed']}")

            # Check if we can open new positions
            can_open, reason = self.risk_manager.can_open_new_position()
            if not can_open:
                logger.info(f"Cannot open new position: {reason}")
                return

            # Try to open position for each currency
            for currency in self.currencies:
                logger.info(f"\nChecking {currency}...")

                # Get spot price
                spot_price = self.client.get_index_price(currency)
                if not spot_price:
                    logger.warning(f"Could not get spot price for {currency}")
                    continue

                logger.info(f"Spot price: ${spot_price:,.2f}")

                # Find suitable expiration
                expiration = self.find_suitable_expiration(currency)
                if not expiration:
                    continue

                # Get options chain
                options = self.get_options_chain_with_greeks(currency, expiration)
                if not options:
                    logger.warning(f"No options data for {currency}")
                    continue

                # Get ATM IV
                atm_iv = self.volatility_analyzer.get_atm_iv(options, spot_price)
                if not atm_iv:
                    logger.warning(f"Could not calculate IV for {currency}")
                    continue

                logger.info(f"ATM IV: {atm_iv:.2%}")

                # Update IV history
                self.volatility_analyzer.update_iv_history(currency, atm_iv)

                # Check if conditions are good to enter
                should_enter, iv_reason = self.volatility_analyzer.should_enter_position(
                    currency, atm_iv, min_iv_percentile=self.min_iv_percentile
                )

                if not should_enter:
                    logger.info(f"Skipping {currency}: {iv_reason}")
                    continue

                logger.info(f"IV conditions met: {iv_reason}")

                # Calculate position size
                risk_amount = self.risk_manager.calculate_position_size()

                # Build Iron Condor
                condor = self.condor_builder.build_condor(
                    currency=currency,
                    options_chain=options,
                    spot_price=spot_price,
                    expiration_date=expiration,
                    risk_per_condor=risk_amount,
                    tp_ratio=self.tp_ratio,
                    sl_mult=self.sl_mult
                )

                if not condor:
                    logger.error(f"Failed to build condor for {currency}")
                    continue

                # Validate trade
                is_valid, validation_reason = self.risk_manager.validate_trade(condor.max_loss)
                if not is_valid:
                    logger.warning(f"Trade validation failed: {validation_reason}")
                    continue

                # Open the condor
                # Use market orders for testnet (low liquidity), aggressive limits for live
                use_market = (self.env == "test")
                logger.info(f"Opening Iron Condor for {currency}... (using {'MARKET' if use_market else 'LIMIT'} orders)")
                success = self.order_manager.open_iron_condor(condor, use_market_orders=use_market)

                if success:
                    self.position_monitor.add_condor(condor)
                    logger.info(f"✓ Successfully opened condor {condor.id}")
                else:
                    logger.error(f"✗ Failed to open condor")

                # Check if we can still open more
                can_open, _ = self.risk_manager.can_open_new_position()
                if not can_open:
                    logger.info("Risk limit reached, stopping scan")
                    break

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
            logger.info(f"Open condors: {portfolio['total_condors']}")
            logger.info(f"Total P&L: ${portfolio['total_pnl']:.2f}")

            if portfolio['total_condors'] == 0:
                logger.info("No open positions to manage")
                return

            # Show individual positions
            for condor_info in portfolio['condors']:
                logger.info(f"\n  {condor_info['id']}")
                logger.info(f"    Currency: {condor_info['currency']}")
                logger.info(f"    P&L: ${condor_info['pnl']:.2f} / ${condor_info['credit']:.2f}")
                logger.info(f"    Targets: TP ${condor_info['tp_target']:.2f}, SL ${condor_info['sl_target']:.2f}")

            # Monitor and execute TP/SL
            stats = self.position_monitor.monitor_positions(self.close_before_expiry_hours)

            logger.info(f"\nMonitoring results:")
            logger.info(f"  Closed (TP): {stats['closed_tp']}")
            logger.info(f"  Closed (SL): {stats['closed_sl']}")
            logger.info(f"  Closed (Expiry): {stats['closed_expiry']}")
            logger.info(f"  Errors: {stats['errors']}")

            if stats['closed_tp'] + stats['closed_sl'] + stats['closed_expiry'] > 0:
                logger.info(f"  Total realized P&L: ${stats['total_pnl']:.2f}")

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

    def start(self):
        """Start the trading bot"""
        logger.info("=" * 60)
        logger.info("STARTING TRADING BOT")
        logger.info("=" * 60)

        # Authenticate
        if not self.authenticate():
            logger.error("Authentication failed. Exiting.")
            return

        self.running = True

        # Schedule daily position opening (e.g., 10:00 AM)
        schedule.every().day.at("10:00").do(self.run_daily_routine)

        # Schedule position monitoring every 5 minutes
        schedule.every(5).minutes.do(self.run_monitoring_routine)

        logger.info("Bot started. Schedules:")
        logger.info("  - Daily scan: 10:00 AM")
        logger.info("  - Position monitoring: Every 5 minutes")

        # Run initial scan
        logger.info("\nRunning initial scan...")
        self.run_daily_routine()
        self.manage_open_positions()

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

        # Show final summary
        risk_summary = self.risk_manager.get_risk_summary()
        logger.info(f"Final equity: ${risk_summary['equity']:,.2f}")
        logger.info(f"Total P&L: ${risk_summary['equity_change']:,.2f} ({risk_summary['equity_change_pct']:.2f}%)")
        logger.info(f"Open positions: {risk_summary['current_condors']}")

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
    bot = TradingBot()
    bot.start()


if __name__ == "__main__":
    main()
