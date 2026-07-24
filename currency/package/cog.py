from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from discord.utils import format_dt
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ballsdex.core.utils import checks
from ballsdex.core.utils.transformers import BallInstanceTransform, SpecialEnabledTransform
from bd_models.models import BallInstance, Player
from settings.models import settings
from settings.utils import format_currency

from .. import boosts
from ..models import CurrencySettings, CurrencyTransaction, DailyClaim, SpawnBoost
from . import listeners  # noqa: F401 — imported to register Django signals
from .rewards import compute_sell_value, credit_player
from .shop import build_shop_group
from .views import ConfirmView

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("currency.cog")


@transaction.atomic
def _perform_sell(instance_pk: int, player_pk: int, amount: int, detail: str) -> None:
    """
    Re-validate and finalise a sale under a row lock.

    Guards against double-selling and selling a ball that got locked/traded between
    the confirmation prompt and the click. Soft-deletes the instance (``deleted=True``)
    rather than hard-deleting, so history and anti-cheat records are preserved.
    """
    instance = BallInstance.objects.select_for_update().get(pk=instance_pk)
    if instance.deleted:
        raise RuntimeError("already-sold")
    if instance.player_id != player_pk:
        raise RuntimeError("not-owner")
    if instance.locked and instance.locked > timezone.now() - timedelta(minutes=30):
        raise RuntimeError("locked")

    instance.deleted = True
    instance.save(update_fields=("deleted",))
    Player.objects.filter(pk=player_pk).update(money=F("money") + amount)
    CurrencyTransaction.objects.create(
        player_id=player_pk, source=CurrencyTransaction.SOURCE_SELL, amount=amount, detail=detail[:255]
    )


