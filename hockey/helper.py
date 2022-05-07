from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Dict,
    List,
    Literal,
    NamedTuple,
    Optional,
    Pattern,
    Tuple,
    Union,
)

import discord
import pytz
from discord.ext.commands.converter import Converter
from discord.ext.commands.errors import BadArgument
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.data_manager import cog_data_path
from redbot.core.i18n import Translator
from redbot.core.utils import AsyncIter

from .constants import TEAMS
from .teamentry import TeamEntry

if TYPE_CHECKING:
    from .game import Game


_ = Translator("Hockey", __file__)

log = logging.getLogger("red.trusty-cogs.Hockey")

DATE_RE = re.compile(
    r"((19|20)\d\d)[- \/.](0[1-9]|1[012]|[1-9])[- \/.](0[1-9]|[12][0-9]|3[01]|[1-9])"
)
DAY_REF_RE = re.compile(r"(yesterday|tomorrow|today)", re.I)

YEAR_RE = re.compile(r"((19|20)\d\d)-?\/?((19|20)\d\d)?")
# https://www.regular-expressions.info/dates.html

TIMEZONE_RE = re.compile(r"|".join(re.escape(zone) for zone in pytz.common_timezones), flags=re.I)

ACTIVE_TEAM_RE_STR = r"|".join(
    rf"{team}|{data['tri_code']}|{'|'.join(n for n in data['nickname'])}"
    for team, data in TEAMS.items()
    if data["active"]
)
ACTIVE_TEAM_RE = re.compile(ACTIVE_TEAM_RE_STR, flags=re.I)

VERSUS_RE = re.compile(r"vs\.?|versus", flags=re.I)


def utc_to_local(utc_dt: datetime, new_timezone: str = "US/Pacific") -> datetime:
    eastern = pytz.timezone(new_timezone)
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz=eastern)


def get_chn_name(game: Game) -> str:
    """
    Creates game day channel name
    """
    timestamp = utc_to_local(game.game_start)
    chn_name = "{}-vs-{}-{}-{}-{}".format(
        game.home_abr, game.away_abr, timestamp.year, timestamp.month, timestamp.day
    )
    return chn_name.lower()


class YearFinder(Converter):
    """
    Validates Year format

    for use in the `[p]nhl games` command to pull up specific dates
    """

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> re.Match:
        find = YEAR_RE.search(argument)
        if find:
            return find
        else:
            raise BadArgument(_("`{arg}` is not a valid year.").format(arg=argument))


class DateFinder(discord.app_commands.Transformer):
    """
    Converter for `YYYY-MM-DD` date formats

    for use in the `[p]nhl games` command to pull up specific dates
    """

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> datetime:
        find = DATE_RE.search(argument)
        if find:
            date_str = f"{find.group(1)}-{find.group(3)}-{find.group(4)}"
            return datetime.strptime(date_str, "%Y-%m-%d").astimezone(timezone.utc)
        else:
            raise BadArgument()

    @classmethod
    async def transform(cls, interaction: discord.Interaction, value: str) -> datetime:
        find = DATE_RE.search(value)
        if find:
            date_str = f"{find.group(1)}-{find.group(3)}-{find.group(4)}"
            return datetime.strptime(date_str, "%Y-%m-%d").astimezone(timezone.utc)
        else:
            return datetime.now(timezone.utc)


