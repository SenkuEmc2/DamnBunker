"""Generate scenarios from the JSON files in this directory."""

import json
from itertools import permutations
from pathlib import Path
from random import Random


def load_deck(name):
    return json.loads(Path(__file__).with_name(name + ".json").read_text(encoding="utf-8"))


def valid_detail_pairs(catastrophe, bunker, details, possible_card_tags):
    for first, second in permutations(bunker["detailPoolIds"], 2):
        available_tags = possible_card_tags | set(details[first]["tags"]) | set(details[second]["tags"])
        if all(
            any(set(requirements) <= available_tags for requirements in challenge["anyTagCombination"])
            for challenge in catastrophe["final"]["challenges"]
        ):
            yield first, second


def generate_scenario(rng=None, catastrophe_id=None):
    rng = rng if rng is not None else Random()
    catastrophes = load_deck("catastrophes")
    catastrophe = rng.choice(catastrophes) if catastrophe_id is None else next(
        row for row in catastrophes if row["id"] == catastrophe_id
    )
    bunkers = {row["id"]: row for row in load_deck("bunkers")}
    details = {row["id"]: row for row in load_deck("bunker_details")}
    card_tags = {
        tag
        for name in ("professions", "health", "hobbies", "phobias", "baggage", "facts")
        for card in load_deck(name)
        for tag in card["tags"]
    }
    compatible = []
    for bunker_id in catastrophe["compatibleBunkerIds"]:
        bunker = bunkers[bunker_id]
        pairs = list(valid_detail_pairs(catastrophe, bunker, details, card_tags))
        if pairs:
            compatible.append((bunker, pairs))
    if not compatible:
        raise ValueError(f"No valid bunker for {catastrophe['id']}")
    bunker, pairs = rng.choice(compatible)
    first, second = rng.choice(pairs)
    return {
        "catastrophe": catastrophe,
        "bunker": {
            **{key: bunker[key] for key in ("id", "title", "areaM2", "stayDays", "ventilation")},
            **{key: rng.randint(*limits) for key, limits in bunker["ranges"].items()},
            **{key: rng.choice(values) for key, values in bunker["systemOptions"].items()},
        },
        "initialDetail": details[first],
        "roundTwoDetail": details[second],
    }
