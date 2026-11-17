"""Build public and private Discord messages without exposing undealt information."""

import discord
from .engine import LABELS, PHASES, active, card_text, hidden, player


def safe(text):
    return discord.utils.escape_markdown(str(text)).replace("@", "＠")


def hand(game, uid):
    p = player(game, uid)
    if "cards" not in p:
        return "Карты появятся после старта партии."
    lines = [f"**{safe(LABELS[slot])}** — {safe(card_text(card))} "
             + ("👁" if slot in p["open"] else "🔒") for slot, card in p["cards"].items()]
    lines += ["", "**Особые условия** — " + safe(p["special"]["title"]),
              safe(p["special"]["description"]),
              "Использована" if p["special_used"] else "Можно использовать один раз",
              f"Скрытых характеристик — {len(hidden(p))}"]
    if game["phase"] in {"vote", "revise", "event", "final"} and p["id"] in game["votes"]:
        target = game["votes"][p["id"]]
        title = {"heating": "отопление", "radio": "радиосвязь"}.get(target)
        lines.append("Ваш голос — " + safe(title or player(game, target)["name"]))
    return "\n".join(lines)


def public_players(game):
    embeds = []
    for offset in range(0, len(game["players"]), 6):
        embed = discord.Embed(title="Открытые карты игроков", color=0xD5A852)
        for p in game["players"][offset:offset+6]:
            lines = [f"{LABELS[slot]} — {card_text(p['cards'][slot])}" for slot in p.get("open", [])]
            embed.add_field(name=safe(p["name"]), value=safe("\n".join(lines) or "Карты ещё закрыты"), inline=False)
        embeds.append(embed)
    return embeds


def board(game):
    phase = game["phase"]
    embed = discord.Embed(title=f"Бункер · {PHASES[phase]}", color=0xD5A852)
    host = player(game, game["host"])
    text = [f"Ведущий — {safe(host['name'])} · игроков {len(game['players'])}/{game['size']}",
            f"Мест — {game['capacity']} · круг {game['round']}",
            f"Голоса — {'открытые' if game['public'] else 'тайные'} · "
            f"финал — {'с решением' if game['choice'] else 'автоматический'}",
            f"Особое событие — {'вкл.' if game['events'] else 'выкл.'} · "
            f"переходы — {'автоматические' if game['auto'] else 'ведущий'}"]
    if game["paused"]:
        text.append("⏸ Партия на паузе")
    elif game.get("deadline"):
        text.append(f"Таймер этапа — <t:{int(game['deadline'])}:R>")
    elif game.get("timer_elapsed"):
        text.append("Время вышло. Ведущий выбирает следующий этап.")
    if "scenario" in game:
        scenario = game["scenario"]
        bunker = scenario["bunker"]
        text += ["", "**" + safe(scenario["catastrophe"]["title"]) + "**",
                 safe(scenario["catastrophe"]["outsideRisk"]),
                 f"{safe(bunker['title'])} · {bunker['areaM2']} м² · {bunker['stayDays']} дней",
                 f"Еда {bunker['foodPortions']} · вода {bunker['waterPortions']} дневных порций · коек {bunker['beds']}",
                 f"Отопление — {safe(bunker['heating'])}, питание — {safe(bunker['power'])}"]
        details = [scenario["initialDetail"]]
        if game["round"] >= 2:
            details.append(scenario["roundTwoDetail"])
        for detail in details:
            text.append("**" + safe(detail["title"]) + "** — " + safe(detail["description"]))
            text.append(safe(detail["possibleUse"]))
    if phase == "event":
        if game["event_kind"] == "raid":
            text.append("\n**Рейд за едой.** Нужен опыт маршрутов или зимних походов и снаряжение "
                        "для тепла, ремонта или связи. На круг участник покинет голосование. "
                        "При успехе принесёт 12 порций еды, при провале погибнет.")
        else:
            text.append("\n**Незнакомец у входа.** Он предлагает обмен. "
                        "Социальный опыт, забота о людях или связь помогут получить багаж. "
                        "Иначе участник вернётся с ушибом. Выберите представителя.")
    if phase == "final":
        text.append("\n**Последнее решение.** При резервном питании ремонт отопления требует "
                    "направить энергию на обогрев. Теплоизоляция работает при любом выборе. "
                    "Радио позволяет вызвать эвакуацию за сутки, если есть оборудование и подходящая карта. "
                    "Понадобится тепло или личное снаряжение. "
                    "При ничьей выбирается отопление.")
    embed.description = "\n".join(text)
    statuses = {"active": "в игре", "raid": "в рейде", "dead": "погиб", "exiled": "изгнан"}
    for p in game["players"]:
        cards = [f"{LABELS[slot]} — {card_text(p['cards'][slot])}" for slot in p.get("open", [])]
        content = "\n".join(cards) or "Ожидает раздачи"
        if len(content) > 270:
            content = content[:235] + "…\nПолностью — «Карты игроков»"
        embed.add_field(name=f"{safe(p['name'])} · {statuses[p['status']]}",
                        value=safe(content), inline=True)
    if phase in {"vote", "revise", "event", "final"}:
        votes = game["votes"]
        content = f"Принято {len(votes)} из {len(active(game))}."
        if game["public"] and phase in {"vote", "revise", "event"}:
            content += "\n" + "\n".join(
                f"{safe(player(game, uid)['name'])} → {safe(player(game, target)['name'])}"
                for uid, target in votes.items())
        if phase == "revise":
            content += "\nКаждый может изменить голос один раз."
        embed.add_field(name="Голоса", value=content, inline=False)
    if game["log"]:
        embed.add_field(name="Последние события", value=safe("\n".join(game["log"][-3:]))[:700], inline=False)
    embeds = [embed]
    if "result" in game:
        embeds.append(discord.Embed(title="История выживших", description=safe("\n\n".join(
            game["result"]["story"])), color=0x65A487))
    # Preserve votes and player names when the Discord message budget gets tight.
    compact = "Полные карты — кнопка «Карты игроков»"
    while sum(len(e) for e in embeds) > 5900:
        candidates = [(i, field) for i, field in enumerate(embed.fields[:len(game["players"])])
                      if len(field.value) > len(compact)]
        if not candidates:
            break
        i, field = max(candidates, key=lambda item: len(item[1].value))
        embed.set_field_at(i, name=field.name, value=compact, inline=True)
    return embeds
