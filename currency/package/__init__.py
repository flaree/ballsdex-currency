from typing import TYPE_CHECKING

from .cog import setup

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

__all__ = ("setup",)
