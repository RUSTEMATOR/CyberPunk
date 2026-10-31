"""Router assembly. Order matters: real commands, admin, the 'Скоро' stubs, and the text input last."""

from __future__ import annotations

from aiogram import Router

# importing the modules registers their screens and text handlers
from app.bot.handlers import (  # noqa: F401
    arena, actions, activities, bigmoney, pcorp, smartgun, sortie, dgame, loans, vampire, reinforce, perks, blackwall, crisis, challenge, corpraid, control, pguard, army, enemies, ambition, finale, lghall, exped, relics, hariton, arcade, budget, elitejob, pmc, dispatch, paths, handbook, studio, city, net, aeon, elysium, libria, world, hubs, pservices, requests, purge, chatgames, economy, ending, admin, ai, admin_llm, admin_world, combat, crime, daily, entry, events, gigs, jail, legacy, legends, lifecheck, menu, minigames, npcchat, onboarding, players, property, raid, psychosis, relations, rename, ripper, upgrades, vehicles, vows,
    week, who, work, inline, possess, chromeab, dungeon, wanted, pally, corpforce, blacktears, equilib, clones, avatar, capri, look, pboss, aidash,
)
from app.bot.handlers.commands import build_soon_router


def build_routers() -> list[Router]:
    return [pally.router, pboss.router, avatar.router, inline.router, legends.router, who.router, legacy.router, entry.play_router, admin.router, admin_llm.router, admin_world.router, daily.router, minigames.router, raid.router, players.router, bigmoney.router, handbook.router, requests.router, purge.router, build_soon_router(), entry.text_router]
