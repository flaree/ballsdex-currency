import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("bd_models", "0018_guildconfig_manual_drop_enabled"),
    ]

    operations = [
        migrations.CreateModel(
            name="CurrencySettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("catch_enabled", models.BooleanField(default=True, help_text="Reward currency when a player catches a wild collectible")),
                ("catch_base_reward", models.PositiveIntegerField(default=10, help_text="Base reward for catching the most common collectible")),
                ("catch_rarity_scaling", models.BooleanField(default=True, help_text="Scale the reward up for rarer collectibles")),
                ("catch_rarity_exponent", models.FloatField(default=1.0, help_text="How aggressively rarity increases the reward. 0 = flat, 1 = linear, >1 = steeper.")),
                ("catch_special_multiplier", models.FloatField(default=2.0, help_text="Reward multiplier when the caught collectible has a special background")),
                ("catch_min_reward", models.PositiveIntegerField(default=1, help_text="Lower clamp for a single catch reward")),
                ("catch_max_reward", models.PositiveIntegerField(default=500, help_text="Upper clamp for a single catch reward")),
                ("catch_daily_cap", models.PositiveIntegerField(default=1000, help_text="Maximum currency a player can earn from catches per day (0 = unlimited)")),
                ("daily_enabled", models.BooleanField(default=True)),
                ("daily_base_reward", models.PositiveIntegerField(default=50, help_text="Base reward for the /currency daily claim")),
                ("daily_streak_bonus", models.PositiveIntegerField(default=10, help_text="Extra currency added per consecutive day of the streak")),
                ("daily_max_streak", models.PositiveIntegerField(default=7, help_text="Streak length at which the streak bonus stops growing")),
                ("sell_enabled", models.BooleanField(default=True)),
                ("sell_base_value", models.PositiveIntegerField(default=5, help_text="Base value for selling the most common collectible")),
                ("sell_rarity_scaling", models.BooleanField(default=True, help_text="Scale the value up for rarer collectibles")),
                ("sell_rarity_exponent", models.FloatField(default=1.0, help_text="How aggressively rarity increases the value. 0 = flat, 1 = linear, >1 = steeper.")),
                ("sell_special_multiplier", models.FloatField(default=3.0, help_text="Value multiplier when the sold collectible has a special background")),
                ("sell_min_value", models.PositiveIntegerField(default=1, help_text="Lower clamp for a single sale")),
                ("sell_max_value", models.PositiveIntegerField(default=1000, help_text="Upper clamp for a single sale")),
                ("sell_allow_favorite", models.BooleanField(default=False, help_text="Allow players to sell collectibles they have favorited")),
            ],
            options={
                "verbose_name": "Currency Settings",
                "verbose_name_plural": "Currency Settings",
            },
        ),
        migrations.CreateModel(
            name="DailyClaim",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_claim", models.DateTimeField(blank=True, null=True, help_text="When the player last claimed a daily")),
                ("streak", models.PositiveIntegerField(default=0, help_text="Current consecutive-day streak")),
                ("total_claimed", models.PositiveBigIntegerField(default=0, help_text="Lifetime currency claimed from daily rewards")),
                (
                    "player",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="currency_daily",
                        to="bd_models.player",
                    ),
                ),
            ],
            options={
                "verbose_name": "Daily Claim",
                "verbose_name_plural": "Daily Claims",
            },
        ),
        migrations.CreateModel(
            name="CurrencyTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(choices=[("catch", "Catch bounty"), ("daily", "Daily reward"), ("sell", "Sell")], max_length=16)),
                ("amount", models.PositiveBigIntegerField()),
                ("detail", models.CharField(blank=True, help_text="Human-readable context (collectible name, streak, …)", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="currency_transactions",
                        to="bd_models.player",
                    ),
                ),
            ],
            options={
                "verbose_name": "Currency Transaction",
                "verbose_name_plural": "Currency Transactions",
            },
        ),
        migrations.AddIndex(
            model_name="currencytransaction",
            index=models.Index(fields=["player", "source", "created_at"], name="currency_cu_player__b3f3d1_idx"),
        ),
        migrations.AddIndex(
            model_name="currencytransaction",
            index=models.Index(fields=["created_at"], name="currency_cu_created_9c1a7e_idx"),
        ),
    ]