class Currency(commands.Cog):
    """
    Ways to earn currency: a daily streak reward and selling collectibles.

    Rather than creating its own command group, this cog attaches ``daily`` and
    ``sell`` subcommands to the core currency command group (named after
    ``settings.currency_name``, the same group that provides ``balance`` and
    ``give``). Catch bounties are granted automatically on wild catches and need
    no command.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self._group: app_commands.Group | None = None
        self._registered: list[str] = []

    async def cog_load(self) -> None:
        if not settings.currency_enabled:
            log.warning("Currency is not configured in settings; daily/sell commands are disabled.")
            return
        group = self.bot.tree.get_command(settings.currency_name or "")
        if not isinstance(group, app_commands.Group):
            log.warning(
                "Could not find the '%s' currency command group; is the money package loaded? "
                "The daily and sell commands will not be available.",
                settings.currency_name,
            )
            return
        self._group = group
        for command in self._build_commands():
            group.add_command(command)
            self._registered.append(command.name)

        config = await CurrencySettings.aget_solo()
        if config.shop_enabled:
            shop_group = build_shop_group(self.bot)
            group.add_command(shop_group)
            self._registered.append(shop_group.name)
        else:
            log.info(
                "Shop is disabled in currency settings; the shop command group will not be registered. "
                "Enable CurrencySettings.shop_enabled and reload+resync to make it live."
            )

    def cog_unload(self) -> None:
        if self._group is not None:
            for name in self._registered:
                self._group.remove_command(name)
        self._registered = []
        self._group = None

    def _build_commands(self) -> list[app_commands.Command]:
        """Build the standalone ``daily`` and ``sell`` commands bound to this cog."""
        cog = self

        async def daily(interaction: discord.Interaction["BallsDexBot"]) -> None:
            await cog._daily(interaction)

        async def sell(
            interaction: discord.Interaction["BallsDexBot"],
            countryball: BallInstanceTransform,
            special: SpecialEnabledTransform | None = None,
        ) -> None:
            await cog._sell(interaction, countryball, special)

        daily_cmd = app_commands.Command(
            name="daily",
            description="Claim your daily reward. Claim on consecutive days to grow your streak.",
            callback=daily,
        )
        sell_cmd = app_commands.Command(
            name="sell", description=f"Sell one of your {settings.plural_collectible_name} for currency.", callback=sell
        )
        app_commands.describe(
            countryball=f"The {settings.collectible_name} you want to sell.",
            special="Filter the autocomplete to a special event. Ignored afterwards.",
        )(sell_cmd)
        return [daily_cmd, sell_cmd]

    async def _daily(self, interaction: discord.Interaction["BallsDexBot"]):
        config = await CurrencySettings.aget_solo()
        if not config.daily_enabled:
            await interaction.response.send_message("Daily rewards are currently disabled.", ephemeral=True)
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        claim, _ = await DailyClaim.objects.aget_or_create(player=player)

        today = timezone.localdate()
        last = timezone.localtime(claim.last_claim).date() if claim.last_claim else None
        if last == today:
            await interaction.response.send_message(
                "You have already claimed your daily reward today. Come back tomorrow!", ephemeral=True
            )
            return

        # Consecutive day continues the streak; any gap resets it.
        if last == today - timedelta(days=1):
            claim.streak += 1
        else:
            claim.streak = 1

        # Streak bonus grows until it plateaus at daily_max_streak.
        streak_steps = min(claim.streak, config.daily_max_streak) - 1
        reward = config.daily_base_reward + streak_steps * config.daily_streak_bonus

        claim.last_claim = timezone.now()
        claim.total_claimed += reward
        await claim.asave(update_fields=("last_claim", "streak", "total_claimed"))
        await credit_player(player, reward, CurrencyTransaction.SOURCE_DAILY, detail=f"streak {claim.streak}")

        new_balance = (await Player.objects.aget(pk=player.pk)).money
        embed = discord.Embed(
            title="Daily reward claimed!",
            description=(
                f"You received **{format_currency(reward, shortened=False, bot=self.bot)}**.\n"
                f"🔥 Streak: **{claim.streak}** "
                f"{'day' if claim.streak == 1 else 'days'}\n"
                f"New balance: **{format_currency(new_balance, shortened=False, bot=self.bot)}**"
            ),
            colour=discord.Colour.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _sell(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        config = await CurrencySettings.aget_solo()
        if not config.sell_enabled:
            await interaction.response.send_message("Selling is currently disabled.", ephemeral=True)
            return
        if not countryball:
            return

        if not countryball.is_tradeable:
            await interaction.response.send_message("You can't sell untradeable balls.", ephemeral=True)
            return

        # Ownership is already enforced by the transformer.
        if countryball.favorite and not config.sell_allow_favorite:
            await interaction.response.send_message(
                f"That {settings.collectible_name} is favorited. Unfavorite it first if you really want to sell it.",
                ephemeral=True,
            )
            return
        if await countryball.is_locked():
            await interaction.response.send_message(
                f"This {settings.collectible_name} is currently locked for a trade. Please try again later.",
                ephemeral=True,
            )
            return

        value = compute_sell_value(config, countryball.countryball, countryball.special_id is not None)
        description = countryball.description(short=True)

        view = ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"Are you sure you want to sell **{description}** for "
            f"**{format_currency(value, shortened=False, bot=self.bot)}**?\nThis cannot be undone.",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.value:
            await interaction.edit_original_response(
                content="Sale cancelled." if view.value is False else "Sale timed out.", view=None
            )
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        try:
            await sync_to_async(_perform_sell)(countryball.pk, player.pk, value, countryball.countryball.country)
        except RuntimeError as exc:
            reason = {
                "already-sold": f"That {settings.collectible_name} has already been sold.",
                "not-owner": f"That {settings.collectible_name} no longer belongs to you.",
                "locked": f"That {settings.collectible_name} is now locked for a trade.",
            }.get(str(exc), "The sale could not be completed. Please try again later.")
            await interaction.edit_original_response(content=reason, view=None)
            return

        log.info(
            "%s (%s) sold %s (%s) for %s",
            interaction.user,
            interaction.user.id,
            description,
            f"{countryball.pk:0X}",
            value,
        )
        new_balance = (await Player.objects.aget(pk=player.pk)).money
        await interaction.edit_original_response(
            content=(
                f"You sold **{description}** for "
                f"**{format_currency(value, shortened=False, bot=self.bot)}**.\n"
                f"New balance: **{format_currency(new_balance, shortened=False, bot=self.bot)}**"
            ),
            view=None,
        )

    @commands.group(invoke_without_command=True)
    @checks.is_staff()
    async def spawnboost(self, ctx: commands.Context["BallsDexBot"]):
        """
        Staff tools to manually boost spawn rates.
        """
        await ctx.send_help(ctx.command)

    @spawnboost.command(name="set")
    @checks.is_staff()
    async def spawnboost_set(
        self, ctx: commands.Context["BallsDexBot"], multiplier: float, hours: float = 6.0, guild_id: int | None = None
    ):
        """
        Boost spawn rates in a server by an arbitrary multiplier.

        Parameters
        ----------
        multiplier: float
            The spawn rate multiplier (2 = double spawns). Must be above 1.
        hours: float
            How long the boost lasts, defaults to 6 hours.
        guild_id: int | None
            The server to boost. Defaults to the current server.
        """
        if multiplier <= 1 or multiplier > 20:
            await ctx.send("Multiplier must be above 1 and at most 20.")
            return
        if hours <= 0 or hours > 24 * 7:
            await ctx.send("Duration must be positive and at most a week.")
            return
        target = guild_id or (ctx.guild.id if ctx.guild else None)
        if target is None:
            await ctx.send("Specify a guild id when using this command in DMs.")
            return

        boost = await SpawnBoost.objects.acreate(
            guild_id=target,
            multiplier=multiplier,
            expires_at=timezone.now() + timedelta(hours=hours),
            source=SpawnBoost.SOURCE_ADMIN,
        )
        boosts.invalidate(target)
        log.info(
            "%s (%s) set a x%g spawn boost for guild %s (%gh)", ctx.author, ctx.author.id, multiplier, target, hours
        )
        await ctx.send(
            f"✅ Spawn boost of **x{multiplier:g}** set for guild `{target}` until "
            f"{format_dt(boost.expires_at)}. Note: only the highest active boost applies, and it "
            "can take up to a minute to propagate to all clusters."
        )

    @spawnboost.command(name="clear")
    @checks.is_staff()
    async def spawnboost_clear(self, ctx: commands.Context["BallsDexBot"], guild_id: int | None = None):
        """
        Expire all active spawn boosts in a server (including purchased ones).

        Parameters
        ----------
        guild_id: int | None
            The server to clear. Defaults to the current server.
        """
        target = guild_id or (ctx.guild.id if ctx.guild else None)
        if target is None:
            await ctx.send("Specify a guild id when using this command in DMs.")
            return
        count = await SpawnBoost.objects.filter(guild_id=target, expires_at__gt=timezone.now()).aupdate(
            expires_at=timezone.now()
        )
        boosts.invalidate(target)
        log.info("%s (%s) cleared %d spawn boost(s) for guild %s", ctx.author, ctx.author.id, count, target)
        await ctx.send(
            f"✅ Expired {count} active spawn boost(s) for guild `{target}`. "
            "It can take up to a minute to propagate to all clusters."
        )

    @spawnboost.command(name="list")
    @checks.is_staff()
    async def spawnboost_list(self, ctx: commands.Context["BallsDexBot"]):
        """
        List every currently active spawn boost.
        """
        source_labels = dict(SpawnBoost.SOURCE_CHOICES)
        entries = []
        async for boost in SpawnBoost.objects.filter(expires_at__gt=timezone.now()).order_by("expires_at")[:30]:
            buyer = f" by player {boost.purchased_by_id}" if boost.purchased_by_id else ""
            entries.append(
                f"- guild `{boost.guild_id}`: **x{boost.multiplier:g}** "
                f"({source_labels.get(boost.source, boost.source)}{buyer}) "
                f"until {format_dt(boost.expires_at)}"
            )
        if not entries:
            await ctx.send("No active spawn boosts.")
            return
        await ctx.send("\n".join(entries))


async def setup(bot: "BallsDexBot") -> None:
    await bot.add_cog(Currency(bot))
