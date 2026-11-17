"""Run an offline example with automatic player choices; no Discord connection."""

import argparse
from pathlib import Path
from random import Random

from bunker import engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=6)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output", type=Path, default=Path("Пример партии.md"))
    args = parser.parse_args()
    rng = Random(args.seed)
    game = engine.new_game(0, "0", "Алексей", size=args.players, events=True)
    names = ["Мария", "Тимофей", "Даша", "Слава", "Лада", "Артём", "Юля", "Илья", "Соня", "Максим"]
    for i in range(1, args.players):
        engine.apply(game, str(i), "join", 0, name=names[i-1])
    engine.apply(game, "0", "advance", 0, rng)
    scenario = game["scenario"]
    bunker = scenario["bunker"]
    lines = ["# Пример партии без подключения к Discord", "",
             "Это автоматическая демонстрация: раскрытия и голоса выбирает скрипт. В игре решения принимают люди.", "",
             f"Игроков: {args.players}. Мест: {game['capacity']}. Катастрофа: {scenario['catastrophe']['title']}.",
             f"Бункер: {bunker['title']}, {bunker['areaM2']} м², {bunker['stayDays']} суток.",
             f"Еда: {bunker['foodPortions']}, вода: {bunker['waterPortions']}, коек: {bunker['beds']}.",
             f"Первая деталь: {scenario['initialDetail']['title']}.", ""]
    last_log = 0
    steps = 0
    while game["phase"] != "finished":
        steps += 1
        if steps > 100:
            raise RuntimeError("Simulation did not finish")
        if game["phase"] == "reveal":
            lines += [f"## Круг {game['round']}", ""]
            if game["round"] == 2:
                lines += ["Открылась деталь: " + scenario["roundTwoDetail"]["title"] + ".", ""]
            for p in engine.active(game):
                if not p["turn_done"]:
                    slot = rng.choice(engine.hidden(p))
                    engine.apply(game, p["id"], "reveal", steps, rng, slot=slot)
                    lines.append(f"- {p['name']}: {engine.LABELS[slot]} — {engine.card_text(p['cards'][slot])}.")
            lines.append("")
        elif game["phase"] == "vote":
            # Agree on one candidate for a compact, reproducible example.
            for p in engine.active(game):
                target = next(uid for uid in game["candidates"] if uid != p["id"])
                engine.apply(game, p["id"], "vote", steps, rng, target=target)
        elif game["phase"] in {"event", "final"}:
            target = engine.active(game)[-1]["id"] if game["phase"] == "event" else "radio"
            for p in engine.active(game):
                engine.apply(game, p["id"], "vote", steps, rng, target=target)
            engine.advance(game, steps, rng)
        else:
            engine.advance(game, steps, rng)
        for entry in game["log"][last_log:]:
            lines.append(entry)
        last_log = len(game["log"])
    lines += ["", "## Финал", "", *game["result"]["story"], "",
              "Скрытых характеристик у финалистов: " + ", ".join(
                  f"{p['name']} — {len(engine.hidden(p))}" for p in engine.active(game)) + "."]
    args.output.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    print("Offline example saved.")


if __name__ == "__main__":
    main()
