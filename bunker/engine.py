"""Pure game rules. Callers commit a modified copy only after a successful action."""

from collections import Counter
from copy import deepcopy
from random import Random
from uuid import uuid4

from cards.scenario_generator import generate_scenario, load_deck


LABELS = {
    "profession": "Профессия", "biology": "Биология", "health": "Здоровье",
    "hobby": "Хобби", "phobia": "Фобия", "baggage": "Багаж",
    "fact1": "Факт 1", "fact2": "Факт 2",
}
PHASES = {
    "lobby": "Сбор игроков", "reveal": "Раскрытие карт", "discussion": "Обсуждение",
    "event": "Выбор участника события", "vote": "Голосование",
    "revise": "Можно один раз сменить голос", "defense": "Защита при ничьей",
    "final": "Последнее решение", "finished": "Итог", "cancelled": "Партия остановлена",
}
TIMER_PHASE = {"reveal": "reveal", "discussion": "discussion", "event": "final",
               "vote": "vote", "revise": "revise", "defense": "discussion", "final": "final"}


class RuleError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RuleError(message)


def new_game(channel, host, name, size=6, public=True, choice=True, events=False, auto=False):
    require(4 <= size <= 12, "В лобби должно быть от 4 до 12 игроков.")
    tier = 0 if size <= 6 else 1 if size <= 9 else 2
    return {
        "id": uuid4().hex[:12], "channel": channel, "host": str(host), "size": size,
        "capacity": size // 2, "public": public, "choice": choice, "events": events,
        "auto": auto, "players": [{"id": str(host), "name": name[:40], "status": "active"}],
        "phase": "lobby", "epoch": 0, "revision": 0, "round": 0,
        "timers": dict(reveal=45 * size, discussion=[120, 180, 240][tier],
                       vote=[45, 60, 75][tier], revise=[30, 30, 45][tier], final=90),
        "deadline": None, "paused": False, "remaining": None,
        "log": [], "votes": {}, "changed": [], "runoff": False, "candidates": [],
        "event_done": False, "raid": None, "message": None,
    }


def player(game, uid):
    result = next((p for p in game["players"] if p["id"] == str(uid)), None)
    require(result is not None, "Вы не участвуете в этой партии.")
    return result


def active(game):
    return [p for p in game["players"] if p["status"] == "active"]


def contenders(game):
    return [p for p in game["players"] if p["status"] in {"active", "raid"}]


def log(game, text):
    game["log"].append(text)


def enter(game, phase, now):
    game["phase"] = phase
    game["epoch"] += 1
    game["timer_elapsed"] = False
    seconds = game["timers"].get(TIMER_PHASE.get(phase), 0)
    game["deadline"] = now + seconds if seconds else None


def card_text(card):
    text = card["title"]
    if card.get("stage"):
        text += " (" + card["stage"] + ")"
    if "experience" in card:
        text += f" · стаж {card['experience']} лет"
    return text


def hidden(p):
    return [slot for slot in p["cards"] if slot not in p["open"]]


def draw(game, deck, count, rng):
    used = set(game["drawn"].setdefault(deck, []))
    available = [c for c in load_deck(deck) if c["id"] not in used]
    require(len(available) >= count, "В этой колоде закончились новые карты.")
    cards = deepcopy(rng.sample(available, count))
    game["drawn"][deck].extend(c["id"] for c in cards)
    return cards


