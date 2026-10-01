"""Fabricated names for the scrubber: a stable fake per real ledger name (FR-C2).

A fake is drawn from these word lists by hashing the real name's key, so the
same real name gives the same fake on every run over the same ledgers. The
caller re-draws (`attempt` + 1) when a fake is taken or would itself contain a
real or allowlisted name. Every word here is made up for this tool; none comes
from a ledger.
"""

from __future__ import annotations

import hashlib

_FIRST = (
    "Amber", "Aster", "Birch", "Bright", "Cedar", "Clear", "Copper", "Coral", "Dawn", "Elm",
    "Fern", "Garnet", "Granite", "Hazel", "Indigo", "Ivy", "Juniper", "Lark", "Linden", "Maple",
    "Meadow", "Mist", "Opal", "Pebble", "Pine", "Quartz", "Reed", "Russet", "Sable", "Sage",
    "Slate", "Sparrow", "Tamarind", "Teal", "Thistle", "Willow", "Wren", "Yarrow",
)
_SECOND = (
    "brook", "crest", "dale", "field", "ford", "glen", "grove", "haven", "hollow", "hurst",
    "leaf", "mere", "mont", "moor", "point", "shore", "stead", "vale", "view", "well", "wick", "wood",
)
_VENDOR_KIND = (
    "Trading", "Supply", "Services", "Enterprises", "Mart", "Store", "Works", "Depot",
    "Traders", "Outlet", "Center", "Corner", "Shop", "Provisions", "Goods",
)

KINDS = ("organisation", "vendor", "reference")  # priority order when a name plays several roles


def fake_name(kind: str, key: str, attempt: int = 0) -> str:
    """A fabricated name for `key` (the real name's casefolded core form).

    vendor / organisation -> "Pinebrook Trading"; reference (a project or event) -> "Project Pinebrook".
    """
    digest = hashlib.sha256(f"txns-fake-name-v1|{kind}|{attempt}|{key}".encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")
    first = _FIRST[n % len(_FIRST)]
    n //= len(_FIRST)
    second = _SECOND[n % len(_SECOND)]
    n //= len(_SECOND)
    word = first + second
    if kind == "reference":
        return f"Project {word}"
    return f"{word} {_VENDOR_KIND[n % len(_VENDOR_KIND)]}"
