from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OptionLeg:
    """Represents a single option leg in the condor"""
    instrument_name: str
    strike: float
    option_type: str  # "call" or "put"
    direction: str  # "buy" or "sell"
    delta: float
    mark_price: float
    mark_iv: float


@dataclass
class IronCondor:
    """Represents a complete Iron Condor structure"""
    id: str
    currency: str
    expiration_date: str
    spot_price: float
    entry_time: datetime

    # Put spread (lower)
    long_put: OptionLeg
    short_put: OptionLeg

    # Call spread (upper)
    short_call: OptionLeg
    long_call: OptionLeg

    # Financial details
    credit_received: float
    max_loss: float
    max_profit: float
    size: float  # Position size multiplier

    # Risk parameters
    take_profit_target: float
    stop_loss_target: float

    # Status
    status: str = "open"  # open, closed, expired
    close_time: Optional[datetime] = None
    close_reason: Optional[str] = None
    realized_pnl: Optional[float] = None


class IronCondorBuilder:
    """Build Iron Condor structures from options chain"""

    def __init__(self, short_delta_target: float = 0.12, wing_width_percent: float = 0.05):
        """
        Initialize Iron Condor builder

        Args:
            short_delta_target: Target delta for short options (e.g., 0.12 = 12 delta)
            wing_width_percent: Width of protective wings as % of spot (e.g., 0.05 = 5%)
        """
        self.short_delta_target = short_delta_target
        self.wing_width_percent = wing_width_percent

    def find_strike_by_delta(self, options: List[Dict], target_delta: float,
                            option_type: str, tolerance: float = 0.05) -> Optional[Dict]:
        """
        Find option strike closest to target delta

        Args:
            options: List of option instruments with greeks
            target_delta: Target delta (absolute value)
            option_type: "call" or "put"
            tolerance: Maximum delta difference to accept

        Returns:
            Option dict or None
        """
        target_delta = abs(target_delta)
        candidates = []

        for opt in options:
            if opt.get("option_type") != option_type:
                continue

            delta = opt.get("greeks", {}).get("delta")
            if delta is None:
                continue

            delta = abs(delta)
            delta_diff = abs(delta - target_delta)

            if delta_diff <= tolerance:
                candidates.append({
                    "option": opt,
                    "delta": delta,
                    "delta_diff": delta_diff
                })

        if not candidates:
            logger.warning(f"No {option_type} found with delta ~{target_delta}")
            return None

        # Return closest match
        candidates.sort(key=lambda x: x["delta_diff"])
        return candidates[0]["option"]

    def find_protective_strike(self, options: List[Dict], short_strike: float,
                              option_type: str, spot_price: float,
                              direction: str = "farther") -> Optional[Dict]:
        """
        Find protective long strike based on wing width

        Args:
            options: List of options
            short_strike: Strike of the short option
            option_type: "call" or "put"
            spot_price: Current spot price
            direction: "farther" from money (for protection)

        Returns:
            Option dict or None
        """
        wing_distance = spot_price * self.wing_width_percent

        if option_type == "put":
            # Long put should be below short put
            target_strike = short_strike - wing_distance
            candidates = [
                opt for opt in options
                if opt.get("option_type") == "put"
                and opt.get("strike", 0) < short_strike
                and opt.get("strike", 0) <= target_strike
            ]
        else:  # call
            # Long call should be above short call
            target_strike = short_strike + wing_distance
            candidates = [
                opt for opt in options
                if opt.get("option_type") == "call"
                and opt.get("strike", 0) > short_strike
                and opt.get("strike", 0) >= target_strike
            ]

        if not candidates:
            logger.warning(f"No protective {option_type} found for strike {short_strike}")
            return None

        # Find closest to target
        candidates.sort(key=lambda x: abs(x["strike"] - target_strike))
        return candidates[0]

    def build_condor(self, currency: str, options_chain: List[Dict],
                    spot_price: float, expiration_date: str,
                    risk_per_condor: float, tp_ratio: float = 0.55,
                    sl_mult: float = 1.2) -> Optional[IronCondor]:
        """
        Build a complete Iron Condor structure

        Args:
            currency: BTC or ETH
            options_chain: List of available options with greeks and prices
            spot_price: Current spot price
            expiration_date: Expiration date string
            risk_per_condor: Maximum risk in USD for this condor
            tp_ratio: Take profit ratio (e.g., 0.55 = 55% of credit)
            sl_mult: Stop loss multiplier (e.g., 1.2 = 1.2x credit)

        Returns:
            IronCondor object or None if cannot build
        """
        try:
            # Find short put (negative delta ~-0.12)
            short_put_opt = self.find_strike_by_delta(
                options_chain, -self.short_delta_target, "put"
            )
            if not short_put_opt:
                logger.error("Could not find short put strike")
                return None

            # Find short call (positive delta ~+0.12)
            short_call_opt = self.find_strike_by_delta(
                options_chain, self.short_delta_target, "call"
            )
            if not short_call_opt:
                logger.error("Could not find short call strike")
                return None

            # Find protective long put
            long_put_opt = self.find_protective_strike(
                options_chain, short_put_opt["strike"], "put", spot_price
            )
            if not long_put_opt:
                logger.error("Could not find long put strike")
                return None

            # Find protective long call
            long_call_opt = self.find_protective_strike(
                options_chain, short_call_opt["strike"], "call", spot_price
            )
            if not long_call_opt:
                logger.error("Could not find long call strike")
                return None

            # Create option legs
            short_put = OptionLeg(
                instrument_name=short_put_opt["instrument_name"],
                strike=short_put_opt["strike"],
                option_type="put",
                direction="sell",
                delta=short_put_opt["greeks"]["delta"],
                mark_price=short_put_opt["mark_price"],
                mark_iv=short_put_opt.get("mark_iv", 0)
            )

            long_put = OptionLeg(
                instrument_name=long_put_opt["instrument_name"],
                strike=long_put_opt["strike"],
                option_type="put",
                direction="buy",
                delta=long_put_opt["greeks"]["delta"],
                mark_price=long_put_opt["mark_price"],
                mark_iv=long_put_opt.get("mark_iv", 0)
            )

            short_call = OptionLeg(
                instrument_name=short_call_opt["instrument_name"],
                strike=short_call_opt["strike"],
                option_type="call",
                direction="sell",
                delta=short_call_opt["greeks"]["delta"],
                mark_price=short_call_opt["mark_price"],
                mark_iv=short_call_opt.get("mark_iv", 0)
            )

            long_call = OptionLeg(
                instrument_name=long_call_opt["instrument_name"],
                strike=long_call_opt["strike"],
                option_type="call",
                direction="buy",
                delta=long_call_opt["greeks"]["delta"],
                mark_price=long_call_opt["mark_price"],
                mark_iv=long_call_opt.get("mark_iv", 0)
            )

            # Calculate credit per unit (1 contract)
            credit_per_unit = (
                short_put.mark_price + short_call.mark_price
                - long_put.mark_price - long_call.mark_price
            ) * spot_price  # Convert to USD

            if credit_per_unit <= 0:
                logger.error(f"Invalid credit: {credit_per_unit}")
                return None

            # Calculate max loss per unit
            put_spread_width = (short_put.strike - long_put.strike)
            call_spread_width = (long_call.strike - short_call.strike)
            max_loss_per_unit = max(put_spread_width, call_spread_width) - credit_per_unit

            if max_loss_per_unit <= 0:
                logger.error(f"Invalid max loss: {max_loss_per_unit}")
                return None

            # Calculate position size
            size = risk_per_condor / max_loss_per_unit

            # Adjust for practical limits (Deribit minimum sizes)
            size = max(0.01, min(size, 10.0))  # Between 0.01 and 10 contracts

            # Create condor
            condor_id = f"{currency}_{expiration_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            condor = IronCondor(
                id=condor_id,
                currency=currency,
                expiration_date=expiration_date,
                spot_price=spot_price,
                entry_time=datetime.now(),
                long_put=long_put,
                short_put=short_put,
                short_call=short_call,
                long_call=long_call,
                credit_received=credit_per_unit * size,
                max_loss=max_loss_per_unit * size,
                max_profit=credit_per_unit * size,
                size=size,
                take_profit_target=credit_per_unit * size * tp_ratio,
                stop_loss_target=-(credit_per_unit * size * sl_mult),
                status="open"
            )

            logger.info(f"Built Iron Condor: {condor_id}")
            logger.info(f"  Spot: ${spot_price:,.0f}")
            logger.info(f"  Put spread: {long_put.strike:.0f}/{short_put.strike:.0f}")
            logger.info(f"  Call spread: {short_call.strike:.0f}/{long_call.strike:.0f}")
            logger.info(f"  Credit: ${condor.credit_received:.2f}, Max loss: ${condor.max_loss:.2f}")
            logger.info(f"  Size: {size:.3f} contracts")

            return condor

        except Exception as e:
            logger.error(f"Error building Iron Condor: {e}")
            return None

    def get_days_to_expiration(self, expiration_date: str) -> int:
        """
        Calculate days to expiration

        Args:
            expiration_date: Date string in format DDMMMYY (e.g., "27DEC24")

        Returns:
            Number of days
        """
        try:
            exp_dt = datetime.strptime(expiration_date, "%d%b%y")
            dte = (exp_dt - datetime.now()).days
            return dte
        except Exception as e:
            logger.error(f"Error parsing expiration date {expiration_date}: {e}")
            return 0
