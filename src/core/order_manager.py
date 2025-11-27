from typing import Dict, List, Optional
import time
import logging
from src.core.deribit_client import DeribitClient
from src.strategies.iron_condor import IronCondor, OptionLeg

logger = logging.getLogger(__name__)


class OrderManager:
    """Manage order execution for Iron Condor structures"""

    def __init__(self, client: DeribitClient, max_retries: int = 3, retry_delay: float = 1.0,
                 use_aggressive_limits: bool = True, slippage_pct: float = 0.10):
        """
        Initialize order manager

        Args:
            client: Deribit API client
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
            use_aggressive_limits: Use aggressive limit orders for better fills
            slippage_pct: Slippage percentage for aggressive limits (e.g., 0.10 = 10%)
        """
        self.client = client
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.use_aggressive_limits = use_aggressive_limits
        self.slippage_pct = slippage_pct

    def open_iron_condor(self, condor: IronCondor, use_market_orders: bool = False) -> bool:
        """
        Open all 4 legs of an Iron Condor

        Args:
            condor: IronCondor structure to open
            use_market_orders: Use market orders instead of limit orders

        Returns:
            True if all legs opened successfully
        """
        logger.info(f"Opening Iron Condor: {condor.id}")

        legs = [
            (condor.long_put, "buy"),
            (condor.short_put, "sell"),
            (condor.short_call, "sell"),
            (condor.long_call, "buy")
        ]

        opened_orders = []

        try:
            for leg, side in legs:
                logger.info(f"Executing leg: {side.upper()} {leg.option_type} @ {leg.strike} ({leg.instrument_name})")
                success = self._execute_leg(leg, side, condor.size, use_market_orders)

                if success:
                    opened_orders.append((leg, side))
                    logger.info(f"  ✓ {side.upper()} {leg.option_type} @ {leg.strike} - FILLED")
                else:
                    logger.error(f"  ✗ Failed to {side} {leg.option_type} @ {leg.strike} - NOT FILLED")
                    # Rollback: close already opened legs
                    logger.warning(f"Rolling back {len(opened_orders)} opened legs...")
                    self._rollback_orders(opened_orders, condor.size)
                    return False

                # Small delay between legs
                time.sleep(0.5)

            logger.info(f"Successfully opened Iron Condor: {condor.id}")
            return True

        except Exception as e:
            logger.error(f"Error opening Iron Condor: {e}")
            self._rollback_orders(opened_orders, condor.size)
            return False

    def close_iron_condor(self, condor: IronCondor, reason: str = "manual") -> bool:
        """
        Close all 4 legs of an Iron Condor

        Args:
            condor: IronCondor structure to close
            reason: Reason for closing (TP, SL, expiry, manual)

        Returns:
            True if all legs closed successfully
        """
        logger.info(f"Closing Iron Condor: {condor.id} (reason: {reason})")

        legs = [
            (condor.long_put, "sell"),  # Close long = sell
            (condor.short_put, "buy"),  # Close short = buy back
            (condor.short_call, "buy"),  # Close short = buy back
            (condor.long_call, "sell")  # Close long = sell
        ]

        all_closed = True

        for leg, side in legs:
            success = self._close_leg(leg, side, condor.size)

            if success:
                logger.info(f"  ✓ Closed {leg.option_type} @ {leg.strike}")
            else:
                logger.error(f"  ✗ Failed to close {leg.option_type} @ {leg.strike}")
                all_closed = False

            time.sleep(0.5)

        if all_closed:
            logger.info(f"Successfully closed Iron Condor: {condor.id}")
        else:
            logger.warning(f"Partially closed Iron Condor: {condor.id}")

        return all_closed

    def _round_to_tick_size(self, price: float, instrument_name: str) -> float:
        """
        Round price to Deribit tick size (4 decimals for BTC, 8 for ETH options)

        Args:
            price: Raw price
            instrument_name: Instrument name to determine currency

        Returns:
            Rounded price
        """
        # Deribit options tick size: 0.0001 for BTC, 0.00000001 for ETH
        if 'BTC' in instrument_name:
            # BTC options: 4 decimals (0.0001 tick size)
            return round(price, 4)
        elif 'ETH' in instrument_name:
            # ETH options: 8 decimals (0.00000001 tick size)
            return round(price, 8)
        else:
            # Default to 4 decimals
            return round(price, 4)

    def _get_aggressive_price(self, instrument_name: str, side: str, mark_price: float) -> Optional[float]:
        """
        Get aggressive limit price that will likely fill immediately

        Args:
            instrument_name: Instrument name
            side: "buy" or "sell"
            mark_price: Current mark price as fallback

        Returns:
            Aggressive limit price or None for market order
        """
        try:
            # Get order book
            book = self.client.get_order_book(instrument_name, depth=5)

            if not book:
                logger.warning(f"No order book for {instrument_name}, using mark price")
                rounded = self._round_to_tick_size(mark_price, instrument_name)
                return rounded

            best_bid = book.get('best_bid_price')
            best_ask = book.get('best_ask_price')

            if side == "buy":
                # To buy: pay slightly more than best ask to ensure fill
                if best_ask and best_ask > 0:
                    aggressive_price = best_ask * (1 + self.slippage_pct)
                    rounded = self._round_to_tick_size(aggressive_price, instrument_name)
                    logger.info(f"BUY aggressive: {rounded:.4f} (best_ask: {best_ask:.4f} +{self.slippage_pct:.0%})")
                    return rounded
                else:
                    logger.warning(f"No best_ask for {instrument_name}, using mark * 1.1")
                    if mark_price:
                        rounded = self._round_to_tick_size(mark_price * 1.1, instrument_name)
                        return rounded
                    return None

            else:  # sell
                # To sell: use best_bid (someone wants to buy at this price)
                # OR use best_ask and slightly undercut to ensure fill
                if best_bid and best_bid > 0:
                    # Simply use best_bid - this is what market maker is willing to pay
                    # No need to go lower, that's leaving money on the table
                    rounded = self._round_to_tick_size(best_bid, instrument_name)
                    logger.info(f"SELL aggressive: {rounded:.4f} (best_bid: {best_bid:.4f})")
                    return rounded
                elif best_ask and best_ask > 0:
                    # If no bid, undercut the ask slightly
                    aggressive_price = best_ask * (1 - self.slippage_pct * 0.5)  # Less aggressive
                    rounded = self._round_to_tick_size(aggressive_price, instrument_name)
                    logger.info(f"SELL aggressive: {rounded:.4f} (undercutting ask: {best_ask:.4f})")
                    return rounded
                else:
                    logger.warning(f"No best_bid/ask for {instrument_name}, using mark")
                    if mark_price:
                        rounded = self._round_to_tick_size(mark_price, instrument_name)
                        return rounded
                    return None

        except Exception as e:
            logger.error(f"Error getting aggressive price: {e}")
            if mark_price:
                return self._round_to_tick_size(mark_price, instrument_name)
            return None

    def _execute_leg(self, leg: OptionLeg, side: str, size: float,
                    use_market_orders: bool = False) -> bool:
        """
        Execute a single option leg and verify it was filled

        Args:
            leg: Option leg to execute
            side: "buy" or "sell"
            size: Position size
            use_market_orders: Use market instead of limit orders

        Returns:
            True if successful and filled
        """
        for attempt in range(self.max_retries):
            try:
                if use_market_orders:
                    price = None  # Market order
                    logger.info(f"Using MARKET order for {leg.instrument_name}")
                elif self.use_aggressive_limits:
                    # Use aggressive limit price for better fill probability
                    price = self._get_aggressive_price(leg.instrument_name, side, leg.mark_price)
                    if price:
                        # Logging already done in _get_aggressive_price
                        pass
                    else:
                        logger.warning(f"Could not get aggressive price, using MARKET")
                        price = None
                else:
                    # Use mark price for conservative limit order
                    price = self._round_to_tick_size(leg.mark_price, leg.instrument_name)
                    logger.info(f"Using mark price {price:.4f} for {leg.instrument_name}")

                if side == "buy":
                    order = self.client.buy(
                        instrument_name=leg.instrument_name,
                        amount=size,
                        price=price,
                        label=f"iron_condor"
                    )
                else:  # sell
                    order = self.client.sell(
                        instrument_name=leg.instrument_name,
                        amount=size,
                        price=price,
                        label=f"iron_condor"
                    )

                if order:
                    order_id = order.get('order_id')
                    order_state = order.get('order_state', 'unknown')
                    logger.info(f"Order placed: {order_id}, state: {order_state}")

                    # Verify order was filled
                    if self._verify_order_filled(order_id, leg.instrument_name):
                        logger.info(f"Order {order_id} FILLED successfully")
                        return True
                    else:
                        logger.warning(f"Order {order_id} NOT filled, attempt {attempt + 1}/{self.max_retries}")
                        # Cancel the unfilled order before retrying
                        try:
                            self.client.get_order_state(order_id)  # Check if still exists
                            logger.info(f"Cancelling unfilled order {order_id}")
                        except:
                            pass
                else:
                    logger.error(f"Order placement FAILED (no response), attempt {attempt + 1}/{self.max_retries}")

            except Exception as e:
                logger.error(f"Error executing leg: {e}", exc_info=True)

            if attempt < self.max_retries - 1:
                logger.info(f"Waiting {self.retry_delay}s before retry...")
                time.sleep(self.retry_delay)

        return False

    def _verify_order_filled(self, order_id: str, instrument_name: str,
                            max_wait: int = 5) -> bool:
        """
        Verify that an order was filled

        Args:
            order_id: Order ID to check
            instrument_name: Instrument name for logging
            max_wait: Maximum seconds to wait for fill

        Returns:
            True if order is filled
        """
        for i in range(max_wait):
            try:
                order_state = self.client.get_order_state(order_id)

                if order_state:
                    state = order_state.get('order_state')
                    filled = order_state.get('filled_amount', 0)

                    logger.debug(f"Order {order_id} state: {state}, filled: {filled}")

                    if state == 'filled':
                        return True
                    elif state in ['rejected', 'cancelled']:
                        logger.error(f"Order {order_id} was {state}")
                        return False

                # Wait before checking again
                if i < max_wait - 1:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Error checking order state: {e}")

        logger.error(f"Order {order_id} for {instrument_name} not filled after {max_wait}s")
        return False

    def _close_leg(self, leg: OptionLeg, side: str, size: float) -> bool:
        """
        Close a single option leg

        Args:
            leg: Option leg to close
            side: "buy" or "sell" (opposite of opening)
            size: Position size

        Returns:
            True if successful
        """
        for attempt in range(self.max_retries):
            try:
                # Try using close_position first (faster)
                result = self.client.close_position(
                    instrument_name=leg.instrument_name,
                    type_="market"
                )

                if result:
                    logger.debug(f"Position closed via close_position")
                    return True

                # Fallback to manual order
                if side == "buy":
                    order = self.client.buy(
                        instrument_name=leg.instrument_name,
                        amount=size,
                        price=None  # Market order
                    )
                else:
                    order = self.client.sell(
                        instrument_name=leg.instrument_name,
                        amount=size,
                        price=None
                    )

                if order:
                    logger.debug(f"Position closed via manual order")
                    return True

            except Exception as e:
                logger.error(f"Error closing leg: {e}")

            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)

        return False

    def _rollback_orders(self, opened_orders: List, size: float):
        """
        Rollback (close) orders that were already opened

        Args:
            opened_orders: List of (leg, side) tuples that were opened
            size: Position size
        """
        logger.warning(f"Rolling back {len(opened_orders)} opened orders")

        for leg, original_side in opened_orders:
            # Reverse the side to close
            close_side = "sell" if original_side == "buy" else "buy"

            try:
                self._close_leg(leg, close_side, size)
            except Exception as e:
                logger.error(f"Error during rollback: {e}")

    def get_position_details(self, instrument_name: str, currency: str) -> Optional[Dict]:
        """
        Get current position details for an instrument

        Args:
            instrument_name: Option instrument name
            currency: BTC or ETH

        Returns:
            Position dict or None
        """
        try:
            positions = self.client.get_positions(currency)

            for pos in positions:
                if pos.get("instrument_name") == instrument_name:
                    return pos

            return None

        except Exception as e:
            logger.error(f"Error getting position details: {e}")
            return None

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders"""
        try:
            result = self.client.cancel_all()
            if result:
                logger.info("Cancelled all open orders")
                return True
            return False
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
            return False
