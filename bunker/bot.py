"""Discord adapter. Each action commits under a per-game lock before publishing."""

import asyncio
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from . import engine
from .presentation import board, hand, public_players
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("bunker")


async def answer(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


class Menu(discord.ui.View):
    def __init__(self, client, game, uid):
        super().__init__(timeout=180)
        self.client, self.game_id, self.uid = client, game["id"], str(uid)
        self.epoch = game["epoch"]
        # A private menu remains valid through other players' actions in the same phase.

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.uid:
            await answer(interaction, "Откройте своё меню.")
            return False
        return True

    async def on_error(self, interaction, error, item):
        LOG.exception("Private menu failed", exc_info=error)
        await answer(interaction, "Не удалось выполнить действие. Откройте меню снова.")

    def select(self, placeholder, options, callback):
        select = discord.ui.Select(placeholder=placeholder, options=[
            discord.SelectOption(label=label[:100], value=value) for value, label in options])
        select.callback = callback
        self.add_item(select)
        return select


class Choose(Menu):
    def __init__(self, client, game, uid, action, options, argument, extra=None):
        super().__init__(client, game, uid)
        self.action, self.argument, self.extra = action, argument, extra or {}
        self.selector = self.select("Выберите вариант", options, self.chosen)

    async def chosen(self, interaction):
        args = {**self.extra, self.argument: self.selector.values[0]}
        await self.client.mutate(interaction, self.game_id, self.action, self.epoch, **args)
        self.stop()


class SpecialMenu(Menu):
    def __init__(self, client, game, uid):
        super().__init__(client, game, uid)
        p = engine.player(game, uid)
        op = p["special"]["operation"]
        self.selectors = {}
        if op in {"swap", "peek", "reveal_other"}:
            choices = [(q["id"], q["name"]) for q in engine.active(game) if q["id"] != str(uid)]
            if choices:
                self.selectors["target"] = self.select("Игрок", choices, self.remember)
        if op in {"peek", "reveal_other"}:
            self.selectors["slot"] = self.select("Характеристика", list(engine.LABELS.items()), self.remember)
        button = discord.ui.Button(label="Использовать спецкарту", style=discord.ButtonStyle.danger)
        button.callback = self.use
        self.add_item(button)

    async def remember(self, interaction):
        await interaction.response.defer()

    async def use(self, interaction):
        if any(not selector.values for selector in self.selectors.values()):
            await answer(interaction, "Выберите игрока и характеристику в меню.")
            return
        args = {key: selector.values[0] for key, selector in self.selectors.items()}
        await self.client.mutate(interaction, self.game_id, "special", self.epoch, **args)
        self.stop()


class TimerModal(discord.ui.Modal):
    def __init__(self, client, game):
        super().__init__(title="Таймеры в секундах · 0 выключает")
        self.client, self.game_id, self.epoch = client, game["id"], game["epoch"]
        self.inputs = {}
        for key, label in [("reveal", "Раскрытие — весь круг"), ("discussion", "Обсуждение / защита"),
                           ("vote", "Первый голос"), ("revise", "Смена голоса"),
                           ("final", "Событие / решение в финале")]:
            item = discord.ui.TextInput(label=label, default=str(game["timers"][key]), max_length=4)
            self.inputs[key] = item
            self.add_item(item)

    async def on_submit(self, interaction):
        try:
            values = {key: int(item.value) for key, item in self.inputs.items()}
        except ValueError:
            await answer(interaction, "Введите целое число секунд в каждом поле.")
            return
        await self.client.mutate(interaction, self.game_id, "timers", self.epoch, values=values)


class BoardView(discord.ui.View):
    def __init__(self, client, game_id):
        super().__init__(timeout=None)
        self.client, self.game_id = client, game_id
        for action, label, row in [
            ("join", "Войти", 0), ("leave", "Выйти из лобби", 0),
            ("hand", "Мои карты", 0), ("public", "Карты игроков", 0),
            ("reveal", "Раскрыть карту", 1), ("vote", "Голосовать", 1),
            ("special", "Особые условия", 1), ("advance", "Следующий этап", 2),
            ("pause", "Пауза / продолжить", 2), ("timers", "Таймеры", 2),
            ("transfer", "Передать ведущего", 2), ("cancel", "Остановить партию", 2),
        ]:
            button = discord.ui.Button(label=label, row=row, custom_id=f"bunker:{game_id}:{action}")
            async def clicked(interaction, selected=action):
                await self.client.open_menu(interaction, self.game_id, selected)
            button.callback = clicked
            self.add_item(button)

    async def on_error(self, interaction, error, item):
        LOG.exception("Board action failed", exc_info=error)
        await answer(interaction, "Не удалось выполнить действие. Попробуйте открыть меню снова.")


class BunkerClient(discord.Client):
    def __init__(self, store_path=None):
        super().__init__(intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.store = Store(store_path or ROOT / "data" / "games.sqlite3")
        self.locks = defaultdict(asyncio.Lock)
        self.channel_locks = defaultdict(asyncio.Lock)
        self.dirty = set()
        self.register_commands()

    def register_commands(self):
        @self.tree.command(name="bunker", description="Создать партию «Бункера»")
        @app_commands.guild_only()
        @app_commands.describe(players="Количество игроков", public="Показывать голоса сразу",
                               decision="Одно общее решение перед финалом",
                               events="Случайное особое событие", automatic="Автопереход по таймерам")
        async def create(interaction: discord.Interaction, players: app_commands.Range[int, 4, 12] = 6,
                         public: bool = True, decision: bool = True, events: bool = False,
                         automatic: bool = False):
            if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
                await answer(interaction, "Создайте партию в текстовом канале сервера.")
                return
            permissions = interaction.app_permissions
            can_send = (permissions.send_messages_in_threads if isinstance(interaction.channel, discord.Thread)
                        else permissions.send_messages)
            if not (can_send and permissions.embed_links and permissions.read_message_history):
                await answer(interaction, "Боту нужны отправка сообщений, вставка ссылок и чтение истории канала.")
                return
            await interaction.response.defer(ephemeral=True)
            async with self.channel_locks[interaction.channel_id]:
                game = engine.new_game(interaction.channel_id, interaction.user.id,
                                       interaction.user.display_name, players, public, decision, events, automatic)
                try:
                    self.store.create(game)
                except engine.RuleError as error:
                    await answer(interaction, str(error))
                    return
                self.dirty.add(game["id"])
                try:
                    async with self.locks[game["id"]]:
                        await self.publish(game)
                except discord.HTTPException:
                    LOG.exception("Could not create board")
                    await answer(interaction, "Лобби сохранено. Бот повторит отправку панели. Доступна команда /bunker_resume.")
                    return
            await answer(interaction, "Лобби создано. Настройте таймеры и пригласите друзей кнопкой «Войти».")

        @self.tree.command(name="bunker_resume", description="Восстановить панель текущей партии")
        @app_commands.guild_only()
        async def resume(interaction: discord.Interaction):
            game = self.store.for_channel(interaction.channel_id)
            if not game:
                await answer(interaction, "В этом канале нет активной партии.")
                return
            await interaction.response.defer(ephemeral=True)
            async with self.locks[game["id"]]:
                game = self.store.get(game["id"])
                self.dirty.add(game["id"])
                await self.publish(game)
            await answer(interaction, "Панель партии обновлена.")

        @self.tree.command(name="bunker_help", description="Правила и управление партией")
        async def help_command(interaction: discord.Interaction):
            await answer(interaction,
                "Создайте лобби командой /bunker. Все игроки нажимают «Войти». Ведущий настраивает таймеры "
                "и нажимает «Следующий этап». В первом круге профессия открывается автоматически, "
                "вы выбираете ещё одну карту. Дальше раскрывается по одной.\n\n"
                "После голосования есть отдельное окно для одной смены голоса. При ничьей — защита "
                "и повторный выбор. Изгнанные наблюдают. Спецкарта доступна во время голосования, "
                "пока персонаж в игре и не в рейде. В финале оставшиеся скрытые карты влияют на историю.\n\n"
                "В ручном режиме окончание таймера даёт сигнал ведущему. «Следующий этап» завершает "
                "текущий этап, пропущенные раскрытия выполняются случайно, пропущенный голос — воздержание. "
                "Пауза останавливает все действия. /bunker_resume восстанавливает панель.\n\n"
                "Первая сборка использует «Сильный мороз» со случайным бункером. Рейд доступен от 8 игроков.")

        @self.tree.error
        async def command_error(interaction, error):
            LOG.error("Slash command error: %s", type(error).__name__)
            await answer(interaction, "Команда не выполнена. Проверьте права бота и попробуйте снова.")

    async def setup_hook(self):
        for game in self.store.all_active():
            self.add_view(BoardView(self, game["id"]), message_id=game.get("message"))
            self.dirty.add(game["id"])
        guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.countdowns.start()

    async def on_ready(self):
        LOG.info("Discord connected as %s", self.user)

    async def publish(self, game):
        channel = self.get_channel(game["channel"]) or await self.fetch_channel(game["channel"])
        view = BoardView(self, game["id"])
        if game.get("message"):
            try:
                await channel.get_partial_message(game["message"]).edit(embeds=board(game), view=view)
            except discord.NotFound:
                game["message"] = None
        if not game.get("message"):
            message = await channel.send(embeds=board(game), view=view)
            game["message"] = message.id
            self.store.save(game)
        self.dirty.discard(game["id"])

    async def mutate(self, interaction, game_id, action, epoch=None, **kwargs):
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.locks[game_id]:
            try:
                game = self.store.get(game_id)
                engine.require(interaction.channel_id == game["channel"], "Эта панель принадлежит другому каналу.")
                if epoch is not None:
                    engine.require(epoch == game["epoch"], "Этап уже изменился. Откройте меню снова.")
                message = engine.apply(game, interaction.user.id, action, time.time(), **kwargs)
                self.store.save(game)
            except engine.RuleError as error:
                await answer(interaction, str(error))
                return
            self.dirty.add(game_id)
            try:
                await self.publish(game)
            except discord.HTTPException:
                LOG.warning("Board refresh delayed for %s", game_id)
            await answer(interaction, message)

    async def open_menu(self, interaction, game_id, action):
        try:
            game = self.store.get(game_id)
            engine.require(interaction.channel_id == game["channel"], "Панель другого канала.")
            uid = str(interaction.user.id)
            if action == "hand":
                await answer(interaction, hand(game, uid))
                return
            if action == "public":
                embeds = public_players(game)
                await interaction.response.send_message(embed=embeds[0], ephemeral=True)
                for embed in embeds[1:]:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                return
            if action in {"join", "leave", "pause"}:
                await self.mutate(interaction, game_id, action, game["epoch"], name=interaction.user.display_name)
                return
            if action in {"advance", "transfer", "timers", "cancel"}:
                engine.require(uid == game["host"], "Эта кнопка доступна ведущему.")
            if action == "advance":
                await self.mutate(interaction, game_id, action, game["epoch"])
                return
            if action == "cancel":
                view = Choose(self, game, uid, "cancel", [("yes", "Подтверждаю остановку партии")], "confirmation")
            elif action == "timers":
                engine.require(game["phase"] == "lobby", "Таймеры задаются в лобби.")
                await interaction.response.send_modal(TimerModal(self, game))
                return
            elif action == "transfer":
                options = [(p["id"], p["name"]) for p in game["players"] if p["id"] != uid]
                engine.require(bool(options), "Сначала должен присоединиться ещё один игрок.")
                view = Choose(self, game, uid, "transfer", options, "target")
            else:
                p = engine.player(game, uid)
                engine.require(p["status"] == "active" and "cards" in p, "Действие станет доступно участникам игры после старта.")
                if action == "reveal":
                    engine.require(game["phase"] == "reveal" and not p["turn_done"], "Сейчас вы не раскрываете карту.")
                    options = [(slot, engine.LABELS[slot] + " — " + engine.card_text(p["cards"][slot]))
                               for slot in engine.hidden(p)]
                    view = Choose(self, game, uid, action, options, "slot")
                elif action == "vote":
                    if game["phase"] == "final":
                        options = [("heating", "Направить резерв на отопление"), ("radio", "Направить резерв на радио")]
                    elif game["phase"] == "event":
                        options = [(q["id"], q["name"]) for q in engine.active(game)]
                    else:
                        engine.require(game["phase"] in {"vote", "revise"}, "Голосование сейчас закрыто.")
                        options = [(q["id"], q["name"]) for q in engine.active(game)
                                   if q["id"] in game["candidates"] and q["id"] != uid]
                    engine.require(bool(options), "Нет подходящих кандидатов.")
                    view = Choose(self, game, uid, action, options, "target")
                elif action == "special":
                    engine.require(game["phase"] not in {"final", "finished", "cancelled"},
                                   "Бункер закрыт. Спецкарты больше не действуют.")
                    engine.require(not p["special_used"], "Спецкарта уже использована.")
                    view = SpecialMenu(self, game, uid)
                    await interaction.response.send_message(
                        p["special"]["title"] + "\n" + p["special"]["description"], view=view, ephemeral=True)
                    return
                else:
                    raise engine.RuleError("Неизвестное меню.")
            await interaction.response.send_message("Выберите действие.", view=view, ephemeral=True)
        except engine.RuleError as error:
            await answer(interaction, str(error))

    @tasks.loop(seconds=2)
    async def countdowns(self):
        ids = {g["id"] for g in self.store.all_active()} | self.dirty
        for game_id in ids:
            async with self.locks[game_id]:
                try:
                    game = self.store.get(game_id)
                    now = time.time()
                    if game["deadline"] is not None and game["deadline"] <= now and not game["paused"]:
                        if game["auto"]:
                            engine.advance(game, now, engine.Random())
                        else:
                            game["deadline"] = None
                            game["timer_elapsed"] = True
                        self.store.save(game)
                        self.dirty.add(game_id)
                    if game_id in self.dirty:
                        await self.publish(game)
                except (discord.HTTPException, OSError):
                    LOG.warning("Will retry board refresh for %s", game_id)
                except Exception:
                    LOG.exception("Timer failed for %s", game_id)

    @countdowns.before_loop
    async def before_countdowns(self):
        await self.wait_until_ready()

    async def close(self):
        self.countdowns.cancel()
        await super().close()
        self.store.close()


def main():
    load_dotenv(ROOT / ".env")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("Заполните DISCORD_TOKEN в файле .env. Инструкция — ЗАПУСК.md")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = BunkerClient()
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
