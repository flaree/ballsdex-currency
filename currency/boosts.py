from __future__ import annotations

import logging

from cachetools import TTLCache
from django.db.models import Max
from django.utils import timezone

from .models import SpawnBoost

log = logging.getLogger("currency.boosts")

# guild_id -> highest active multiplier. The short TTL bounds both the DB load (at most one
# query per guild per minute, spawn managers call this on every message) and the propagation
# delay of new or expired boosts across clusters (at most one minute).
_cache: TTLCache[int, float] = TTLCache(maxsize=50_000, ttl=60)


async def get_spawn_multiplier(guild_id: int) -> float:
    """Return the highest active spawn boost multiplier for this guild, ``1.0`` if none."""
    cached = _cache.get(guild_id)
    if cached is not None:
        return cached
    try:
        result = await SpawnBoost.objects.filter(guild_id=guild_id, expires_at__gt=timezone.now()).aaggregate(
            multiplier=Max("multiplier")
        )
        multiplier = max(result["multiplier"] or 1.0, 1.0)
    except Exception:
        # Never let a DB hiccup break spawning; fall back to no boost and retry next miss.
        log.exception("Failed to fetch spawn boost for guild %s", guild_id)
        return 1.0
    _cache[guild_id] = multiplier
    return multiplier


def invalidate(guild_id: int) -> None:
    """Drop the cached multiplier so a boost change applies immediately on this cluster."""
    _cache.pop(guild_id, None)
