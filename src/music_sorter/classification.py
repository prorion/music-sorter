from __future__ import annotations

import json
from importlib.resources import files

TAXONOMY = json.loads(files("music_sorter").joinpath("resources/taxonomy.json").read_text("utf-8"))
AXES = ("major", "subgenre", "vocal", "mood", "concept")
LABELS = dict(zip(AXES, ("대분류", "세부 장르", "보컬/연주", "분위기", "컨셉")))


def empty_classification() -> dict:
    return {axis: {"value": None, "status": "unclassified", "protected": False,
                   "source": None, "confidence": None, "reason": ""} for axis in AXES}


def validate(classification: dict) -> None:
    major = classification["major"]["value"]
    for axis in AXES:
        field = classification[axis]
        if field["status"] not in {"confirmed", "unresolved", "unclassified"}:
            raise ValueError("판정 상태가 올바르지 않습니다.")
        value = field["value"]
        if field["status"] != "confirmed":
            if value is not None:
                raise ValueError("미확정 항목의 값은 비워야 합니다.")
            continue
        allowed = TAXONOMY["major"].get(major, []) if axis == "subgenre" else TAXONOMY[axis]
        if axis in {"major", "vocal"}:
            if value not in allowed:
                raise ValueError(f"{LABELS[axis]} 허용 목록을 확인하세요.")
        else:
            if not isinstance(value, list) or len(set(value)) != len(value):
                raise ValueError(f"{LABELS[axis]} 목록이 올바르지 않습니다.")
            if any(item not in allowed for item in value):
                raise ValueError(f"{LABELS[axis]} 허용 목록을 확인하세요.")
            if axis != "concept" and not 1 <= len(value) <= 2:
                raise ValueError(f"{LABELS[axis]}는 1~2개를 선택하세요.")


def review_state(classification: dict) -> str:
    statuses = [classification[axis]["status"] for axis in AXES]
    if all(s == "unclassified" for s in statuses):
        return "unclassified"
    return "confirmed" if all(s == "confirmed" for s in statuses) else "unresolved"