def start(game, now, rng):
    require(len(game["players"]) == game["size"], "Дождитесь заполнения лобби.")
    game["scenario"] = generate_scenario(rng, "catastrophe_1")
    game["drawn"] = {}
    professions = draw(game, "professions", game["size"], rng)
    special_deck = [c for c in load_deck("special_conditions")
                    if game["size"] >= 6 or c.get("slot") != "phobia"]
    specials = deepcopy(rng.sample(special_deck, game["size"]))
    for p, profession, special in zip(game["players"], professions, specials):
        age = rng.randint(max(18, profession["minWorkAge"]), 75)
        experience = profession.get("experienceYears", {"min": 0, "max": 45})
        profession["experience"] = rng.randint(experience["min"], min(age-profession["minWorkAge"], experience["max"]))
        p["cards"] = {"profession": profession, "biology": {
            "id": "biology_" + p["id"], "title": f"{rng.choice(['Мужчина', 'Женщина'])}, {age} лет",
            "tags": [], "age": age,
        }}
        for slot, deck in [("health", "health"), ("baggage", "baggage"),
                           ("hobby", "hobbies"), ("fact1", "facts")]:
            p["cards"][slot] = draw(game, deck, 1, rng)[0]
        if game["size"] >= 6:
            p["cards"]["phobia"] = draw(game, "phobias", 1, rng)[0]
        if game["size"] >= 9:
            p["cards"]["fact2"] = draw(game, "facts", 1, rng)[0]
        p.update(open=["profession"], special=special, special_used=False, turn_done=False)
    game["round"] = 1
    log(game, "Профессии раскрыты. Каждый выбирает ещё одну характеристику.")
    enter(game, "reveal", now)


def reveal(game, p, slot, now):
    require(game["phase"] == "reveal" and not p["turn_done"], "Ваше раскрытие в этом круге завершено.")
    require(slot in hidden(p) and len(hidden(p)) > 1, "Должна остаться хотя бы одна закрытая характеристика.")
    p["open"].append(slot)
    p["turn_done"] = True
    log(game, f"{p['name']} раскрывает «{LABELS[slot]}».")
    if all(q["turn_done"] for q in active(game)):
        enter(game, "discussion", now)


def begin_vote(game, now, runoff=False):
    game.update(votes={}, changed=[], runoff=runoff)
    if not runoff:
        game["candidates"] = [p["id"] for p in active(game)]
    enter(game, "vote", now)


def vote(game, p, target, now):
    phase = game["phase"]
    require(phase in {"vote", "revise", "event", "final"}, "Сейчас голосование закрыто.")
    uid = p["id"]
    if phase in {"vote", "revise"}:
        require(target in game["candidates"] and target != uid, "Выберите другого допустимого кандидата.")
    elif phase == "event":
        require(target in [q["id"] for q in active(game)], "Этот игрок не может участвовать в событии.")
    else:
        require(target in {"heating", "radio"}, "Выберите один из вариантов финала.")
    if phase == "revise":
        require(uid not in game["changed"], "Вы уже использовали единственную смену голоса.")
        require(game["votes"].get(uid) != target, "Этот голос уже учтён.")
        game["changed"].append(uid)
    else:
        require(uid not in game["votes"], "Голос принят. Сменить его можно в следующем окне.")
    game["votes"][uid] = target
    if phase == "vote" and len(game["votes"]) == len(active(game)):
        enter(game, "revise", now)


def event_result(game, now, rng):
    counts = Counter(game["votes"].values())
    candidates = [p["id"] for p in active(game)]
    top = max(counts.values(), default=0)
    target = rng.choice([uid for uid in candidates if counts[uid] == top])
    p = player(game, target)
    game["event_done"] = True
    if game["event_kind"] == "raid":
        p["status"] = "raid"
        game["raid"] = {"player": target, "return_round": game["round"] + 1}
        log(game, f"{p['name']} отправляется в рейд до следующего круга.")
    else:
        # Hidden characteristics also influence the event without being published.
        tags = {t for c in p["cards"].values() for t in c["tags"]}
        if tags & {"social", "care", "communications"}:
            p["cards"]["baggage"] = draw(game, "baggage", 1, rng)[0]
            log(game, f"Незнакомец отдал {p['name']} новый багаж.")
        else:
            p["cards"]["health"] = {"id": "event_bruise", "title": "Ушиб плеча после встречи",
                                      "tags": [], "stage": None}
            log(game, f"{p['name']} вернулся со встречи с ушибом плеча.")
    begin_vote(game, now)


