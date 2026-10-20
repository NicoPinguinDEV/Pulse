"""K-04: write back only *our* changes to shared JSON data after `await`s.

Flows like "warn → Discord role sync → save" load team_data.json, await Discord several times and then
saved their old snapshot – a warning or note written meanwhile by another flow was lost. `merge_entry`
does a three-way merge (snapshot before / our version / current file): keys we did not touch keep the
current value, and warning lists are merged per warning `id`.
"""
from __future__ import annotations

import copy

_LIST_ID_KEYS = {"warns_list": "id", "notes": "id"}
_MISSING = object()


def _merge_list(before: list, ours: list, current: list, id_key: str) -> list:
    def index(items):
        return {item.get(id_key): item for item in items if isinstance(item, dict) and item.get(id_key)}
    if not all(isinstance(x, dict) and x.get(id_key) for x in list(before) + list(ours)):
        # Items without ids: keep ours and add what others appended meanwhile (e.g. older notes).
        extra = [item for item in current if item not in before and item not in ours]
        return copy.deepcopy(list(ours) + extra)
    b, o = index(before), index(ours)
    result = [copy.deepcopy(item) for item in current
              if not (isinstance(item, dict) and item.get(id_key) in b and item.get(id_key) not in o)]  # we removed it
    positions = {item.get(id_key): pos for pos, item in enumerate(result) if isinstance(item, dict)}
    for key, item in o.items():
        if key in b and b[key] == item:
            continue  # unchanged by us → current version wins
        if key in positions:
            result[positions[key]] = copy.deepcopy(item)
        else:
            positions[key] = len(result)
            result.append(copy.deepcopy(item))
    return result


def merge_entry(before: dict, ours: dict, current: dict) -> dict:
    """Apply the difference between `before` and `ours` onto `current` (all are dicts of one member)."""
    merged = copy.deepcopy(current if isinstance(current, dict) else {})
    for key in set(before) | set(ours):
        old, new = before.get(key, _MISSING), ours.get(key, _MISSING)
        if old == new:
            continue
        if new is _MISSING:
            merged.pop(key, None)
        elif key in _LIST_ID_KEYS and isinstance(new, list):
            base = old if isinstance(old, list) else []
            cur = merged.get(key) if isinstance(merged.get(key), list) else []
            merged[key] = _merge_list(base, new, cur, _LIST_ID_KEYS[key])
        else:
            merged[key] = copy.deepcopy(new)
    return merged


class EntryTracker:
    """Remembers a member entry as loaded; `commit()` merges our changes into the current file."""

    def __init__(self, uid, entry: dict, commit_fn):
        self.uid, self.entry, self._commit_fn = str(uid), entry, commit_fn
        self.before = copy.deepcopy(entry)

    def commit(self) -> dict:
        merged = self._commit_fn(self.uid, self.before, self.entry)
        self.before = copy.deepcopy(self.entry)
        return merged
