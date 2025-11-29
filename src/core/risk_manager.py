from typing import Dict, Optional, Tuple
import logging
from src.core.deribit_client import DeribitClient
from src.core.position_monitor import PositionMonitor

logger = logging.getLogger(__name__)


class RiskManager:
    """Manage risk and position sizing with compounding"""

    def __init__(
        self,
        client: DeribitClient,
        position_monitor: PositionMonitor,
        initial_equity: float,
        risk_per_condor: float = 0.01,
        max_portfolio_risk: float = 0.03
    ):
        """
        Initialize risk manager

        Args:
            client: Deribit API client
            position_monitor: Position monitor instance
            initial_equity: Initial account equity in USD
            risk_per_condor: Risk per condor as fraction of equity (e.g., 0.01 = 1%)
            max_portfolio_risk: Max total risk as fraction of equity (e.g., 0.03 = 3%)
        """
        self.client = client
        self.position_monitor = position_monitor
        self.initial_equity = initial_equity
        self.risk_per_condor = risk_per_condor
        self.max_portfolio_risk = max_portfolio_risk

    def get_current_equity(self, currency: str = "BTC") -> Optional[float]:
        """
        Get current account equity from Deribit

        Args:
            currency: Currency to check (BTC or ETH)

        Returns:
            Current equity in USD
        """
        try:
            account = self.client.get_account_summary(currency)

            if account:
                # Deribit returns equity in the currency (BTC/ETH)
                equity_in_currency = account.get("equity", 0)

                # Convert to USD
                spot_price = self.client.get_index_price(currency)
                if spot_price:
                    equity_usd = equity_in_currency * spot_price
                    logger.info(f"Current equity for {currency}: ${equity_usd:,.2f}")
                    return equity_usd

            logger.warning(f"Could not get equity for {currency}, using initial equity")
            return self.initial_equity

        except Exception as e:
            logger.error(f"Error getting equity: {e}")
            return self.initial_equity

    def get_total_equity(self) -> float:
        """
        Get total equity across all currencies (BTC + ETH)

        Returns:
            Total equity in USD
        """
        btc_equity = self.get_current_equity("BTC") or 0
        eth_equity = self.get_current_equity("ETH") or 0
        total = btc_equity + eth_equity

        logger.info(f"Total equity: ${total:,.2f} (BTC: ${btc_equity:,.2f}, ETH: ${eth_equity:,.2f})")
        return total

    def calculate_position_size(self, equity: Optional[float] = None) -> float:
        """
        Calculate risk amount per condor based on current equity

        Args:
            equity: Current equity (if None, will fetch from account)

        Returns:
            Risk amount in USD per condor
        """
        if equity is None:
            equity = self.get_total_equity()

        risk_amount = equity * self.risk_per_condor

        logger.info(f"Risk per condor: ${risk_amount:.2f} ({self.risk_per_condor:.1%} of ${equity:,.2f})")
        return risk_amount

    def can_open_new_position(self) -> Tuple[bool, str]:
        """
        Check if we can open a new position based on risk limits

        Returns:
            Tuple of (can_open, reason)
        """
        # Get current equity
        equity = self.get_total_equity()

        # Get current risk exposure
        current_risk = self.position_monitor.get_total_risk_exposure()

        # Calculate max allowed risk
        max_risk = equity * self.max_portfolio_risk

        # Calculate risk for new condor
        new_condor_risk = self.calculate_position_size(equity)

        # Check if adding new position would exceed limit
        total_risk_after = current_risk + new_condor_risk

        if total_risk_after > max_risk:
            return False, (
                f"Would exceed max portfolio risk: "
                f"${total_risk_after:.2f} > ${max_risk:.2f} "
                f"(current: ${current_risk:.2f})"
            )

        available_risk = max_risk - current_risk
        logger.info(
            f"Can open new position: "
            f"${available_risk:.2f} available of ${max_risk:.2f} max risk"
        )

        return True, f"Available risk: ${available_risk:.2f}"

    def get_max_condors_allowed(self) -> int:
        """
        Calculate maximum number of condors allowed based on current equity

        Returns:
            Maximum number of condors
        """
        equity = self.get_total_equity()
        max_total_risk = equity * self.max_portfolio_risk
        risk_per_condor = equity * self.risk_per_condor

        if risk_per_condor <= 0:
            return 0

        max_condors = int(max_total_risk / risk_per_condor)
        return max_condors

    def get_risk_summary(self) -> Dict:
        """
        Get comprehensive risk summary

        Returns:
            Dict with risk metrics
        """
        equity = self.get_total_equity()
        current_risk = self.position_monitor.get_total_risk_exposure()
        max_risk = equity * self.max_portfolio_risk
        risk_per_condor = self.calculate_position_size(equity)

        portfolio = self.position_monitor.get_portfolio_summary()

        summary = {
            "equity": equity,
            "initial_equity": self.initial_equity,
            "equity_change": equity - self.initial_equity,
            "equity_change_pct": ((equity - self.initial_equity) / self.initial_equity * 100)
                                if self.initial_equity > 0 else 0,
            "current_risk": current_risk,
            "max_risk": max_risk,
            "risk_utilization_pct": (current_risk / max_risk * 100) if max_risk > 0 else 0,
            "available_risk": max_risk - current_risk,
            "risk_per_condor": risk_per_condor,
            "max_condors_allowed": self.get_max_condors_allowed(),
            "current_condors": self.position_monitor.get_open_condor_count(),
            "total_pnl": portfolio["total_pnl"],
            "config": {
                "risk_per_condor_pct": self.risk_per_condor * 100,
                "max_portfolio_risk_pct": self.max_portfolio_risk * 100
            }
        }

        return summary

    def validate_trade(self, expected_max_loss: float) -> Tuple[bool, str]:
        """
        Validate if a trade meets risk requirements

        Args:
            expected_max_loss: Expected max loss of the trade

        Returns:
            Tuple of (is_valid, reason)
        """
        equity = self.get_total_equity()
        risk_per_condor = self.calculate_position_size(equity)

        # Check if trade risk is reasonable
        # Accept trades between 20% and 150% of target risk
        if expected_max_loss > risk_per_condor * 1.5:
            return False, (
                f"Trade risk ${expected_max_loss:.2f} too high "
                f"(target: ${risk_per_condor:.2f})"
            )

        if expected_max_loss < risk_per_condor * 0.2:
            return False, (
                f"Trade risk ${expected_max_loss:.2f} too low "
                f"(target: ${risk_per_condor:.2f}, minimum 20%)"
            )

        # Check portfolio risk limit
        can_open, reason = self.can_open_new_position()
        if not can_open:
            return False, reason

        return True, "Trade validated"

    def update_risk_parameters(self, risk_per_condor: float = None,
                              max_portfolio_risk: float = None):
        """
        Update risk parameters

        Args:
            risk_per_condor: New risk per condor (fraction)
            max_portfolio_risk: New max portfolio risk (fraction)
        """
        if risk_per_condor is not None:
            self.risk_per_condor = risk_per_condor
            logger.info(f"Updated risk per condor: {risk_per_condor:.1%}")

        if max_portfolio_risk is not None:
            self.max_portfolio_risk = max_portfolio_risk
            logger.info(f"Updated max portfolio risk: {max_portfolio_risk:.1%}")

    def emergency_stop(self) -> bool:
        """
        Emergency stop: close all positions

        Returns:
            True if successful
        """
        logger.warning("EMERGENCY STOP TRIGGERED - Closing all positions")

        try:
            # Cancel all pending orders first
            self.client.cancel_all()

            # Close all open condors
            for condor in list(self.position_monitor.open_condors.values()):
                from src.core.order_manager import OrderManager
                order_manager = OrderManager(self.client)
                order_manager.close_iron_condor(condor, "emergency_stop")

            logger.info("Emergency stop completed")
            return True

        except Exception as e:
            logger.error(f"Error during emergency stop: {e}")
            return False
    def calculate_futures_quantity(self, entry_price: float, sl_price: float, risk_pct: float = 0.01) -> float:
        """
        Calculate position size for futures/perpetuals based on risk percentage and stop loss.
        
        Formula: Quantity = (Equity * Risk_Pct) / |Entry - SL|
        Returns quantity in Base Currency (e.g., BTC).
        
        Args:
            entry_price: Entry price
            sl_price: Stop loss price
            risk_pct: Risk percentage (default 1%)
            
        Returns:
            Quantity in base currency (e.g. BTC)
        """
        equity = self.get_total_equity()
        
        if entry_price <= 0 or sl_price <= 0:
            logger.error("Invalid prices for size calculation")
            return 0.0
            
        price_diff = abs(entry_price - sl_price)
        if price_diff == 0:
             logger.error("Entry price equals SL price, cannot calculate size")
             return 0.0
             
        risk_amount = equity * risk_pct
        quantity = risk_amount / price_diff
        
        logger.info(f"Calculated Futures Size: {quantity:.4f} BTC (Equity: ${equity:.2f}, Risk: ${risk_amount:.2f}, Diff: ${price_diff:.2f})")
        
        return quantity
