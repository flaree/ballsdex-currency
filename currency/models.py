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
    catch_rarity_scaling = models.BooleanField(default=True, help_text="Scale the reward up for rarer collectibles")
    catch_rarity_exponent = models.FloatField(
        default=1.0, help_text="How aggressively rarity increases the reward. 0 = flat, 1 = linear, >1 = steeper."
    )
    catch_special_multiplier = models.FloatField(
        default=2.0, help_text="Reward multiplier when the caught collectible has a special background"
    )
    catch_min_reward = models.PositiveIntegerField(default=1, help_text="Lower clamp for a single catch reward")
    catch_max_reward = models.PositiveIntegerField(default=500, help_text="Upper clamp for a single catch reward")
    catch_daily_cap = models.PositiveIntegerField(
        default=1000, help_text="Maximum currency a player can earn from catches per day (0 = unlimited)"
    )

    # --- Daily reward ---
    daily_enabled = models.BooleanField(default=True)
    daily_base_reward = models.PositiveIntegerField(default=50, help_text="Base reward for the /currency daily claim")
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
    sell_rarity_scaling = models.BooleanField(default=True, help_text="Scale the value up for rarer collectibles")
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

    # --- Shop ---
    shop_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Register the shop command group with Discord. Leave this off while configuring shop "
            "prices, then enable it and resync the command tree to make it live."
        ),
    )

    # --- Shop: stat reroll ---
    reroll_enabled = models.BooleanField(default=True, help_text="Allow buying stat rerolls from the shop")
    reroll_base_cost = models.PositiveIntegerField(
        default=500, help_text="Cost of the first stat reroll on a collectible"
    )
    reroll_cost_escalation = models.BooleanField(
        default=True, help_text="Multiply the cost by the reroll number (2nd reroll costs 2x base, 3rd costs 3x)"
    )
    reroll_max_per_ball = models.PositiveIntegerField(
        default=3, help_text="Maximum number of stat rerolls a single collectible can receive"
    )

    # --- Shop: custom catch phrase ---
    catch_phrase_enabled = models.BooleanField(
        default=True, help_text="Allow buying a custom catch phrase from the shop"
    )
    catch_phrase_cost = models.PositiveIntegerField(
        default=1000, help_text="Cost of setting (or replacing) a custom catch phrase"
    )
    catch_phrase_max_length = models.PositiveIntegerField(
        default=100, help_text="Maximum length of a custom catch phrase"
    )

    # --- Shop: spawn boost ---
    spawn_boost_enabled = models.BooleanField(
        default=True, help_text="Allow buying a server-wide spawn boost from the shop"
    )
    spawn_boost_cost = models.PositiveIntegerField(default=2000, help_text="Cost of a server-wide spawn boost")
    spawn_boost_multiplier = models.FloatField(
        default=2.0, help_text="Spawn rate multiplier applied by a purchased boost"
    )
    spawn_boost_duration_hours = models.FloatField(
        default=6.0, help_text="Duration in hours of a purchased spawn boost"
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
    player_id: int
    last_claim = models.DateTimeField(null=True, blank=True, help_text="When the player last claimed a daily")
    streak = models.PositiveIntegerField(default=0, help_text="Current consecutive-day streak")
    total_claimed = models.PositiveBigIntegerField(default=0, help_text="Lifetime currency claimed from daily rewards")

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
    SOURCE_REROLL = "reroll"
    SOURCE_CATCH_PHRASE = "catch_phrase"
    SOURCE_SPAWN_BOOST = "spawn_boost"
    SOURCE_CHOICES = [
        (SOURCE_CATCH, "Catch bounty"),
        (SOURCE_DAILY, "Daily reward"),
        (SOURCE_SELL, "Sell"),
        (SOURCE_REROLL, "Shop: stat reroll"),
        (SOURCE_CATCH_PHRASE, "Shop: catch phrase"),
        (SOURCE_SPAWN_BOOST, "Shop: spawn boost"),
    ]
    #: Sources that represent currency leaving the economy rather than entering it.
    #: ``amount`` stays positive; subtract these when computing net flow.
    SPEND_SOURCES = frozenset({SOURCE_REROLL, SOURCE_CATCH_PHRASE, SOURCE_SPAWN_BOOST})

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="currency_transactions")
    player_id: int
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
        indexes = (models.Index(fields=("player", "source", "created_at")), models.Index(fields=("created_at",)))

    def __str__(self) -> str:
        sign = "-" if self.source in self.SPEND_SOURCES else "+"
        label = dict(self.SOURCE_CHOICES).get(self.source, self.source)
        return f"{label} {sign}{self.amount} → player {self.player_id}"


class SpawnBoost(models.Model):
    """
    A time-boxed spawn rate multiplier for a guild.

    Rows may come from a shop purchase or from a staff command. The spawn manager
    (``AntiSpamNew``) looks up the highest active multiplier for the guild through a
    short-lived cache, so expired rows simply stop applying — no cleanup required.
    """

    SOURCE_PURCHASE = "purchase"
    SOURCE_ADMIN = "admin"
    SOURCE_CHOICES = [(SOURCE_PURCHASE, "Shop purchase"), (SOURCE_ADMIN, "Staff command")]

    guild_id = models.PositiveBigIntegerField(help_text="Discord guild this boost applies to")
    multiplier = models.FloatField(help_text="Spawn rate multiplier (2.0 = double spawns)")
    expires_at = models.DateTimeField(help_text="When this boost stops applying")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_PURCHASE)
    purchased_by = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spawn_boosts",
        help_text="Player who bought the boost (empty for staff boosts)",
    )
    purchased_by_id: int | None
    created_at = models.DateTimeField(auto_now_add=True)

    objects: Manager[Self] = Manager()

    class Meta:
        verbose_name = "Spawn Boost"
        verbose_name_plural = "Spawn Boosts"
        indexes = (models.Index(fields=("guild_id", "expires_at")),)

    def __str__(self) -> str:
        return f"x{self.multiplier} in guild {self.guild_id} until {self.expires_at:%Y-%m-%d %H:%M}"


class PlayerCatchPhrase(models.Model):
    """The currently active custom catch phrase of a player, shown under their catch messages."""

    player = models.OneToOneField(Player, on_delete=models.CASCADE, related_name="custom_catch_phrase")
    player_id: int
    phrase = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    objects: Manager[Self] = Manager()

    class Meta:
        verbose_name = "Player Catch Phrase"
        verbose_name_plural = "Player Catch Phrases"

    def __str__(self) -> str:
        return f"Player {self.player_id}: {self.phrase!r}"


class CatchPhraseLog(models.Model):
    """
    Append-only audit trail of every catch phrase purchase.

    Support staff can review the full history in the Django admin; deleting a player's
    active ``PlayerCatchPhrase`` there is the moderation path for abusive phrases.
    """

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="catch_phrase_logs")
    player_id: int
    phrase = models.CharField(max_length=255)
    cost = models.PositiveBigIntegerField(help_text="Currency paid for this phrase")
    created_at = models.DateTimeField(auto_now_add=True)

    objects: Manager[Self] = Manager()

    class Meta:
        verbose_name = "Catch Phrase Log"
        verbose_name_plural = "Catch Phrase Logs"
        indexes = (models.Index(fields=("player", "created_at")),)

    def __str__(self) -> str:
        return f"Player {self.player_id} @ {self.created_at:%Y-%m-%d %H:%M}: {self.phrase!r}"
