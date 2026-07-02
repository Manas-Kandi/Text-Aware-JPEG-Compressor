from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any


DEFAULT_LENGTHS = (16, 32, 64, 128)
DEFAULT_SEEDS = (1103, 2207, 3301, 4409, 5519)
PROBE_TYPES = (
    "current_state", "overwritten_value", "historical_fact", "arithmetic",
    "constraint", "multi_hop", "next_action",
)


@dataclass(frozen=True)
class Probe:
    checkpoint: int
    probe_type: str
    prompt: str
    expected: Any
    context: str
    context_hash: str


@dataclass(frozen=True)
class Trajectory:
    trajectory_id: str
    seed: int
    length: int
    events: tuple[str, ...]
    probes: tuple[Probe, ...]


def canonical_context(events: list[str]) -> str:
    return "TASK LOG\n" + "\n".join(f"{index + 1:04d} | {event}" for index, event in enumerate(events))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _probe(kind: str, checkpoint: int, events: list[str], state: dict[str, Any]) -> Probe:
    if kind == "current_state":
        prompt, expected = "What is the current project lead?", state["lead"]
    elif kind == "overwritten_value":
        prompt, expected = "What is the current operating region after all replacements?", state["region"]
    elif kind == "historical_fact":
        prompt, expected = "What was the original access phrase?", state["original_access"]
    elif kind == "arithmetic":
        prompt, expected = "What is the current integer budget?", state["budget"]
    elif kind == "constraint":
        prompt, expected = "Which risk keyword is currently blocked?", state["blocked_risk"]
    elif kind == "multi_hop":
        prompt, expected = "Which city is assigned to the current project lead?", state["lead_cities"][state["lead"]]
    else:
        prompt = "What is the next incomplete milestone in dependency order?"
        expected = next((name for name in state["milestones"] if name not in state["completed"]), "none")
    context = canonical_context(events)
    return Probe(checkpoint, kind, prompt, expected, context, _digest(context))


def build_trajectory(seed: int, length: int) -> Trajectory:
    if length < 4:
        raise ValueError("trajectory length must be at least four")
    rng = random.Random(seed * 1009 + length)
    people = ["Mira", "Soren", "Anika", "Theo", "Priya", "Ivo", "Nadia", "Elian"]
    cities = ["Oslo", "Lima", "Kyoto", "Accra", "Riga", "Tallinn", "Quito", "Busan"]
    risks = ["cobalt", "willow", "ember", "tundra", "harbor", "saffron"]
    milestones = ["spec", "prototype", "review", "release"]
    rng.shuffle(people); rng.shuffle(cities); rng.shuffle(risks)
    state: dict[str, Any] = {
        "lead": people[0], "region": cities[0], "budget": 100 + seed % 31,
        "original_access": f"{risks[0]}-{seed % 97:02d}", "blocked_risk": risks[1],
        "lead_cities": dict(zip(people, cities)), "milestones": milestones, "completed": set(),
    }
    events = [
        f"Initialize project: lead={state['lead']}; region={state['region']}; budget={state['budget']}; access={state['original_access']}.",
        f"Block risk keyword {state['blocked_risk']}.",
        "; ".join(f"Assign {person} to {city}" for person, city in state["lead_cities"].items()) + ".",
        "Milestone dependency order: spec -> prototype -> review -> release.",
    ]
    checkpoints = sorted({max(4, round(length * fraction)) for fraction in (.25, .5, .75, 1.0)})
    probes: list[Probe] = []
    while len(events) < length:
        step = len(events) + 1
        operation = step % 7
        if operation == 0:
            state["lead"] = people[(step // 7) % len(people)]
            event = f"Replace project lead with {state['lead']}."
        elif operation == 1:
            state["region"] = cities[(step // 5) % len(cities)]
            event = f"Replace operating region with {state['region']}."
        elif operation == 2:
            delta = rng.randint(-17, 23)
            state["budget"] += delta
            event = f"Adjust budget by {delta:+d}; canonical budget is now {state['budget']}."
        elif operation == 3:
            state["blocked_risk"] = risks[(step // 3) % len(risks)]
            event = f"Replace blocked risk keyword with {state['blocked_risk']}."
        elif operation == 4 and len(state["completed"]) < len(milestones):
            milestone = milestones[len(state["completed"])]
            state["completed"].add(milestone)
            event = f"Complete milestone {milestone}."
        else:
            person = people[step % len(people)]
            city = cities[(step * 3) % len(cities)]
            state["lead_cities"][person] = city
            event = f"Reassign {person} to {city}; this supersedes that person's prior city."
        events.append(event)
        if len(events) in checkpoints:
            kind = PROBE_TYPES[(seed + len(probes) * 2 + length) % len(PROBE_TYPES)]
            probes.append(_probe(kind, len(events), events.copy(), state))
    # A short trajectory can reach a checkpoint before the generated loop begins.
    for checkpoint in checkpoints:
        if not any(item.checkpoint == checkpoint for item in probes):
            prefix = events[:checkpoint]
            # The supported benchmark lengths all avoid this path; retain a stable smoke-test fallback.
            probes.append(Probe(checkpoint, "historical_fact", "What was the original access phrase?", state["original_access"], canonical_context(prefix), _digest(canonical_context(prefix))))
    trajectory_id = _digest(json.dumps({"seed": seed, "length": length}, sort_keys=True))[:16]
    return Trajectory(trajectory_id, seed, length, tuple(events), tuple(sorted(probes, key=lambda item: item.checkpoint)))
