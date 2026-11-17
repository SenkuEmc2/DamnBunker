"""Regression checks for secret cards, voting windows and event transitions."""

import asyncio
import json
import unittest
from copy import deepcopy
from pathlib import Path
from random import Random
from contextlib import contextmanager
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bunker import engine as e
from bunker.storage import Store
from bunker.presentation import board, public_players, hand


@contextmanager
def test_directory():
    # Keep test artifacts in the writable workspace; do not remove user files.
    directory = Path(__file__).resolve().parent.parent / "data" / "test-runs" / uuid4().hex
    directory.mkdir(parents=True)
    yield directory


def game(size=6, choice=True, events=False, seed=15):
    g = e.new_game(123, "0", "Игрок 0", size=size, choice=choice, events=events)
    for i in range(1, size):
        e.apply(g, str(i), "join", 1, name=f"Игрок {i}")
    e.apply(g, "0", "advance", 2, Random(seed))
    return g


def reveal_round(g):
    for p in e.active(g):
        if not p["turn_done"]:
            e.apply(g, p["id"], "reveal", 3, slot=e.hidden(p)[0])


def full_ballot(g):
    for p in e.active(g):
        target = next(i for i in g["candidates"] if i != p["id"])
        e.apply(g, p["id"], "vote", 10, target=target)
    assert g["phase"] == "revise"


