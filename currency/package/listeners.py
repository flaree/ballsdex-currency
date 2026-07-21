from __future__ import annotations

import asyncio
import logging
import threading

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from bd_models.models import BallInstance

from ..models import CurrencySettings, CurrencyTransaction
from .rewards import catch_earnings_today, compute_catch_reward, credit_player

log = logging.getLogger("currency.listeners")


def _run_async(coro) -> None:
    """Run an already-created coroutine to completion in a background thread post-commit."""

    def _runner():
        try:
            asyncio.run(coro)
        except Exception:
            log.exception("Currency catch-bounty task raised an exception")

    transaction.on_commit(lambda: threading.Thread(target=_runner, daemon=True).start())


async def _reward_catch(instance_pk: int) -> None:
    config = await CurrencySettings.aget_solo()
    if not config.catch_enabled:
        return

    try:
        instance = await BallInstance.objects.select_related("player").aget(pk=instance_pk)
    except BallInstance.DoesNotExist:
        return

    # Only genuine wild catches set ``spawned_time`` (see countryball.catch_ball).
    #   * Drops re-assign an existing instance -> post_save created=False (filtered
    #     out in the signal handler below), so they never reach here.
    #   * Reward grants from other packages (games, topgg, battlepass) and admin
    #     give/spawn create instances without a spawned_time, so they are skipped.
    # This is the single check that keeps catch bounties tied to real catches and
    # immune to the drop-and-recatch farming exploit.
    if instance.spawned_time is None:
        return

    player = instance.player
    reward = compute_catch_reward(config, instance.countryball, instance.special_id is not None)
    if reward <= 0:
        return

    # Enforce the per-player daily cap.
    if config.catch_daily_cap > 0:
        remaining = config.catch_daily_cap - await catch_earnings_today(player)
        if remaining <= 0:
            return
        reward = min(reward, remaining)

    await credit_player(
        player, reward, CurrencyTransaction.SOURCE_CATCH, detail=instance.countryball.country
    )
    log.debug("Granted %s catch bounty to player %s", reward, player.pk)


@receiver(post_save, sender=BallInstance)
def _on_ballinstance_saved(sender, instance: BallInstance, created: bool, **kwargs) -> None:
    # Only newly caught balls; drops (ownership transfer) fire with created=False.
    if not created:
        return
    _run_async(_reward_catch(instance.pk))
