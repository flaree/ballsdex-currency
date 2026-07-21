from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import F, Sum
from django.utils import timezone

from bd_models.models import Player, balls

from ..models import CurrencySettings, CurrencyTransaction

if TYPE_CHECKING:
    from bd_models.models import Ball


def _clamp(value: int, low: int, high: int) -> int:
    if high < low:
        high = low
    return max(low, min(value, high))


def rarity_multiplier(rarity: float, exponent: float) -> float:
    """
    Return a multiplier >= 1 that grows for rarer collectibles.

    A ball's ``rarity`` is a spawn weight: higher values spawn more often (common),
    lower values are rarer. The multiplier is normalised against the most common
    enabled ball so it automatically adapts to whatever rarity scale the server uses
    (some servers use 1–100, others 0–1).
    """
    if rarity <= 0:
        return 1.0
    enabled = [b.rarity for b in balls.values() if b.enabled and b.rarity > 0]
    if not enabled:
        return 1.0
    max_rarity = max(enabled)
    ratio = max_rarity / rarity  # 1.0 for the most common ball, larger for rarer ones
    return max(1.0, ratio) ** max(0.0, exponent)


def compute_catch_reward(config: CurrencySettings, ball: "Ball", has_special: bool) -> int:
    """Currency awarded for catching a wild ``ball`` (optionally with a special)."""
    reward = float(config.catch_base_reward)
    if config.catch_rarity_scaling:
        reward *= rarity_multiplier(ball.rarity, config.catch_rarity_exponent)
    if has_special:
        reward *= config.catch_special_multiplier
    return _clamp(round(reward), config.catch_min_reward, config.catch_max_reward)


def compute_sell_value(config: CurrencySettings, ball: "Ball", has_special: bool) -> int:
    """Currency paid out when selling a ``ball`` instance (optionally with a special)."""
    value = float(config.sell_base_value)
    if config.sell_rarity_scaling:
        value *= rarity_multiplier(ball.rarity, config.sell_rarity_exponent)
    if has_special:
        value *= config.sell_special_multiplier
    return _clamp(round(value), config.sell_min_value, config.sell_max_value)


async def catch_earnings_today(player: Player) -> int:
    """Total currency this player has earned from catches during the current local day."""
    today = timezone.localdate()
    agg = await CurrencyTransaction.objects.filter(
        player=player, source=CurrencyTransaction.SOURCE_CATCH, created_at__date=today
    ).aaggregate(total=Sum("amount"))
    return agg["total"] or 0


async def credit_player(player: Player, amount: int, source: str, detail: str = "") -> None:
    """
    Atomically add ``amount`` to the player's balance and record a ledger entry.

    Uses an ``F`` expression so concurrent grants (e.g. rapid catches) can never
    clobber each other's balance updates.
    """
    if amount <= 0:
        return
    await Player.objects.filter(pk=player.pk).aupdate(money=F("money") + amount)
    await CurrencyTransaction.objects.acreate(
        player=player, source=source, amount=amount, detail=detail[:255]
    )
