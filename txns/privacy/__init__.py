"""Keeping real vendor and person names on this machine (FR-C2, FR-D2 gate 3, ADR 0007).

    allow = load_allowlist(cwd)                  # inputs/brands-allowlist.txt; missing = empty (.found False)
    index = NameIndex(derived.ledgers, allow)    # ledger names -> allowlist spelling or stable fake
    payload = build_payload(derived, index)      # aggregates + item-text patterns, scrubbed
    find_in_object(payload, index)               # [] or locations of surviving names (block the call)
    find_in_files(paths, index, root=...)        # same check over files (bundle promotion gates)
    index.private_map()                          # real -> fake: LOCAL ONLY, never in payload/bundle/git
"""

from txns.privacy.leaks import find_in_files, find_in_object
from txns.privacy.names import ALLOWLIST_PATH, Allowlist, NameIndex, load_allowlist
from txns.privacy.payload import build as build_payload

__all__ = [
    "ALLOWLIST_PATH",
    "Allowlist",
    "NameIndex",
    "build_payload",
    "find_in_files",
    "find_in_object",
    "load_allowlist",
]