def return_raid(game):
    raid = game["raid"]
    if not raid:
        return
    p = player(game, raid["player"])
    tags = {t for card in p["cards"].values() for t in card["tags"]}
    # The preview explicitly states these conditions before the group chooses.
    survived = bool(tags & {"navigation", "winter_experience"}) and bool(
        tags & {"warmth", "repair", "power_signal"})
    p["status"] = "active" if survived else "dead"
    if survived:
        game["scenario"]["bunker"]["foodPortions"] += 12
        log(game, f"{p['name']} вернулся из рейда с 12 порциями еды.")
    else:
        log(game, f"{p['name']} погиб в рейде. Вместимость бункера прежняя.")
    game["raid"] = None


def close_bunker(game, now):
    log(game, "Дверь закрыта. Состав бункера зафиксирован.")
    game["votes"] = {}
    enter(game, "final", now)
    if not game["choice"]:
        finish(game, "heating", now)


def next_round(game, now):
    game["round"] += 1
    return_raid(game)
    if len(contenders(game)) <= game["capacity"]:
        close_bunker(game, now)
        return
    for p in active(game):
        p["turn_done"] = len(hidden(p)) <= 1
    if game["round"] == 2:
        log(game, "Открыта новая деталь бункера.")
    enter(game, "reveal", now)
    if all(p["turn_done"] for p in active(game)):
        enter(game, "discussion", now)


def tally(game, now, rng):
    counts = Counter(game["votes"].values())
    top = max(counts.values(), default=0)
    leaders = [uid for uid in game["candidates"] if counts[uid] == top]
    game["last_tally"] = dict(counts)
    log(game, "Итоги голосования — " + ", ".join(
        f"{player(game, uid)['name']}: {counts[uid]}" for uid in game["candidates"]))
    if len(leaders) > 1 and not game["runoff"]:
        game["candidates"] = leaders
        log(game, "Ничья. Кандидаты защищаются перед повторным голосованием.")
        enter(game, "defense", now)
        return
    eliminated = player(game, rng.choice(leaders))
    eliminated["status"] = "exiled"
    log(game, f"Изгнан игрок {eliminated['name']}.")
    next_round(game, now)