class GameTests(unittest.TestCase):
    def test_complete_games_for_every_size_and_final_mode(self):
        for size in range(4, 13):
            for choice in (False, True):
                g = game(size, choice=choice)
                steps = 0
                while g["phase"] != "finished":
                    if g["phase"] == "reveal":
                        reveal_round(g)
                    elif g["phase"] == "vote":
                        full_ballot(g)
                    elif g["phase"] == "final":
                        for p in e.active(g):
                            e.apply(g, p["id"], "vote", 20, target="radio")
                        e.advance(g, 21, Random(2))
                    else:
                        e.advance(g, 20, Random(2))
                    steps += 1
                    self.assertLess(steps, 90)
                    self.assertTrue(all(e.hidden(p) for p in g["players"]))
                    self.assertLessEqual(sum(len(embed) for embed in board(g)), 6000)
                    for embed in board(g):
                        self.assertLessEqual(len(embed.description or ""), 4096)
                        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
                self.assertEqual(len(e.active(g)), g["capacity"])
                self.assertTrue(set(g["result"]["winners"]) <= {p["id"] for p in e.active(g)})

    def test_hand_counts_unique_cards_and_experience(self):
        for size, count in [(4, 6), (6, 7), (12, 8)]:
            g = game(size)
            ids = []
            for p in g["players"]:
                self.assertEqual(len(p["cards"]), count)
                self.assertEqual(len(e.hidden(p)), count-1)
                cards = p["cards"]
                self.assertLessEqual(cards["profession"]["experience"],
                                     cards["biology"]["age"] - cards["profession"]["minWorkAge"])
                ids.extend(c["id"] for c in cards.values())
                if size < 6:
                    self.assertNotEqual(p["special"].get("slot"), "phobia")
            self.assertEqual(len(ids), len(set(ids)))

    def test_single_change_after_all_votes(self):
        g = game()
        reveal_round(g)
        e.advance(g, 5, Random(1))
        e.apply(g, "0", "vote", 6, target="1")
        with self.assertRaises(e.RuleError):
            e.apply(g, "0", "vote", 7, target="2")
        for i in range(1, 6):
            e.apply(g, str(i), "vote", 7, target="0")
        self.assertEqual(g["phase"], "revise")
        e.apply(g, "0", "vote", 8, target="2")
        with self.assertRaises(e.RuleError):
            e.apply(g, "0", "vote", 9, target="3")

    def test_tie_then_lottery(self):
        g = game(4)
        reveal_round(g)
        e.advance(g, 5, Random(1))
        for _ in range(2):
            for i in range(4):
                e.apply(g, str(i), "vote", 7, target=str((i+1) % 4))
            e.advance(g, 8, Random(1))
            if g["phase"] == "defense":
                e.advance(g, 9, Random(1))
        self.assertEqual(sum(p["status"] == "exiled" for p in g["players"]), 1)

    def test_all_specials_and_no_secret_loss(self):
        for special in e.load_deck("special_conditions"):
            g = game()
            p, target = g["players"][:2]
            p["special"] = special
            reveal_round(g)
            e.advance(g, 5, Random(2))
            slot = e.hidden(target)[0]
            e.apply(g, "0", "special", 7, Random(5), target="1", slot=slot)
            self.assertTrue(p["special_used"])
            self.assertTrue(all(e.hidden(q) for q in g["players"]))
            self.assertEqual(g["phase"], "vote")
            with self.assertRaises(e.RuleError):
                e.apply(g, "0", "special", 8, Random(2), target="1", slot=slot)

    def test_exiled_raid_and_final_cannot_play_special(self):
        for status in ("exiled", "raid", "dead"):
            g = game()
            g["players"][0]["status"] = status
            with self.assertRaises(e.RuleError):
                e.apply(g, "0", "special", 7)
        g = game()
        g["phase"] = "final"
        with self.assertRaises(e.RuleError):
            e.apply(g, "0", "special", 7)

    def test_cannot_reveal_last_characteristic(self):
        g = game()
        p = g["players"][0]
        p["open"] = list(p["cards"])[:-1]
        with self.assertRaises(e.RuleError):
            e.apply(g, "0", "reveal", 7, slot=e.hidden(p)[0])
        q = g["players"][1]
        q["special"] = e.load_deck("special_conditions")[16]
        with self.assertRaises(e.RuleError):
            e.apply(g, "1", "special", 7, target="0", slot=e.hidden(p)[0])

    def test_raid_absence_return_and_capacity(self):
        for survive in (True, False):
            g = game(8, events=True)
            g["round"], g["phase"], g["event_kind"] = 2, "event", "raid"
            g["votes"] = {"0": "1"}
            p = g["players"][1]
            for card in p["cards"].values():
                card["tags"] = []
            if survive:
                p["cards"]["hobby"]["tags"] = ["navigation"]
                p["cards"]["baggage"]["tags"] = ["warmth"]
            e.advance(g, 9, Random(1))
            self.assertEqual(p["status"], "raid")
            self.assertNotIn("1", g["candidates"])
            with self.assertRaises(e.RuleError):
                e.apply(g, "1", "vote", 10, target="0")
            full_ballot(g)
            e.advance(g, 11, Random(1))
            self.assertEqual(p["status"], "active" if survive else "dead")
            self.assertIsNone(g["raid"])
            self.assertEqual(g["capacity"], 4)

    def test_secrets_and_discord_size_limits(self):
        g = game(12)
        for p in g["players"]:
            p["cards"]["health"]["title"] = "HIDDENSENTINEL" + p["id"]
        for public in (True, False):
            g["public"] = public
            payload = json.dumps([b.to_dict() for b in board(g)] +
                                 [b.to_dict() for b in public_players(g)], ensure_ascii=False)
            self.assertFalse("HIDDENSENTINEL" in payload)
            self.assertTrue("HIDDENSENTINEL0" in hand(g, "0"))
            self.assertLess(sum(len(b) for b in board(g)), 6000)
        g["phase"] = "vote"
        g["votes"] = {"0": "1"}
        self.assertNotIn("→", json.dumps([b.to_dict() for b in board(g)], ensure_ascii=False))
        g["public"] = True
        self.assertIn("→", json.dumps([b.to_dict() for b in board(g)], ensure_ascii=False))
        for p in g["players"]:
            p["name"] = "*" * 40
            p["open"] = list(p["cards"])[:-1]
        g["votes"] = {str(i): str((i+1) % 12) for i in range(12)}
        self.assertLessEqual(sum(len(embed) for embed in board(g)), 6000)
        self.assertTrue(any(field.name == "Голоса" for field in board(g)[0].fields))
        for embed in public_players(g):
            self.assertLessEqual(len(embed), 6000)
            self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))

    def test_store_rolls_back_failed_action_and_reopens(self):
        with test_directory() as directory:
            path = Path(directory)/"game.sqlite3"
            store = Store(path)
            g = game()
            store.create(g)
            copy = store.get(g["id"])
            copy["players"][0]["status"] = "raid"
            with self.assertRaises(e.RuleError):
                e.apply(copy, "0", "vote", 7, target="1")
            self.assertEqual(store.get(g["id"])["players"][0]["status"], "active")
            saved = store.get(g["id"])
            saved["deadline"] = 123456
            store.save(saved)
            with self.assertRaises(e.RuleError):
                store.save(g)
            store.close()
            store = Store(path)
            self.assertEqual(store.get(g["id"])["deadline"], 123456)
            store.close()

    def test_host_permissions_and_pause(self):
        g = game()
        with self.assertRaises(e.RuleError):
            e.apply(g, "1", "advance", 3)
        e.apply(g, "0", "pause", 3)
        remaining = g["remaining"]
        with self.assertRaises(e.RuleError):
            e.apply(g, "1", "reveal", 4, slot="health")
        e.apply(g, "0", "pause", 100)
        self.assertEqual(g["deadline"], 100+remaining)

    def test_final_choice_supplies_and_relevant_hidden_evidence(self):
        g = game(4)
        for p in g["players"]:
            for c in p["cards"].values():
                c["tags"] = []
            p["cards"]["health"]["title"] = "UNUSEDSECRET"
        for p in g["players"][2:]:
            p["status"] = "exiled"
        g["round"] = 3
        g["scenario"]["bunker"].update(power="резервное", foodPortions=200, waterPortions=200)
        g["scenario"]["initialDetail"]["tags"] = ["workshop", "radio"]
        g["scenario"]["roundTwoDetail"]["tags"] = []
        g["players"][0]["cards"]["profession"]["tags"] = ["electrical"]
        g["players"][1]["cards"]["baggage"].update(tags=["power_signal"], title="SECRETTRANSMITTER")
        heating = deepcopy(g)
        e.finish(heating, "heating", 100)
        self.assertEqual(heating["result"]["winners"], ["0", "1"])
        radio = deepcopy(g)
        radio["players"][1]["cards"]["hobby"]["tags"] = ["warmth"]
        e.finish(radio, "radio", 100)
        self.assertEqual(radio["result"]["winners"], ["1"])
        story = " ".join(radio["result"]["story"])
        self.assertIn("SECRETTRANSMITTER", story)
        self.assertNotIn("UNUSEDSECRET", story)
        self.assertNotIn("baggage", radio["players"][1]["open"])
        shortage = deepcopy(g)
        shortage["scenario"]["bunker"]["foodPortions"] = 1
        e.finish(shortage, "heating", 100)
        self.assertEqual(shortage["result"]["winners"], [])

    def test_special_reveal_does_not_stall_last_hidden_card(self):
        g = game()
        for p in g["players"]:
            p["turn_done"] = True
        target = g["players"][1]
        target["turn_done"] = False
        target["open"] = list(target["cards"])[:-2]
        g["players"][0]["special"] = e.load_deck("special_conditions")[16]
        e.apply(g, "0", "special", 10, target="1", slot=e.hidden(target)[0])
        self.assertEqual(len(e.hidden(target)), 1)
        self.assertEqual(g["phase"], "discussion")

    def test_raid_death_can_leave_an_empty_place(self):
        g = game(8, events=True)
        for p in g["players"][5:]:
            p["status"] = "exiled"
        p = g["players"][1]
        for c in p["cards"].values():
            c["tags"] = []
        g.update(round=2, phase="event", event_kind="raid", votes={"0": "1"})
        e.advance(g, 9, Random(1))
        full_ballot(g)
        e.advance(g, 11, Random(1))
        self.assertEqual(g["phase"], "final")
        self.assertEqual(g["capacity"], 4)
        self.assertEqual(len(e.active(g)), 3)

    def test_all_decks_and_generated_scenarios(self):
        for name in ["professions", "health", "hobbies", "phobias", "baggage", "facts", "special_conditions"]:
            cards = e.load_deck(name)
            self.assertEqual(len(cards), len({c["id"] for c in cards}))
            self.assertTrue(all(c["title"] and c["origin"] == "original" for c in cards))
        combinations = set()
        for seed in range(50):
            scenario = e.generate_scenario(Random(seed), "catastrophe_1")
            self.assertNotEqual(scenario["initialDetail"]["id"], scenario["roundTwoDetail"]["id"])
            combinations.add((scenario["bunker"]["id"], scenario["initialDetail"]["id"], scenario["roundTwoDetail"]["id"]))
        self.assertGreater(len(combinations), 12)


class UIConstructionTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, uid="0"):
        return SimpleNamespace(user=SimpleNamespace(id=int(uid), display_name="Player"),
                               channel_id=123,
                               response=SimpleNamespace(defer=AsyncMock(), is_done=Mock(return_value=True),
                                                        send_message=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()))

    async def test_views_and_slash_serialization(self):
        from bunker.bot import BunkerClient, BoardView, SpecialMenu, TimerModal
        with test_directory() as directory:
            client = BunkerClient(Path(directory)/"bot.sqlite3")
            g = game()
            view = BoardView(client, g["id"])
            self.assertTrue(view.is_persistent())
            self.assertEqual(len(view.children), 12)
            self.assertEqual(len(TimerModal(client, g).children), 5)
            for special in e.load_deck("special_conditions"):
                g["players"][0]["special"] = special
                self.assertLessEqual(len(SpecialMenu(client, g, "0").children), 3)
            for command in client.tree.get_commands():
                payload = command.to_dict(client.tree)
                self.assertIn("name", payload)
            await client.close()

    async def test_concurrent_actions_and_stale_menu(self):
        from bunker.bot import BunkerClient
        with test_directory() as directory:
            client = BunkerClient(directory/"bot.sqlite3")
            client.publish = AsyncMock()
            g = e.new_game(123, "0", "Host", size=4)
            client.store.create(g)
            await asyncio.gather(
                client.mutate(self.interaction("1"), g["id"], "join", 0, name="One"),
                client.mutate(self.interaction("2"), g["id"], "join", 0, name="Two"))
            self.assertEqual(len(client.store.get(g["id"])["players"]), 3)
            stale = self.interaction("3")
            await client.mutate(stale, g["id"], "join", 99, name="Three")
            self.assertEqual(len(client.store.get(g["id"])["players"]), 3)
            self.assertIn("Этап уже изменился", stale.followup.send.call_args.args[0])
            await client.close()

    async def test_timer_restart_manual_signal_and_publish_retry(self):
        import discord
        from bunker.bot import BunkerClient
        with test_directory() as directory:
            path = directory/"bot.sqlite3"
            store = Store(path)
            g = game()
            g.update(auto=False, deadline=1)
            store.create(g)
            store.close()
            client = BunkerClient(path)
            client.publish = AsyncMock(side_effect=discord.HTTPException(
                SimpleNamespace(status=503, reason="Service unavailable"), "retry"))
            with self.assertLogs("bunker", level="WARNING"):
                await client.countdowns()
            saved = client.store.get(g["id"])
            self.assertEqual(saved["phase"], "reveal")
            self.assertTrue(saved["timer_elapsed"])
            self.assertIsNone(saved["deadline"])
            self.assertIn(g["id"], client.dirty)
            client.publish = AsyncMock()
            saved.update(auto=True, deadline=1)
            client.store.save(saved)
            await client.countdowns()
            saved = client.store.get(g["id"])
            self.assertEqual(saved["phase"], "discussion")
            self.assertTrue(all(p["turn_done"] for p in saved["players"]))
            client.publish.assert_awaited()
            await client.close()


if __name__ == "__main__":
    unittest.main()
