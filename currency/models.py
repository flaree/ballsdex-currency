from __future__ import annotations

from typing import Self

from django.db import models
from django.db.models import Manager

from bd_models.models import Player


class CurrencySettings(models.Model):
    """
    Singleton configuration for every currency faucet in this package.

    A single row (pk=1) holds all tunables so server owners can adjust the economy
    live from the Django admin without touching code or restarting the bot.
    """

    # --- Catch bounty (reward for catching a wild collectible) ---
    catch_enabled = models.BooleanField(
        default=True, help_text="Reward currency when a player catches a wild collectible"
    )
    catch_base_reward = models.PositiveIntegerField(
        default=10, help_text="Base reward for catching the most common collectible"
    )
    catch_rarity_scaling = models.BooleanField(
        default=True, help_text="Scale the reward up for rarer collectibles"
    )
    catch_rarity_exponent = models.FloatField(
        default=1.0,
        help_text="How aggressively rarity increases the reward. 0 = flat, 1 = linear, >1 = steeper.",
    )
    catch_special_multiplier = models.FloatField(
        default=2.0, help_text="Reward multiplier when the caught collectible has a special background"
    )
    catch_min_reward = models.PositiveIntegerField(
        default=1, help_text="Lower clamp for a single catch reward"
    )
    catch_max_reward = models.PositiveIntegerField(
        default=500, help_text="Upper clamp for a single catch reward"
    )
    catch_daily_cap = models.PositiveIntegerField(
        default=1000,
        help_text="Maximum currency a player can earn from catches per day (0 = unlimited)",
    )

    # --- Daily reward ---
    daily_enabled = models.BooleanField(default=True)
    daily_base_reward = models.PositiveIntegerField(
        default=50, help_text="Base reward for the /currency daily claim"
    )
    daily_streak_bonus = models.PositiveIntegerField(
        default=10, help_text="Extra currency added per consecutive day of the streak"
    )
    daily_max_streak = models.PositiveIntegerField(
        default=7, help_text="Streak length at which the streak bonus stops growing"
    )

    # --- Sell ---
    sell_enabled = models.BooleanField(default=True)
    sell_base_value = models.PositiveIntegerField(
        default=5, help_text="Base value for selling the most common collectible"
    )
    sell_rarity_scaling = models.BooleanField(
        default=True, help_text="Scale the value up for rarer collectibles"
    )
    sell_rarity_exponent = models.FloatField(
        default=1.0, help_text="How aggressively rarity increases the value. 0 = flat, 1 = linear, >1 = steeper."
    )
    sell_special_multiplier = models.FloatField(
        default=3.0, help_text="Value multiplier when the sold collectible has a special background"
    )
    sell_min_value = models.PositiveIntegerField(default=1, help_text="Lower clamp for a single sale")
    sell_max_value = models.PositiveIntegerField(default=1000, help_text="Upper clamp for a single sale")
    sell_allow_favorite = models.BooleanField(
        default=False, help_text="Allow players to sell collectibles they have favorited"
    )

    objects: Manager[Self] = Manager()

    class Meta:
        verbose_name = "Currency Settings"
        verbose_name_plural = "Currency Settings"

    def __str__(self) -> str:
        return "Currency Settings"

    def save(self, *args, **kwargs):
        # Enforce a single configuration row.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    async def aget_solo(cls) -> "CurrencySettings":
        obj, _ = await cls.objects.aget_or_create(pk=1)
        return obj


class DailyClaim(models.Model):
    """Tracks a player's daily-reward streak and claim time."""

    player = models.OneToOneField(Player, on_delete=models.CASCADE, related_name="currency_daily")
    last_claim = models.DateTimeField(null=True, blank=True, help_text="When the player last claimed a daily")
    streak = models.PositiveIntegerField(default=0, help_text="Current consecutive-day streak")
    total_claimed = models.PositiveBigIntegerField(
        default=0, help_text="Lifetime currency claimed from daily rewards"
    )

    objects: Manager[Self] = Manager()

    class Meta:
        verbose_name = "Daily Claim"
        verbose_name_plural = "Daily Claims"

    def __str__(self) -> str:
        return f"Player {self.player_id} (streak {self.streak})"


class CurrencyTransaction(models.Model):
    """
    Audit ledger of every currency grant made by this package.

    Besides transparency, catch-source rows are summed to enforce the per-player
    daily catch cap defined on `CurrencySettings`.
    """

    SOURCE_CATCH = "catch"
    SOURCE_DAILY = "daily"
    SOURCE_SELL = "sell"
    SOURCE_CHOICES = [
        (SOURCE_CATCH, "Catch bounty"),
        (SOURCE_DAILY, "Daily reward"),
        (SOURCE_SELL, "Sell"),
    ]

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="currency_transactions")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    amount = models.PositiveBigIntegerField()
    detail = models.CharField(
        max_length=255, blank=True, help_text="Human-readable context (collectible name, streak, …)"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects: Manager[Self] = Manager()

    class Meta:
        verbose_name = "Currency Transaction"
        verbose_name_plural = "Currency Transactions"
        indexes = (
            models.Index(fields=("player", "source", "created_at")),
            models.Index(fields=("created_at",)),
        )

    def __str__(self) -> str:
        return f"{self.get_source_display()} +{self.amount} → player {self.player_id}"
