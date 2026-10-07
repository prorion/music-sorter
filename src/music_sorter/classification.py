from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files

TAXONOMY = json.loads(files("music_sorter").joinpath("resources/taxonomy.json").read_text("utf-8"))
AXES = ("major", "subgenre", "vocal", "mood", "concept")
LABELS = dict(zip(AXES, ("대분류", "세부 장르", "보컬/연주", "분위기", "컨셉")))


def empty_classification() -> dict:
    return {axis: {"value": None, "status": "unclassified", "protected": False,
                   "source": None, "confidence": None, "reason": ""} for axis in AXES}


def validate(classification: dict, taxonomy=None) -> None:
    taxonomy = TAXONOMY if taxonomy is None else taxonomy
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
        allowed = taxonomy["major"].get(major, []) if axis == "subgenre" else taxonomy[axis]
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


def manual_patch(original: dict, operations: dict) -> dict:
    if not operations or any(axis not in AXES for axis in operations):
        raise ValueError("수정할 분류 항목을 선택하세요.")
    updated = deepcopy(original)
    for axis, operation in operations.items():
        mode, value = operation["mode"], operation.get("value")
        if mode == "unknown":
            value = None
        elif axis in {"major", "vocal"}:
            if mode != "set":
                raise ValueError("한 값 항목은 지정 또는 미확정으로 수정하세요.")
        elif mode == "none" and axis == "concept":
            value = []
        elif mode in {"replace", "add", "remove"}:
            if not isinstance(value, list) or not value or len(set(value)) != len(value):
                raise ValueError("수정할 태그를 하나 이상 선택하세요.")
            if mode in {"add", "remove"}:
                previous = original[axis]["value"]
                if previous is None:
                    raise ValueError("미확정 목록은 먼저 목록 교체로 확정하세요.")
                value = list(dict.fromkeys(previous + value)) if mode == "add" else [v for v in previous if v not in value]
        else:
            raise ValueError("수정 방식을 확인하세요.")
        updated[axis] = dict(value=value, status="unresolved" if value is None else "confirmed",
                             protected=True, source="manual", confidence=None, reason="사용자 검토")
    validate(updated)
    return updated
