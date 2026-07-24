from __future__ import annotations

import logging
import random
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.utils import format_dt
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ballsdex.core.utils.transformers import BallInstanceTransform
from bd_models.models import BallInstance, Player
from settings.models import settings
from settings.utils import format_currency

from .. import boosts
from ..models import CatchPhraseLog, CurrencySettings, CurrencyTransaction, PlayerCatchPhrase, SpawnBoost
from .views import ConfirmView

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("currency.shop")


class InsufficientFunds(Exception):
    def __init__(self, cost: int):
        self.cost = cost


def _charge(player_pk: int, cost: int, source: str, detail: str) -> None:
    """
    Debit ``cost`` from the player and record the spend in the ledger.

    The conditional ``UPDATE ... WHERE money >= cost`` makes check-and-deduct a single
    atomic statement, so concurrent purchases can never drive a balance negative.
    Must be called inside a transaction alongside the goods being granted.
    """
    updated = Player.objects.filter(pk=player_pk, money__gte=cost).update(money=F("money") - cost)
    if not updated:
        raise InsufficientFunds(cost)
    CurrencyTransaction.objects.create(player_id=player_pk, source=source, amount=cost, detail=detail[:255])


def compute_reroll_cost(config: CurrencySettings, rerolls_used: int) -> int:
    if config.reroll_cost_escalation:
        return config.reroll_base_cost * (rerolls_used + 1)
    return config.reroll_base_cost


def reroll_count(instance: BallInstance) -> int:
    return int((instance.extra_data or {}).get("stat_rerolls", 0))


@transaction.atomic
def _perform_reroll(
    instance_pk: int, player_pk: int, config: CurrencySettings
) -> tuple[int, tuple[int, int], tuple[int, int], int]:
    """
    Re-validate under a row lock, charge the player and reroll both stat bonuses.

    Returns (cost, old bonuses, new bonuses, rerolls used after this one).
    """
    instance = BallInstance.objects.select_for_update().get(pk=instance_pk)
    if instance.deleted:
        raise RuntimeError("gone")
    if instance.player_id != player_pk:
        raise RuntimeError("not-owner")
    if instance.locked and instance.locked > timezone.now() - timedelta(minutes=30):
        raise RuntimeError("locked")
    used = reroll_count(instance)
    if used >= config.reroll_max_per_ball:
        raise RuntimeError("max-rerolls")

    cost = compute_reroll_cost(config, used)
    _charge(player_pk, cost, CurrencyTransaction.SOURCE_REROLL, f"#{instance_pk:0X} reroll {used + 1}")

    old = (instance.attack_bonus, instance.health_bonus)
    # same distribution as a wild catch (see BallSpawnView.catch_ball)
    instance.attack_bonus = random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
    instance.health_bonus = random.randint(-settings.max_health_bonus, settings.max_health_bonus)
    extra = dict(instance.extra_data or {})
    extra["stat_rerolls"] = used + 1
    instance.extra_data = extra
    instance.save(update_fields=("attack_bonus", "health_bonus", "extra_data"))
    return cost, old, (instance.attack_bonus, instance.health_bonus), used + 1


@transaction.atomic
def _perform_catch_phrase(player_pk: int, phrase: str, cost: int) -> None:
    """Charge the player, set their active phrase and append to the audit log."""
    _charge(player_pk, cost, CurrencyTransaction.SOURCE_CATCH_PHRASE, phrase)
    PlayerCatchPhrase.objects.update_or_create(player_id=player_pk, defaults={"phrase": phrase})
    CatchPhraseLog.objects.create(player_id=player_pk, phrase=phrase, cost=cost)


@transaction.atomic
def _perform_spawn_boost(player_pk: int, guild_id: int, config: CurrencySettings) -> SpawnBoost:
    """Charge the player and create the guild boost, refusing to stack on an active one."""
    if SpawnBoost.objects.filter(guild_id=guild_id, expires_at__gt=timezone.now()).exists():
        raise RuntimeError("active-boost")
    _charge(
        player_pk,
        config.spawn_boost_cost,
        CurrencyTransaction.SOURCE_SPAWN_BOOST,
        f"guild {guild_id} x{config.spawn_boost_multiplier:g}",
    )
    return SpawnBoost.objects.create(
        guild_id=guild_id,
        multiplier=config.spawn_boost_multiplier,
        expires_at=timezone.now() + timedelta(hours=config.spawn_boost_duration_hours),
        source=SpawnBoost.SOURCE_PURCHASE,
        purchased_by_id=player_pk,
    )


_URL_RE = re.compile(r"(https?://|discord\.gg/|discord\.com/invite)", re.IGNORECASE)


