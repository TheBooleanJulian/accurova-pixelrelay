"""Balanced, deterministic photo distribution.

The rule, in one sentence: take photos in priority order and give each one to
the opted-in person who currently has the fewest photos and still has room,
breaking ties by the participant list order.

That is easy to explain to a group and gives the same answer every time for
the same inputs. Equal photo counts are not equal effort; the owner can
adjust the result before and after publishing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Contributor:
    id: str
    name: str
    order: int
    opted_in: bool
    capacity: int | None  # None = no limit
    current: int = 0  # photos already actively assigned


@dataclass(frozen=True)
class PhotoSlot:
    id: str
    priority: int = 0  # higher first
    order: int = 0


@dataclass
class Plan:
    assignments: list[tuple[str, str]]  # (photo_id, member_id)
    unassigned: list[str]  # photo ids that did not fit anyone's capacity
    load: dict[str, int]  # member_id -> total after the plan

    def to_dict(self) -> dict:
        return {
            "assignments": [{"photo_id": p, "member_id": m} for p, m in self.assignments],
            "unassigned": self.unassigned,
            "load": self.load,
        }


def distribute(photos: list[PhotoSlot], people: list[Contributor]) -> Plan:
    eligible = sorted((c for c in people if c.opted_in), key=lambda c: (c.order, c.id))
    load = {c.id: c.current for c in eligible}
    room = {c.id: (None if c.capacity is None else max(0, c.capacity - c.current)) for c in eligible}
    ordered = sorted(photos, key=lambda p: (-p.priority, p.order, p.id))

    plan: list[tuple[str, str]] = []
    left: list[str] = []
    for photo in ordered:
        candidates = [c for c in eligible if room[c.id] is None or room[c.id] > 0]
        if not candidates:
            left.append(photo.id)
            continue
        pick = min(candidates, key=lambda c: (load[c.id], c.order, c.id))
        plan.append((photo.id, pick.id))
        load[pick.id] += 1
        if room[pick.id] is not None:
            room[pick.id] -= 1
    return Plan(plan, left, load)
