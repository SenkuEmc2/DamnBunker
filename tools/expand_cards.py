"""Idempotently add the original cards authored for the first Discord playtest."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "cards"

ADDITIONS = {
    "professions": [
        ("Мастер котельной", ["repair", "insulation"], 18),
        ("Инженер-энергетик", ["engineering", "electrical"], 22),
        ("Техник радиосвязи", ["communications", "radio_operator"], 18),
        ("Сотрудник полярной станции", ["winter_experience", "navigation"], 21),
        ("Механик автономных генераторов", ["repair", "electrical"], 18),
        ("Монтажник теплоизоляции", ["repair", "insulation"], 18),
        ("Инструктор зимнего туризма", ["winter_experience", "navigation"], 18),
        ("Кладовщик аварийного запаса", ["logistics", "food"], 18),
    ],
    "health": [
        ("Близорукость, очки с собой", [], "слабая степень"),
        ("Восстановление после растяжения голеностопа", [], "завершающий этап"),
        ("Аллергия на пыльцу", [], "вне сезонного обострения"),
        ("Повышенная чувствительность к холоду", [], None),
        ("Недавний лёгкий ожог ладони", [], "заживление"),
        ("Сниженный слух на одно ухо", [], None),
        ("Мышечная усталость после долгого перехода", [], None),
        ("Старая травма колена", [], "без обострения"),
    ],
    "hobbies": [
        ("Зимний бивак без палатки", ["winter_experience", "navigation"]),
        ("Самодельные походные печи", ["repair", "insulation"]),
        ("Охота за дальними радиостанциями", ["communications", "radio_operator"]),
        ("Ремонт списанной электроники", ["repair", "electrical"]),
        ("Шитьё утеплённых спальников", ["insulation"]),
        ("Походы по замёрзшим озёрам", ["winter_experience", "navigation"]),
        ("Настольные игры на выживание", ["social"]),
        ("Домашняя сушка продуктов", ["food"]),
    ],
    "phobias": [
        ("Страх треска льда", []), ("Страх переговоров по рации", []),
        ("Страх неисправных лифтов", []), ("Страх пустых коридоров", []),
        ("Страх грохота вентиляции", []), ("Страх манекенов", []),
        ("Страх внезапного стука в дверь", []), ("Страх потерять ориентир в метели", []),
    ],
    "baggage": [
        ("Зимний спальник с ремонтным набором", ["warmth", "repair"]),
        ("Ручной генератор и переходники", ["power_signal"]),
        ("Портативный коротковолновый передатчик", ["radio"]),
        ("Рулон утеплителя и армированный скотч", ["insulation"]),
        ("Ящик инструментов для ремонта отопления", ["repair"]),
        ("Герметичный мешок тёплых вещей", ["warmth"]),
        ("Фильтр для воды и складные канистры", ["water_food"]),
        ("Огромный плюшевый гусь", []),
    ],
    "facts": [
        ("Провёл зиму на удалённой метеостанции", ["winter_experience"]),
        ("Помогал утеплять старое убежище", ["insulation", "repair"]),
        ("Умеет передавать сообщения азбукой Морзе", ["radio_operator"]),
        ("Однажды восстановил питание целого подъезда", ["electrical"]),
        ("Вёл учёт запасов в походе на двадцать человек", ["logistics"]),
        ("Помнит схему технических тоннелей района", ["navigation"]),
        ("Умеет мирить людей после тяжёлой ссоры", ["social", "care"]),
        ("Уверен, что плюшевый гусь приносит удачу", []),
    ],
}


def main():
    for name, rows in ADDITIONS.items():
        path = ROOT / (name + ".json")
        cards = json.loads(path.read_text(encoding="utf-8"))
        existing = {card["id"] for card in cards}
        for i, row in enumerate(rows, 1):
            card_id = f"{name}_discord_{i:03d}"
            if card_id in existing:
                continue
            card = {"id": card_id, "title": row[0], "tags": row[1], "origin": "original"}
            if name == "professions":
                card.update(minWorkAge=row[2], experienceYears={"min": 0, "max": 45})
            elif name == "health":
                card["stage"] = row[2]
            cards.append(card)
        path.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: {len(cards)}")


if __name__ == "__main__":
    main()
