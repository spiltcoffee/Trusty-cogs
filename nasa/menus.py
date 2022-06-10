from __future__ import annotations

import logging
from typing import Any, List, Optional

import discord

# from discord.ext.commands.errors import BadArgument
from redbot.core.commands import commands
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_list, humanize_number
from redbot.vendored.discord.ext import menus

from .models import (
    Collection,
    EPICData,
    Event,
    ManifestPhoto,
    NASAAstronomyPictureOfTheDay,
    PhotoManifest,
    RoverPhoto,
)

log = logging.getLogger("red.Trusty-cogs.NASACog")
_ = Translator("NASA", __file__)


class NASAImagesCollection(menus.ListPageSource):
    def __init__(self, collection: Collection):
        self.collection = collection
        super().__init__(collection.items, per_page=1)

    async def format_page(self, view: BaseMenu, page):
        url = None
        if page.data[0].media_type == "video":
            media_links = await view.cog.request(page.href, include_api_key=False)
            for link in media_links:
                if link.endswith("orig.mp4"):
                    url = link.replace(" ", "%20")
        em = discord.Embed(
            title=page.data[0].title,
            description=page.data[0].description,
            timestamp=page.data[0].date_created,
            url=url,
        )
        em.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        embeds = []
        for link in page.links:
            if link.rel != "preview":
                continue
            e = em.copy()
            e.set_image(url=link.href.replace(" ", "%20"))
            embeds.append(e)

        return {"embeds": embeds}


class MarsRoverManifest(menus.ListPageSource):
    def __init__(self, manifest: PhotoManifest):
        self.manifest = manifest
        super().__init__(manifest.photos, per_page=10)

    async def format_page(self, view: BaseMenu, photos: List[ManifestPhoto]):
        description = ""
        for photo in photos:
            description += (
                f"Sol: {photo.sol} - Earth Date: {photo.earth_date}\n"
                f"Number of Photos: {photo.total_photos} - Cameras: {humanize_list(photo.cameras)}\n\n"
            )
        em = discord.Embed(title=self.manifest.name, description=description)
        em.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        return em


class MarsRoverPhotos(menus.ListPageSource):
    def __init__(self, photos: List[RoverPhoto]):
        super().__init__(photos, per_page=1)

    async def format_page(self, view: BaseMenu, photo: RoverPhoto):
        title = f"{photo.camera.full_name} on {photo.rover.name}"
        description = f"Sol: {photo.sol}\nEarth Date: {photo.earth_date}"
        em = discord.Embed(title=title, description=description)
        em.set_image(url=photo.img_src)
        em.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        return em


class NASAEventPages(menus.ListPageSource):
    def __init__(self, events: List[Event]):
        super().__init__(events, per_page=1)

    async def format_page(self, view: BaseMenu, event: Event):
        em = discord.Embed(title=event.title, description=event.description)
        em.set_image(url=event.image_url)
        coordinates = event.geometry[-1].coordinates
        lat = coordinates[1]
        lon = coordinates[0]
        maps_url = f"https://www.google.com/maps/search/?api=1&query={lat}%2C{lon}"
        coords = f"[Latitude: {lat}\nLongitude: {lon}]({maps_url})"
        em.add_field(name="Coordinates", value=coords)
        value = ""
        for geometry in reversed(event.geometry):
            if len(value) >= 512:
                break
            if geometry.magnitudeValue is None:
                continue
            timestamp = discord.utils.format_dt(geometry.date)
            value += f"{geometry.magnitudeValue} {geometry.magnitudeUnit} - {timestamp}\n"
        if value:
            em.add_field(name="Data", value=value)
        sources = ""
        for source in event.sources:
            sources += f"[{source.id}]({source.url})\n"
        if sources:
            em.add_field(name="Sources", value=sources)
        return em


class NASAapod(menus.ListPageSource):
    def __init__(self, pages: List[NASAAstronomyPictureOfTheDay]):
        super().__init__(pages, per_page=1)

    async def format_page(self, view: Optional[BaseMenu], page: NASAAstronomyPictureOfTheDay):
        em = discord.Embed(
            title=page.title, description=page.explanation, timestamp=page.date, url=page.url
        )
        em.set_image(url=page.hdurl if page.hdurl else page.url)
        if page.thumbnail_url:
            em.set_thumbnail(url=page.thumbnail_url)
        if page.copyright:
            em.add_field(name="Copyright (c)", value=page.copyright)
        if view is not None:
            em.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        return em


