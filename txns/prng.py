"""The tool's own PRNG: SplitMix64 with named sub-streams (FR-E2).

Never use the stdlib `random` module in `generate`. Every consumer asks for its
own stream by a stable name path, for example

    streams.stream("item", item_id, "dates")

so adding a storyline or item never shifts another stream's draws (T5).
A stream's starting state is SHA-256(seed, name path), so it depends only on
the seed and the names, never on call order.
"""

from __future__ import annotations

import hashlib
from typing import Sequence, TypeVar

MASK64 = (1 << 64) - 1
GOLDEN_GAMMA = 0x9E3779B97F4A7C15
MAX_SEED = MASK64  # seeds are 0 .. 2**64 - 1

T = TypeVar("T")


class SplitMix64:
    """Reference SplitMix64 (Vigna). State and outputs are unsigned 64-bit ints."""

    __slots__ = ("state",)

    def __init__(self, state: int):
        self.state = state & MASK64

    def next_u64(self) -> int:
        self.state = (self.state + GOLDEN_GAMMA) & MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)


class Stream:
    """One named sub-stream with the draw helpers the engine needs."""

    __slots__ = ("name", "_gen")

    def __init__(self, name: tuple[str, ...], state: int):
        self.name = name
        self._gen = SplitMix64(state)

    def next_u64(self) -> int:
        return self._gen.next_u64()

    def below(self, n: int) -> int:
        """Uniform int in [0, n), unbiased (rejection sampling)."""
        if n <= 0:
            raise ValueError("below() needs n > 0")
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            x = self.next_u64()
            if x < limit:
                return x % n

    def randint(self, lo: int, hi: int) -> int:
        """Uniform int in [lo, hi] inclusive."""
        return lo + self.below(hi - lo + 1)

    def uniform(self) -> float:
        """Float in [0, 1) from the top 53 bits."""
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

    def chance(self, p: float) -> bool:
        return self.uniform() < p

    def choice(self, seq: Sequence[T]) -> T:
        return seq[self.below(len(seq))]

    def weighted_index(self, weights: Sequence[int]) -> int:
        """Index drawn with probability weight/sum. Weights are non-negative ints."""
        total = sum(weights)
        if total <= 0:
            raise ValueError("weighted_index() needs a positive total weight")
        r = self.below(total)
        for i, w in enumerate(weights):
            if r < w:
                return i
            r -= w
        raise AssertionError("unreachable")

    def shuffle(self, items: list) -> None:
        """In-place Fisher-Yates."""
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]


def derive_state(seed: int, names: Sequence[str]) -> int:
    h = hashlib.sha256()
    h.update(b"txns-stream-v1\0")
    h.update(str(seed).encode("ascii"))
    for name in names:
        h.update(b"\0")
        h.update(str(name).encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "little")


class Streams:
    """Factory of named sub-streams for one seed."""

    def __init__(self, seed: int):
        if not 0 <= seed <= MAX_SEED:
            raise ValueError(f"seed out of range: {seed}")
        self.seed = seed

    def stream(self, *names: str) -> Stream:
        """A fresh stream for this name path. Ask once per concern and keep it."""
        if not names:
            raise ValueError("a stream needs a name")
        return Stream(tuple(names), derive_state(self.seed, names))
