"""Everything an engine stage may read. The engine never touches files, env or clock."""

from __future__ import annotations

from dataclasses import dataclass

from txns.bundle.model import Bundle
from txns.config import ResolvedConfig
from txns.prng import Stream, Streams


@dataclass(frozen=True)
class EngineContext:
    bundle: Bundle
    config: ResolvedConfig
    streams: Streams

    def item_stream(self, item_id: str, concern: str) -> Stream:
        """Per-item sub-stream. Concerns: dates, quantities, prices, text, messiness.

        Keyed by storyline and item id only, so adding or removing any other
        item or storyline never changes this stream (T5).
        """
        item = self.bundle.items[item_id]
        return self.streams.stream("storyline", item.storyline, "item", item_id, concern)

    def stream(self, *names: str) -> Stream:
        """Global sub-stream for a concern that spans items (ordering, calibration)."""
        return self.streams.stream(*names)
