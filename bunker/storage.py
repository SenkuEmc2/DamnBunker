"""Persist state after each successful action, including countdown deadlines."""

import json
import sqlite3
from pathlib import Path

from .engine import RuleError


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, channel INTEGER, "
            "active INTEGER, revision INTEGER, state TEXT)")
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS active_channel ON games(channel) WHERE active=1")
        self.connection.commit()

    def get(self, game_id):
        row = self.connection.execute("SELECT state FROM games WHERE id=?", (game_id,)).fetchone()
        if row is None:
            raise RuleError("Партия не найдена.")
        return json.loads(row[0])

    def all_active(self):
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT state FROM games WHERE active=1")]

    def for_channel(self, channel):
        row = self.connection.execute(
            "SELECT state FROM games WHERE channel=? AND active=1", (channel,)).fetchone()
        return json.loads(row[0]) if row else None

    def create(self, game):
        try:
            with self.connection:
                self.connection.execute("INSERT INTO games VALUES (?, ?, 1, 0, ?)",
                                        (game["id"], game["channel"], json.dumps(game, ensure_ascii=False)))
        except sqlite3.IntegrityError as error:
            raise RuleError("В этом канале уже есть активная партия.") from error

    def save(self, game):
        previous = game["revision"]
        game["revision"] += 1
        is_active = int(game["phase"] not in {"finished", "cancelled"})
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE games SET active=?, revision=?, state=? WHERE id=? AND revision=?",
                (is_active, game["revision"], json.dumps(game, ensure_ascii=False), game["id"], previous))
            if cursor.rowcount != 1:
                raise RuleError("Состояние уже изменилось. Откройте меню ещё раз.")

    def close(self):
        self.connection.close()