def finish(game, choice, now):
    """Cold scenario. Evidence is collected first; only used cards enter the story."""
    bunker = game["scenario"]["bunker"]
    details = [game["scenario"]["initialDetail"]]
    if game["round"] >= 2:
        details.append(game["scenario"]["roundTwoDetail"])
    evidence = {}
    for detail in details:
        for tag in detail["tags"]:
            evidence.setdefault(tag, detail["title"])
    for p in active(game):
        for card in p["cards"].values():
            for tag in card["tags"]:
                evidence.setdefault(tag, f"{p['name']} — {card_text(card)}")
    def combination(options):
        return next((option for option in options if set(option) <= evidence.keys()), None)

    rules = game["scenario"]["catastrophe"]["final"]["rules"]
    heat_rules = rules["heat"]
    # Passive insulation works in either branch; powered repairs compete with the radio.
    heat = combination(heat_rules["passiveRequiresAny"])
    if heat is None and (choice == "heating" or bunker["power"] == "штатное"):
        heat = combination(heat_rules["poweredRequiresAny"])
    radio = combination(rules["rescue"]["requiresAny"])
    rescue = choice == "radio" and radio is not None
    days = rules["rescue"]["earlyRescueDays"] if rescue else bunker["stayDays"]
    story = [f"После закрытия двери группа выбирает {'радиосвязь' if choice == 'radio' else 'отопление'}.",
             f"Снаружи усиливается мороз. Впереди {days} суток до {'эвакуации' if rescue else 'окончания непогоды'}."]
    if heat:
        story.append("Убежище удалось согреть. Помогли " + "; ".join(evidence[t] for t in heat) + ".")
    else:
        story.append("У группы не оказалось сочетания навыков и оборудования для обогрева.")
    if rescue:
        story.append("Связь с эвакуацией обеспечили " + "; ".join(evidence[t] for t in radio) + ".")
    elif choice == "radio":
        story.append("Выйти на связь не удалось. Пришлось ждать окончания непогоды.")
    # Supplies determine how long the entire group can wait; no arbitrary ranking by health.
    food_needed = days * len(active(game)) * rules["rations"]["portionsPerPersonPerDay"]
    water_needed = days * len(active(game)) * rules["rations"]["waterPortionsPerPersonPerDay"]
    enough = bunker["foodPortions"] >= food_needed and bunker["waterPortions"] >= water_needed
    story.append(f"На ожидание нужно {food_needed} порций еды и {water_needed} порций воды. "
                 f"Есть {bunker['foodPortions']} и {bunker['waterPortions']}.")
    winners = []
    for p in active(game):
        protection = next((c for c in p["cards"].values()
                           if set(c["tags"]) & {"warmth", "winter_experience"}), None)
        survives_cold = heat is not None or (rescue and protection is not None)
        if survives_cold and enough:
            winners.append(p["id"])
            explanation = "переживает катастрофу"
            if heat is None and protection:
                explanation += " благодаря карте «" + card_text(protection) + "»"
        else:
            explanation = "погибает от холода" if not survives_cold else "погибает из-за нехватки запасов"
        story.append(f"{p['name']} — {explanation}.")
    game["result"] = {"choice": choice, "winners": winners, "story": story}
    enter(game, "finished", now)


def special(game, p, target_id=None, slot=None, rng=None):
    require(game["phase"] not in {"lobby", "final", "finished", "cancelled"},
            "На этом этапе спецкарты закрыты.")
    require(not p["special_used"], "Спецкарта уже использована.")
    effect = p["special"]
    op = effect["operation"]
    message = "Спецкарта применена."
    if op in {"swap", "peek", "reveal_other"}:
        target = player(game, target_id)
        require(target["status"] == "active" and target != p, "Выберите другого активного игрока.")
    if op in {"replace", "replace_all", "swap"}:
        key = effect["slot"]
        recipients = active(game) if op == "replace_all" else [p]
        require(all(key in q["cards"] for q in recipients), "Этой категории нет в раздаче.")
        if op == "swap":
            require(key in target["cards"], "У цели нет этой категории.")
            p["cards"][key], target["cards"][key] = target["cards"][key], p["cards"][key]
        else:
            for q, card in zip(recipients, draw(game, effect["deck"], len(recipients), rng)):
                q["cards"][key] = card
    elif op in {"peek", "reveal_other"}:
        require(slot in hidden(target), "Выберите скрытую характеристику.")
        if op == "reveal_other":
            require(len(hidden(target)) > 1, "У цели должна остаться скрытая характеристика.")
            target["open"].append(slot)
        else:
            message = f"{target['name']} — {LABELS[slot]}: {card_text(target['cards'][slot])}"
    elif op == "inspect_bunker":
        require(game["round"] < 2, "Эта деталь уже открыта.")
        detail = game["scenario"]["roundTwoDetail"]
        message = f"{detail['title']}. {detail['description']} {detail['possibleUse']}"
    else:
        raise RuleError("Неизвестный эффект спецкарты.")
    p["special_used"] = True
    log(game, f"{p['name']} использует «{effect['title']}».")
    return message