class TeamFinder(discord.app_commands.Transformer):
    """
    Converter for Teams
    """

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> str:
        potential_teams = argument.split()
        result = set()
        include_all = ctx.command.name in ["setup", "add"]
        include_inactive = ctx.command.name in ["roster"]
        for team, data in TEAMS.items():
            if "Team" in team:
                continue
            if not include_inactive and not data["active"]:
                continue
            nick = data["nickname"]
            short = data["tri_code"]
            pattern = fr"{short}\b|" + r"|".join(fr"\b{i}\b" for i in team.split())
            if nick:
                pattern += r"|" + r"|".join(fr"\b{i}\b" for i in nick)
            # log.debug(pattern)
            reg: Pattern = re.compile(fr"\b{pattern}", flags=re.I)
            for pot in potential_teams:
                find = reg.findall(pot)
                if find:
                    log.debug(reg)
                    log.debug(find)
                    result.add(team)
        if include_all and "all" in argument:
            result.add("all")
        if not result:
            raise BadArgument(_("You must provide a valid current team."))
        return list(result)[0]

    @classmethod
    async def transform(cls, interaction: discord.Interaction, argument: str) -> str:
        ctx = await interaction.client.get_context(interaction)
        return await cls.convert(ctx, argument)

    async def autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[discord.app_commands.Choice[str]]:
        team_choices = []
        include_all = interaction.command.name in ["setup", "add"]
        include_inactive = interaction.command.name in ["roster"]
        ret = []
        for t, d in TEAMS.items():
            team_choices.append(discord.app_commands.Choice(name=t, value=t))
        if include_all:
            team_choices.insert(0, discord.app_commands.Choice(name="All", value="all"))
        for choice in team_choices:
            if not include_inactive and not d["active"]:
                continue
            if current.lower() in choice.name.lower():
                ret.append(choice)
        return ret[:25]


class BasePlayer(NamedTuple):
    id: int
    name: str
    on_roster: Literal["Y", "N"]


class PlayerFinder(discord.app_commands.Transformer):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> List[BasePlayer]:
        now = datetime.utcnow()
        cog = ctx.cog
        saved = datetime.fromtimestamp(await cog.config.player_db())
        path = cog_data_path(cog) / "players.json"
        if (now - saved) > timedelta(days=1) or not path.exists():
            async with cog.session.get(
                "https://records.nhl.com/site/api/player?include=id&include=fullName&include=onRoster"
            ) as resp:
                with path.open(encoding="utf-8", mode="w") as f:
                    json.dump(await resp.json(), f)
            await cog.config.player_db.set(int(now.timestamp()))
        with path.open(encoding="utf-8", mode="r") as f:

            players = []
            async for player in AsyncIter(json.loads(f.read())["data"], steps=100):
                if argument.lower() in player["fullName"].lower():
                    if player["onRoster"] == "N":
                        players.append(
                            BasePlayer(
                                id=player["id"],
                                name=player["fullName"],
                                on_roster=player["onRoster"],
                            )
                        )
                    else:
                        players.insert(
                            0,
                            BasePlayer(
                                id=player["id"],
                                name=player["fullName"],
                                on_roster=player["onRoster"],
                            ),
                        )
        return players

    @classmethod
    async def transform(cls, interaction: discord.Interaction, argument: str) -> List[BasePlayer]:
        now = datetime.utcnow()
        cog = interaction.client.get_cog("Hockey")
        saved = datetime.fromtimestamp(await cog.config.player_db())
        path = cog_data_path(cog) / "players.json"
        if (now - saved) > timedelta(days=1) or not path.exists():
            async with cog.session.get(
                "https://records.nhl.com/site/api/player?include=id&include=fullName&include=onRoster"
            ) as resp:
                with path.open(encoding="utf-8", mode="w") as f:
                    json.dump(await resp.json(), f)
            await cog.config.player_db.set(int(now.timestamp()))
        with path.open(encoding="utf-8", mode="r") as f:

            players = []
            async for player in AsyncIter(json.loads(f.read())["data"], steps=100):
                if argument.lower() in player["fullName"].lower():
                    if player["onRoster"] == "N":
                        players.append(
                            BasePlayer(
                                id=player["id"],
                                name=player["fullName"],
                                on_roster=player["onRoster"],
                            )
                        )
                    else:
                        players.insert(
                            0,
                            BasePlayer(
                                id=player["id"],
                                name=player["fullName"],
                                on_roster=player["onRoster"],
                            ),
                        )
        return players

    async def autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[discord.app_commands.Choice]:
        now = datetime.utcnow()
        saved = datetime.fromtimestamp(await self.config.player_db())
        path = cog_data_path(self) / "players.json"
        ret = []
        if (now - saved) > timedelta(days=1) or not path.exists():
            async with self.session.get(
                "https://records.nhl.com/site/api/player?include=id&include=fullName&include=onRoster"
            ) as resp:
                with path.open(encoding="utf-8", mode="w") as f:
                    json.dump(await resp.json(), f)
            await self.config.player_db.set(int(now.timestamp()))
        with path.open(encoding="utf-8", mode="r") as f:
            data = json.loads(f.read())["data"]
            for player in data:
                if current.lower() in player["fullName"].lower():
                    ret.append(
                        discord.app_commands.Choice(
                            name=player["fullName"], value=player["fullName"]
                        )
                    )
        return ret[:25]


