from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from src.core.deribit_client import DeribitClient
from src.strategies.iron_condor import IronCondor
from src.core.order_manager import OrderManager

logger = logging.getLogger(__name__)


class PositionMonitor:
    """Monitor open Iron Condor positions and manage TP/SL"""

    def __init__(self, client: DeribitClient, order_manager: OrderManager):
        """
        Initialize position monitor

        Args:
            client: Deribit API client
            order_manager: Order manager for closing positions
        """
        self.client = client
        self.order_manager = order_manager
        self.open_condors: Dict[str, IronCondor] = {}

    def add_condor(self, condor: IronCondor):
        """Add a new Iron Condor to monitor"""
        self.open_condors[condor.id] = condor
        logger.info(f"Added condor {condor.id} to monitoring")

    def remove_condor(self, condor_id: str):
        """Remove an Iron Condor from monitoring"""
        if condor_id in self.open_condors:
            del self.open_condors[condor_id]
            logger.info(f"Removed condor {condor_id} from monitoring")

    def get_condor_pnl(self, condor: IronCondor) -> Optional[float]:
        """
        Calculate current P&L for an Iron Condor

        Args:
            condor: IronCondor to calculate P&L for

        Returns:
            Current P&L in USD or None if error
        """
        try:
            total_current_value = 0.0

            # Get current mark prices for all legs
            legs = [
                (condor.long_put, "buy"),
                (condor.short_put, "sell"),
                (condor.short_call, "sell"),
                (condor.long_call, "buy")
            ]

            spot_price = self.client.get_index_price(condor.currency)
            if not spot_price:
                logger.error(f"Could not get spot price for {condor.currency}")
                return None

            for leg, direction in legs:
                # Get current order book to get mark price
                book = self.client.get_order_book(leg.instrument_name, depth=1)

                if not book:
                    logger.warning(f"Could not get order book for {leg.instrument_name}")
                    continue

                current_mark = book.get("mark_price", leg.mark_price)

                # Calculate value change
                if direction == "buy":
                    # We bought this, so increase in price is good
                    value = current_mark * condor.size * spot_price
                else:  # sell
                    # We sold this, so decrease in price is good
                    value = -current_mark * condor.size * spot_price

                total_current_value += value

            # P&L = credit received + current value (negative for what we need to buy back)
            pnl = condor.credit_received + total_current_value

            return round(pnl, 2)

        except Exception as e:
            logger.error(f"Error calculating P&L for {condor.id}: {e}")
            return None

    def check_exit_conditions(self, condor: IronCondor, hours_before_expiry: int = 24) -> Tuple[bool, str]:
        """
        Check if any exit conditions are met for a condor

        Args:
            condor: IronCondor to check
            hours_before_expiry: Hours before expiry to force close

        Returns:
            Tuple of (should_exit, reason)
        """
        # Check time to expiration
        try:
            exp_dt = datetime.strptime(condor.expiration_date, "%d%b%y")
            time_to_expiry = exp_dt - datetime.now()

            if time_to_expiry.total_seconds() < (hours_before_expiry * 3600):
                return True, "expiry"
        except Exception as e:
            logger.error(f"Error parsing expiration date: {e}")

        # Check P&L
        pnl = self.get_condor_pnl(condor)

        if pnl is None:
            logger.warning(f"Could not calculate P&L for {condor.id}")
            return False, ""

        # Check take profit
        if pnl >= condor.take_profit_target:
            return True, f"take_profit (P&L: ${pnl:.2f})"

        # Check stop loss
        if pnl <= condor.stop_loss_target:
            return True, f"stop_loss (P&L: ${pnl:.2f})"

        return False, ""

    def monitor_positions(self, close_before_expiry_hours: int = 24) -> Dict[str, any]:
        """
        Monitor all open positions and execute TP/SL

        Args:
            close_before_expiry_hours: Hours before expiry to force close

        Returns:
            Dict with monitoring statistics
        """
        stats = {
            "total_monitored": len(self.open_condors),
            "closed_tp": 0,
            "closed_sl": 0,
            "closed_expiry": 0,
            "total_pnl": 0.0,
            "errors": 0
        }

        condors_to_close = []

        # Check each condor
        for condor_id, condor in self.open_condors.items():
            try:
                should_exit, reason = self.check_exit_conditions(
                    condor, close_before_expiry_hours
                )

                if should_exit:
                    pnl = self.get_condor_pnl(condor)
                    logger.info(f"Exit condition met for {condor_id}: {reason}, P&L: ${pnl:.2f}")
                    condors_to_close.append((condor, reason))

            except Exception as e:
                logger.error(f"Error monitoring condor {condor_id}: {e}")
                stats["errors"] += 1

        # Close condors that need to be closed
        for condor, reason in condors_to_close:
            try:
                success = self.order_manager.close_iron_condor(condor, reason)

                if success:
                    pnl = self.get_condor_pnl(condor)
                    condor.realized_pnl = pnl
                    condor.close_time = datetime.now()
                    condor.close_reason = reason
                    condor.status = "closed"

                    # Update stats
                    if "take_profit" in reason:
                        stats["closed_tp"] += 1
                    elif "stop_loss" in reason:
                        stats["closed_sl"] += 1
                    elif "expiry" in reason:
                        stats["closed_expiry"] += 1

                    stats["total_pnl"] += pnl if pnl else 0

                    # Remove from monitoring
                    self.remove_condor(condor.id)

                    logger.info(f"Closed condor {condor.id}: {reason}, P&L: ${pnl:.2f}")
                else:
                    logger.error(f"Failed to close condor {condor.id}")
                    stats["errors"] += 1

            except Exception as e:
                logger.error(f"Error closing condor {condor.id}: {e}")
                stats["errors"] += 1

        return stats

    def get_portfolio_summary(self) -> Dict:
        """
        Get summary of all open positions

        Returns:
            Dict with portfolio statistics
        """
        summary = {
            "total_condors": len(self.open_condors),
            "total_pnl": 0.0,
            "total_risk": 0.0,
            "by_currency": {},
            "condors": []
        }

        for condor in self.open_condors.values():
            pnl = self.get_condor_pnl(condor)

            if pnl:
                summary["total_pnl"] += pnl

            summary["total_risk"] += condor.max_loss

            # By currency
            if condor.currency not in summary["by_currency"]:
                summary["by_currency"][condor.currency] = {
                    "count": 0,
                    "pnl": 0.0,
                    "risk": 0.0
                }

            summary["by_currency"][condor.currency]["count"] += 1
            summary["by_currency"][condor.currency]["pnl"] += pnl if pnl else 0
            summary["by_currency"][condor.currency]["risk"] += condor.max_loss

            # Individual condor info
            summary["condors"].append({
                "id": condor.id,
                "currency": condor.currency,
                "expiration": condor.expiration_date,
                "entry_time": condor.entry_time.isoformat(),
                "pnl": pnl,
                "max_loss": condor.max_loss,
                "credit": condor.credit_received,
                "tp_target": condor.take_profit_target,
                "sl_target": condor.stop_loss_target
            })

        return summary

    def get_open_condor_count(self) -> int:
        """Get number of open condors"""
        return len(self.open_condors)

    def get_total_risk_exposure(self) -> float:
        """Get total risk across all open condors"""
        return sum(condor.max_loss for condor in self.open_condors.values())
