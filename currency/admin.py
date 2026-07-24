from django.contrib import admin

from .models import CatchPhraseLog, CurrencySettings, CurrencyTransaction, DailyClaim, PlayerCatchPhrase, SpawnBoost


@admin.register(CurrencySettings)
class CurrencySettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Catch bounty",
            {
                "fields": (
                    "catch_enabled",
                    "catch_base_reward",
                    "catch_rarity_scaling",
                    "catch_rarity_exponent",
                    "catch_special_multiplier",
                    "catch_min_reward",
                    "catch_max_reward",
                    "catch_daily_cap",
                )
            },
        ),
        ("Daily reward", {"fields": ("daily_enabled", "daily_base_reward", "daily_streak_bonus", "daily_max_streak")}),
        (
            "Sell",
            {
                "fields": (
                    "sell_enabled",
                    "sell_base_value",
                    "sell_rarity_scaling",
                    "sell_rarity_exponent",
                    "sell_special_multiplier",
                    "sell_min_value",
                    "sell_max_value",
                    "sell_allow_favorite",
                )
            },
        ),
        (
            "Shop",
            {
                "fields": ("shop_enabled",),
                "description": (
                    "The shop command group is only registered with Discord while this is on. "
                    "After changing it, reload the currency package and resync the command tree."
                ),
            },
        ),
        (
            "Shop: stat reroll",
            {"fields": ("reroll_enabled", "reroll_base_cost", "reroll_cost_escalation", "reroll_max_per_ball")},
        ),
        ("Shop: catch phrase", {"fields": ("catch_phrase_enabled", "catch_phrase_cost", "catch_phrase_max_length")}),
        (
            "Shop: spawn boost",
            {
                "fields": (
                    "spawn_boost_enabled",
                    "spawn_boost_cost",
                    "spawn_boost_multiplier",
                    "spawn_boost_duration_hours",
                )
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        # Singleton: only allow creating the first (and only) row.
        return not CurrencySettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(DailyClaim)
class DailyClaimAdmin(admin.ModelAdmin):
    list_display = ("player", "streak", "last_claim", "total_claimed")
    search_fields = ("player__discord_id",)
    autocomplete_fields = ("player",)
    readonly_fields = ("last_claim",)


@admin.register(CurrencyTransaction)
class CurrencyTransactionAdmin(admin.ModelAdmin):
    list_display = ("player", "source", "amount", "detail", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("player__discord_id", "detail")
    autocomplete_fields = ("player",)
    readonly_fields = ("player", "source", "amount", "detail", "created_at")

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(SpawnBoost)
class SpawnBoostAdmin(admin.ModelAdmin):
    list_display = ("guild_id", "multiplier", "source", "purchased_by", "expires_at", "created_at")
    list_filter = ("source", "expires_at")
    search_fields = ("guild_id", "purchased_by__discord_id")
    autocomplete_fields = ("purchased_by",)


@admin.register(PlayerCatchPhrase)
class PlayerCatchPhraseAdmin(admin.ModelAdmin):
    """Deleting a row here is the moderation path for abusive phrases."""

    list_display = ("player", "phrase", "updated_at")
    search_fields = ("player__discord_id", "phrase")
    autocomplete_fields = ("player",)


@admin.register(CatchPhraseLog)
class CatchPhraseLogAdmin(admin.ModelAdmin):
    """Append-only purchase history so support can audit every phrase ever set."""

    list_display = ("player", "phrase", "cost", "created_at")
    list_filter = ("created_at",)
    search_fields = ("player__discord_id", "phrase")
    autocomplete_fields = ("player",)
    readonly_fields = ("player", "phrase", "cost", "created_at")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