def validate_phrase(phrase: str, max_length: int) -> str | None:
    """Return a user-facing error for an invalid phrase, or None if acceptable."""
    if not phrase:
        return "Your catch phrase cannot be empty."
    if len(phrase) > max_length:
        return f"Your catch phrase cannot be longer than {max_length} characters."
    if any(ch in phrase for ch in "\n\r\t") or any(ord(ch) < 32 for ch in phrase):
        return "Your catch phrase must be a single line without control characters."
    if "@everyone" in phrase or "@here" in phrase or "<@" in phrase or "<#" in phrase:
        return "Your catch phrase cannot contain mentions."
    if _URL_RE.search(phrase):
        return "Your catch phrase cannot contain links or invites."
    return None


async def get_catch_phrase(player_pk: int) -> str | None:
    """The player's active custom catch phrase, if they bought one. Used by the catch flow."""
    entry = await PlayerCatchPhrase.objects.filter(player_id=player_pk).values_list("phrase", flat=True).afirst()
    return entry or None


def build_shop_group(bot: "BallsDexBot") -> app_commands.Group:
    """
    Build the ``shop`` subcommand group, attached to the core currency command group
    the same way the ``daily`` and ``sell`` commands are.
    """
    shop = app_commands.Group(
        name="shop", description=f"Spend your {settings.currency_display_plural(bot)} on upgrades"
    )

    @shop.command(name="view", description="See what the shop sells and your balance.")
    async def view(interaction: discord.Interaction["BallsDexBot"]):
        config = await CurrencySettings.aget_solo()
        player = await Player.objects.filter(discord_id=interaction.user.id).afirst()
        balance = player.money if player else 0

        def price(amount: int) -> str:
            return format_currency(amount, shortened=False, bot=bot)

        lines: list[str] = []
        if config.reroll_enabled:
            cost = price(config.reroll_base_cost)
            if config.reroll_cost_escalation:
                cost += " (doubles then triples per reroll)"
            lines.append(
                f"**Stat reroll** — {cost}\nReroll the ATK/HP bonuses of a {settings.collectible_name}, "
                f"up to {config.reroll_max_per_ball} times each."
            )
        if config.catch_phrase_enabled:
            lines.append(
                f"**Custom catch phrase** — {price(config.catch_phrase_cost)}\n"
                "Shown under your catch messages. Buying again replaces it."
            )
        if config.spawn_boost_enabled:
            lines.append(
                f"**Spawn boost** — {price(config.spawn_boost_cost)}\n"
                f"x{config.spawn_boost_multiplier:g} spawns in this server for "
                f"{config.spawn_boost_duration_hours:g} hours."
            )
        if not lines:
            lines.append("The shop is currently empty.")

        embed = discord.Embed(title="Shop", description="\n\n".join(lines), colour=discord.Colour.gold())
        embed.set_footer(text=f"Balance: {format_currency(balance, shortened=False, bot=bot)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @shop.command(
        name="reroll", description=f"Reroll the stat bonuses of one of your {settings.plural_collectible_name}."
    )
    @app_commands.describe(countryball=f"The {settings.collectible_name} whose ATK/HP bonuses you want to reroll.")
    async def reroll(interaction: discord.Interaction["BallsDexBot"], countryball: BallInstanceTransform):
        config = await CurrencySettings.aget_solo()
        if not config.reroll_enabled:
            await interaction.response.send_message("Stat rerolls are currently disabled.", ephemeral=True)
            return
        if not countryball:
            return

        used = reroll_count(countryball)
        if used >= config.reroll_max_per_ball:
            await interaction.response.send_message(
                f"This {settings.collectible_name} has already been rerolled "
                f"{config.reroll_max_per_ball} times and cannot be rerolled again.",
                ephemeral=True,
            )
            return
        if await countryball.is_locked():
            await interaction.response.send_message(
                f"This {settings.collectible_name} is currently locked for a trade. Please try again later.",
                ephemeral=True,
            )
            return

        cost = compute_reroll_cost(config, used)
        confirm = ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"Reroll **{countryball.description(short=True)}** "
            f"(`{countryball.attack_bonus:+d}%/{countryball.health_bonus:+d}%`) for "
            f"**{format_currency(cost, shortened=False, bot=bot)}**?\n"
            f"Both bonuses are rerolled together and the new roll always applies, even if worse. "
            f"Reroll {used + 1}/{config.reroll_max_per_ball}.",
            view=confirm,
            ephemeral=True,
        )
        await confirm.wait()
        if not confirm.value:
            await interaction.edit_original_response(
                content="Reroll cancelled." if confirm.value is False else "Reroll timed out.", view=None
            )
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        try:
            cost, old, new, used = await sync_to_async(_perform_reroll)(countryball.pk, player.pk, config)
        except InsufficientFunds as exc:
            await interaction.edit_original_response(
                content=f"You need {format_currency(exc.cost, shortened=False, bot=bot)} for this reroll.", view=None
            )
            return
        except RuntimeError as exc:
            reason = {
                "gone": f"That {settings.collectible_name} no longer exists.",
                "not-owner": f"That {settings.collectible_name} no longer belongs to you.",
                "locked": f"That {settings.collectible_name} is now locked for a trade.",
                "max-rerolls": f"That {settings.collectible_name} has reached its reroll limit.",
            }.get(str(exc), "The reroll could not be completed. Please try again later.")
            await interaction.edit_original_response(content=reason, view=None)
            return

        await interaction.edit_original_response(
            content=(
                f"🎲 Rerolled **{countryball.description(short=True)}**!\n"
                f"`{old[0]:+d}%/{old[1]:+d}%` → **`{new[0]:+d}%/{new[1]:+d}%`**\n"
                f"Rerolls used: {used}/{config.reroll_max_per_ball}."
            ),
            view=None,
        )

    @shop.command(name="catchphrase", description="Buy a custom catch phrase, shown under your catch messages.")
    @app_commands.describe(phrase="Your new catch phrase. Buying again replaces the old one.")
    async def catchphrase(interaction: discord.Interaction["BallsDexBot"], phrase: str):
        config = await CurrencySettings.aget_solo()
        if not config.catch_phrase_enabled:
            await interaction.response.send_message("Catch phrases are currently disabled.", ephemeral=True)
            return

        phrase = phrase.strip()
        if error := validate_phrase(phrase, config.catch_phrase_max_length):
            await interaction.response.send_message(error, ephemeral=True)
            return

        cost = config.catch_phrase_cost
        confirm = ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"Set your catch phrase to:\n> *{phrase}*\nfor "
            f"**{format_currency(cost, shortened=False, bot=bot)}**?\n"
            "It will be shown publicly under your catch messages. Phrases are logged and "
            "abusive ones will be removed.",
            view=confirm,
            ephemeral=True,
        )
        await confirm.wait()
        if not confirm.value:
            await interaction.edit_original_response(
                content="Purchase cancelled." if confirm.value is False else "Purchase timed out.", view=None
            )
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        try:
            await sync_to_async(_perform_catch_phrase)(player.pk, phrase, cost)
        except InsufficientFunds as exc:
            await interaction.edit_original_response(
                content=f"You need {format_currency(exc.cost, shortened=False, bot=bot)} for a catch phrase.", view=None
            )
            return

        await interaction.edit_original_response(content=f"✅ Your catch phrase is now:\n> *{phrase}*", view=None)

    @shop.command(name="spawnboost", description=f"Boost {settings.collectible_name} spawns for the whole server.")
    async def spawnboost(interaction: discord.Interaction["BallsDexBot"]):
        config = await CurrencySettings.aget_solo()
        if not config.spawn_boost_enabled:
            await interaction.response.send_message("Spawn boosts are currently disabled.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("This can only be bought in a server.", ephemeral=True)
            return

        active = (
            await SpawnBoost.objects.filter(guild_id=interaction.guild.id, expires_at__gt=timezone.now())
            .order_by("-expires_at")
            .afirst()
        )
        if active:
            await interaction.response.send_message(
                f"A x{active.multiplier:g} spawn boost is already active in this server until "
                f"{format_dt(active.expires_at)}.",
                ephemeral=True,
            )
            return

        cost = config.spawn_boost_cost
        confirm = ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"Buy a **x{config.spawn_boost_multiplier:g} spawn boost** for this server, lasting "
            f"**{config.spawn_boost_duration_hours:g} hours**, for "
            f"**{format_currency(cost, shortened=False, bot=bot)}**?",
            view=confirm,
            ephemeral=True,
        )
        await confirm.wait()
        if not confirm.value:
            await interaction.edit_original_response(
                content="Purchase cancelled." if confirm.value is False else "Purchase timed out.", view=None
            )
            return

        player, _ = await Player.objects.aget_or_create(discord_id=interaction.user.id)
        try:
            boost = await sync_to_async(_perform_spawn_boost)(player.pk, interaction.guild.id, config)
        except InsufficientFunds as exc:
            await interaction.edit_original_response(
                content=f"You need {format_currency(exc.cost, shortened=False, bot=bot)} for a spawn boost.", view=None
            )
            return
        except RuntimeError:
            await interaction.edit_original_response(
                content="A spawn boost was just activated in this server; try again once it expires.", view=None
            )
            return

        boosts.invalidate(interaction.guild.id)
        await interaction.edit_original_response(content="✅ Spawn boost purchased!", view=None)
        # Announce publicly: the whole server benefits, and the buyer gets the credit.
        if interaction.channel and isinstance(interaction.channel, discord.abc.Messageable):
            try:
                await interaction.channel.send(
                    f"🚀 {interaction.user.mention} bought a **x{boost.multiplier:g} spawn boost** "
                    f"for this server! It lasts until {format_dt(boost.expires_at)}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

    return shop