class EPICPages(menus.ListPageSource):
    def __init__(self, pages: List[EPICData], enhanced: bool = False):
        super().__init__(pages, per_page=1)
        self.enhanced = enhanced

    def get_distance(self, distance: float) -> str:
        return f"{humanize_number(int(distance))} km ({humanize_number(int(distance*0.621371))} Miles)"

    async def format_page(self, view: BaseMenu, page: EPICData):
        url = page.natural_url if not self.enhanced else page.enhanced_url
        description = (
            f"{page.caption}\n\n"
            f"Distance from Earth: {self.get_distance(page.coords.dscovr_j2000_position.distance)}\n"
            f"Distance from Sun: {self.get_distance(page.coords.sun_j2000_position.distance)}\n"
            f"Distance from Moon: {self.get_distance(page.coords.lunar_j2000_position.distance)}\n"
        )

        em = discord.Embed(title=page.identifier, description=description, url=url)
        em.set_image(url=url)
        return em


class StopButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = "\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}"

    async def callback(self, interaction: discord.Interaction):
        self.view.stop()
        if interaction.message.flags.ephemeral:
            await interaction.response.edit_message(view=None)
            return
        await interaction.message.delete()


class ForwardButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = "\N{BLACK RIGHT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}"

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_checked_page(self.view.current_page + 1, interaction=interaction)


class BackButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = "\N{BLACK LEFT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}"

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_checked_page(self.view.current_page - 1, interaction=interaction)


class LastItemButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = (
            "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_page(self.view._source.get_max_pages() - 1, interaction=interaction)


class FirstItemButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = (
            "\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_page(0, interaction=interaction)


class SkipForwardkButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = (
            "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_page(0, skip_next=True, interaction=interaction)


class SkipBackButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = (
            "\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_page(0, skip_prev=True, interaction=interaction)


class BaseMenu(discord.ui.View):
    def __init__(
        self,
        source: menus.PageSource,
        cog: Optional[commands.Cog] = None,
        page_start: int = 0,
        timeout: int = 180,
        **kwargs: Any,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self._source = source
        self.ctx: commands.Context = None
        self.message: discord.Message = None
        self.page_start = page_start
        self.current_page = page_start
        self.forward_button = ForwardButton(discord.ButtonStyle.grey, 0)
        self.back_button = BackButton(discord.ButtonStyle.grey, 0)
        self.first_item = FirstItemButton(discord.ButtonStyle.grey, 0)
        self.last_item = LastItemButton(discord.ButtonStyle.grey, 0)
        self.stop_button = StopButton(discord.ButtonStyle.red, 0)
        self.add_item(self.stop_button)
        self.add_item(self.first_item)
        self.add_item(self.back_button)
        self.add_item(self.forward_button)
        self.add_item(self.last_item)

    async def on_timeout(self):
        await self.message.edit(view=None)

    @property
    def source(self):
        return self._source

    def disable_pagination(self):
        if not self.source.is_paginating():
            self.forward_button.disabled = True
            self.back_button.disabled = True
            self.first_item.disabled = True
            self.last_item.disabled = True
        else:
            self.forward_button.disabled = False
            self.back_button.disabled = False
            self.first_item.disabled = False
            self.last_item.disabled = False

    async def start(self, ctx: commands.Context):
        await self.source._prepare_once()
        self.ctx = ctx
        self.message = await self.send_initial_message(ctx)

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(self.source.format_page, self, page)
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"content": value, "embed": None}
        elif isinstance(value, discord.Embed):
            return {"embed": value, "content": None}

    async def show_page(self, page_number: int, interaction: discord.Interaction):
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        self.disable_pagination()
        await interaction.response.edit_message(**kwargs, view=self)

    async def send_initial_message(self, ctx: commands.Context) -> discord.Message:
        """|coro|
        The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.
        This implementation shows the first page of the source.
        """
        page = await self._source.get_page(self.page_start)
        kwargs = await self._get_kwargs_from_page(page)
        self.disable_pagination()
        return await ctx.send(**kwargs, view=self)

    async def show_checked_page(self, page_number: int, interaction: discord.Interaction) -> None:
        max_pages = self._source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number, interaction)
            elif page_number >= max_pages:
                await self.show_page(0, interaction)
            elif page_number < 0:
                await self.show_page(max_pages - 1, interaction)
            elif max_pages > page_number >= 0:
                await self.show_page(page_number, interaction)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    async def interaction_check(self, interaction: discord.Interaction):
        """Just extends the default reaction_check to use owner_ids"""
        if interaction.user.id not in (*self.ctx.bot.owner_ids, self.ctx.author.id):
            await interaction.response.send_message(
                content=_("You are not authorized to interact with this."), ephemeral=True
            )
            return False
        return True
