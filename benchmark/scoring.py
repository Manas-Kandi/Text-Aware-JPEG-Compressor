from __future__ import annotations

import json
import re
from typing import Any


def normalize(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return re.sub(r"\s+", " ", str(value).strip().lower())


def parse_answer(answer: str) -> tuple[Any, str]:
    text = answer.strip()
    try:
        parsed = json.loads(text)
        return (parsed.get("answer") if isinstance(parsed, dict) and "answer" in parsed else parsed), ""
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return (parsed.get("answer") if isinstance(parsed, dict) else parsed), ""
            except json.JSONDecodeError:
                pass
    return text, "invalid_json"


def score_answer(answer: str, expected: Any) -> dict[str, Any]:
    parsed, parse_error = parse_answer(answer)
    if isinstance(expected, list):
        actual = parsed if isinstance(parsed, list) else [item.strip() for item in str(parsed).split(",")]
        fields_total = len(expected)
        fields_correct = sum(normalize(left) == normalize(right) for left, right in zip(actual, expected))
        correct = len(actual) == fields_total and fields_correct == fields_total
    else:
        fields_total = 1
        fields_correct = int(normalize(parsed) == normalize(expected))
        correct = fields_correct == 1
    return {"correct": correct, "fields_correct": fields_correct, "fields_total": fields_total, "parse_error": parse_error}
