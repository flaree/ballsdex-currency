from django.contrib import admin

from .models import CurrencySettings, CurrencyTransaction, DailyClaim


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
        (
            "Daily reward",
            {"fields": ("daily_enabled", "daily_base_reward", "daily_streak_bonus", "daily_max_streak")},
        ),
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