class TimezoneFinder(Converter):
    """
    Converts user input into valid timezones for pytz to use
    """

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> str:
        find = TIMEZONE_RE.search(argument)
        if find:
            return find.group(0)
        else:
            raise BadArgument(
                _(
                    "`{argument}` is not a valid timezone. Please see "
                    "`{prefix}hockeyset timezone list`."
                ).format(argument=argument, prefix=ctx.clean_prefix)
            )


class LeaderboardFinder(discord.app_commands.Transformer):
    @classmethod
    async def convert(
        self, ctx: Context, argument: str
    ) -> Literal[
        "season",
        "weekly",
        "worst",
        "playoffs",
        "playoffs_weekly",
        "pre-season",
        "pre-season_weekly",
    ]:
        leaderboard_type = argument.replace(" ", "_").lower()
        if leaderboard_type in ["seasonal", "season"]:
            return "season"
        if leaderboard_type in ["weekly", "week"]:
            return "weekly"
        if leaderboard_type in ["playoffs", "playoff"]:
            return "playoffs"
        if leaderboard_type in ["playoffs_weekly", "playoff_weekly"]:
            return "playoffs_weekly"
        if leaderboard_type in ["pre-season", "preseason"]:
            return "pre-season"
        if leaderboard_type in ["pre-season_weekly", "preseason_weekly"]:
            return "pre-season_weekly"
        if leaderboard_type in ["worst"]:
            return "worst"
        return "season"

    @classmethod
    async def transform(
        cls, interaction: discord.Interaction, argument: str
    ) -> Literal[
        "season",
        "weekly",
        "worst",
        "playoffs",
        "playoffs_weekly",
        "pre-season",
        "pre-season_weekly",
    ]:
        return await cls.convert(interaction, argument)

    async def autocomplete(
        self, interaction: discord.Interaction, argument: str
    ) -> List[discord.app_commands.Choice[str]]:
        choices = [
            discord.app_commands.Choice(name="Seasonal", value="season"),
            discord.app_commands.Choice(name="Worst", value="worst"),
            discord.app_commands.Choice(name="Playoffs", value="playoffs"),
            discord.app_commands.Choice(name="Playoffs Weekly", value="playoffs_weekly"),
            discord.app_commands.Choice(name="Pre-Season", value="pre-season"),
            discord.app_commands.Choice(name="Pre-Season Weekly", value="pre-season_weekly"),
            discord.app_commands.Choice(name="Weekly", value="weekly"),
        ]
        return choices


class HockeyStates(Enum):
    preview = "preview"
    live = "live"
    goal = "goal"
    periodrecap = "periodrecap"
    final = "final"


class StateFinder(discord.app_commands.Transformer):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> HockeyStates:
        state_list = ["preview", "live", "final", "goal", "periodrecap"]
        if argument.lower() not in state_list:
            raise BadArgument('"{}" is not a valid game state.'.format(argument))
        return HockeyStates(argument.lower())

    @classmethod
    async def transform(cls, interaction: discord.Interaction, argument: str) -> HockeyStates:
        return await cls.convert(interaction, argument)  # type: ignore

    async def autocomplete(
        self, interaction: discord.Interaction, value: str
    ) -> List[discord.app_commands.Choice[str]]:
        return [
            discord.app_commands.Choice(name=v.name.title(), value=v.name) for v in HockeyStates
        ]


class Divisions(Enum):
    Metropolitan = "Metropolitan"
    Atlantic = "Atlantic"
    Central = "Central"
    Pacific = "Pacific"


class Conferences(Enum):
    Eastern = "Eastern"
    Western = "Western"