def advance(game, now, rng):
    phase = game["phase"]
    if phase == "lobby":
        start(game, now, rng)
    elif phase == "reveal":
        for p in active(game):
            if not p["turn_done"]:
                if len(hidden(p)) > 1:
                    reveal(game, p, rng.choice(hidden(p)), now)
                else:
                    p["turn_done"] = True
        enter(game, "discussion", now)
    elif phase == "discussion":
        if game["round"] == 2 and game["events"] and not game["event_done"]:
            kinds = ["stranger"]
            if game["size"] >= 8 and len(active(game)) > game["capacity"]:
                kinds.append("raid")
            game["event_kind"] = rng.choice(kinds)
            game["votes"] = {}
            enter(game, "event", now)
        else:
            begin_vote(game, now)
    elif phase == "event":
        event_result(game, now, rng)
    elif phase == "vote":
        enter(game, "revise", now)
    elif phase == "revise":
        tally(game, now, rng)
    elif phase == "defense":
        begin_vote(game, now, runoff=True)
    elif phase == "final":
        votes = Counter(game["votes"].values())
        choice = "radio" if votes["radio"] > votes["heating"] else "heating"
        finish(game, choice, now)
    else:
        raise RuleError("Партия уже завершена.")


def apply(game, uid, action, now, rng=None, **args):
    """All public mutations pass through authorization and phase checks here."""
    rng = rng if rng is not None else Random()
    uid = str(uid)
    require(game["phase"] not in {"finished", "cancelled"}, "Партия завершена.")
    if action == "join":
        require(game["phase"] == "lobby", "Партия уже началась.")
        require(len(game["players"]) < game["size"], "Лобби заполнено.")
        require(uid not in [p["id"] for p in game["players"]], "Вы уже в лобби.")
        game["players"].append({"id": uid, "name": args["name"][:40], "status": "active"})
        return "Вы присоединились."
    if action in {"advance", "pause", "timers", "transfer", "cancel"}:
        require(uid == game["host"], "Эта кнопка доступна ведущему.")
        if action == "pause":
            if game["paused"]:
                game["deadline"] = now + game["remaining"] if game["remaining"] is not None else None
            else:
                game["remaining"] = max(0, game["deadline"]-now) if game["deadline"] is not None else None
                game["deadline"] = None
            game["paused"] = not game["paused"]
            return "Пауза переключена."
        if action == "cancel":
            enter(game, "cancelled", now)
            return "Партия остановлена."
        if action == "transfer":
            require(game["phase"] == "lobby", "Ведущего можно сменить в лобби.")
            game["host"] = player(game, args["target"])["id"]
            return "Роль ведущего передана."
        if action == "timers":
            require(game["phase"] == "lobby", "Настройте таймеры до старта.")
            values = args["values"]
            require(set(values) == set(game["timers"]) and all(type(v) is int and 0 <= v <= 3600 for v in values.values()),
                    "Время каждого этапа — от 0 до 3600 секунд. 0 выключает таймер.")
            game["timers"] = values
            return "Таймеры сохранены."
        require(not game["paused"], "Сначала снимите паузу.")
        advance(game, now, rng)
        return "Этап завершён."
    require(not game["paused"], "Партия на паузе.")
    p = player(game, uid)
    if action == "leave":
        require(game["phase"] == "lobby" and uid != game["host"], "Выйти можно в лобби. Ведущий сначала передаёт роль.")
        game["players"].remove(p)
        return "Вы вышли из лобби."
    require(p["status"] == "active", "Вы сейчас наблюдаете за партией.")
    if action == "reveal":
        reveal(game, p, args["slot"], now)
    elif action == "vote":
        vote(game, p, args["target"], now)
        target = args["target"]
        title = {"heating": "отопление", "radio": "радиосвязь"}.get(target)
        return "Ваш голос: " + (title or player(game, target)["name"]) + "."
    elif action == "special":
        result = special(game, p, args.get("target"), args.get("slot"), rng)
        if game["phase"] == "reveal":
            for q in active(game):
                if len(hidden(q)) <= 1:
                    q["turn_done"] = True
            if all(q["turn_done"] for q in active(game)):
                enter(game, "discussion", now)
        return result
    else:
        raise RuleError("Неизвестное действие.")
    return "Принято."