class StandingsFinder(discord.app_commands.Transformer):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> str:
        ret = ""
        try:
            ret = Divisions(argument.title()).name
        except ValueError:
            pass
        try:
            ret = Conferences(argument.title()).name
        except ValueError:
            pass
        if argument.lower() == "all":
            ret = "all"
        if argument.lower() == "league":
            ret = "league"
        if not ret:
            for division in Divisions:
                if argument.lower() in division.name.lower():
                    ret = division.name
            for conference in Conferences:
                if argument.lower() in conference.name.lower():
                    ret = conference.name
        return ret.lower()

    @classmethod
    async def transform(cls, ctx: Context, argument: str) -> str:
        ret = ""
        try:
            ret = Divisions(argument.title()).name
        except ValueError:
            pass
        try:
            ret = Conferences(argument.title()).name
        except ValueError:
            pass
        if argument.lower() == "all":
            ret = "all"
        if argument.lower() == "league":
            ret = "league"
        return ret.lower()

    async def autocomplete(
        self, interaction: discord.Interaction, argument: str
    ) -> List[discord.app_commands.Choice[str]]:
        choices = [
            discord.app_commands.Choice(name="All", value="all"),
            discord.app_commands.Choice(name="League", value="league"),
        ]
        choices += [discord.app_commands.Choice(name=d.name, value=d.name) for d in Divisions]
        choices += [discord.app_commands.Choice(name=d.name, value=d.name) for d in Conferences]
        return choices


async def check_to_post(
    bot: Red, channel: discord.TextChannel, channel_data: dict, post_state: str, game_state: str
) -> bool:
    if channel is None:
        return False
    channel_teams = channel_data.get("team", [])
    if channel_teams is None:
        await bot.get_cog("Hockey").config.channel(channel).team.clear()
        return False
    should_post = False
    if game_state in channel_data["game_states"]:
        for team in channel_teams:
            if team in post_state:
                should_post = True
    return should_post


async def get_team_role(guild: discord.Guild, home_team: str, away_team: str) -> Tuple[str, str]:
    """
    This returns the role mentions if they exist
    Otherwise it returns the name of the team as a str
    """
    home_role = None
    away_role = None

    for role in guild.roles:
        if "Montréal Canadiens" in home_team and "Montreal Canadiens" in role.name:
            home_role = role.mention
        elif role.name == home_team:
            home_role = role.mention
        if "Montréal Canadiens" in away_team and "Montreal Canadiens" in role.name:
            away_role = role.mention
        elif role.name == away_team:
            away_role = role.mention
    if home_role is None:
        home_role = home_team
    if away_role is None:
        away_role = away_team
    return home_role, away_role


async def get_team(bot: Red, team: str) -> TeamEntry:
    config = bot.get_cog("Hockey").config
    team_list = await config.teams()
    if team_list is None:
        team_list = []
        team_entry = TeamEntry("Null", team, 0, [], {}, [], "")
        team_list.append(team_entry.to_json())
        await config.teams.set(team_list)
    for teams in team_list:
        if team == teams["team_name"]:
            return teams
    # Add unknown teams to the config to track stats
    return_team = TeamEntry("Null", team, 0, [], {}, [], "")
    team_list.append(return_team.to_json())
    await config.teams.set(team_list)
    return return_team


async def get_channel_obj(
    bot: Red, channel_id: int, data: dict
) -> Optional[Union[discord.TextChannel, discord.Thread]]:
    """
    Requires a bot object to access config, channel_id, and channel config data
    Returns the channel object and sets the guild ID if it's missing from config

    This is used in Game objects and Goal objects so it's here to be shared
    between the two rather than duplicating the code
    """
    if not data["guild_id"]:
        channel = bot.get_channel(channel_id)
        if not channel:
            await bot.get_cog("Hockey").config.channel_from_id(channel_id).clear()
            log.info(f"{channel_id} channel was removed because it no longer exists")
            return None
        guild = channel.guild
        await bot.get_cog("Hockey").config.channel(channel).guild_id.set(guild.id)
        return channel
    guild = bot.get_guild(data["guild_id"])
    if not guild:
        await bot.get_cog("Hockey").config.channel_from_id(channel_id).clear()
        log.info(f"{channel_id} channel was removed because it no longer exists")
        return None
    channel = guild.get_channel(channel_id)
    thread = guild.get_thread(channel_id)
    if channel is None and thread is None:
        await bot.get_cog("Hockey").config.channel_from_id(channel_id).clear()
        log.info(f"{channel_id} channel was removed because it no longer exists")
        return None
    return channel or thread
