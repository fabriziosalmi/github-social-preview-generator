"""Deterministic pseudo-randomness.

Procedural artwork must be reproducible: the same repository name has to yield
the same composition on every machine, forever. ``random`` is seeded per
process and its algorithm is an implementation detail of CPython, so this
module derives its stream from SHA-256 instead — specified, portable and
stable across interpreters and versions.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Iterable, List, Sequence, TypeVar

T = TypeVar("T")

_MASK53 = (1 << 53) - 1
_SCALE53 = float(1 << 53)


def digest(*parts: object) -> bytes:
    """Return a stable 32-byte digest of ``parts``.

    Parts are joined with a separator that cannot appear in their UTF-8
    encoding, so ``("ab", "c")`` and ``("a", "bc")`` never collide.
    """
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(str(part).encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.digest()


class Rng:
    """A counter-mode SHA-256 stream exposing the slice of ``random`` we need."""

    __slots__ = ("_key", "_counter", "_buffer", "_offset")

    def __init__(self, *seed_parts: object) -> None:
        self._key = digest(*seed_parts)
        self._counter = 0
        self._buffer = b""
        self._offset = 0

    def fork(self, *seed_parts: object) -> "Rng":
        """Return an independent stream derived from this one plus ``seed_parts``."""
        return Rng(self._key.hex(), *seed_parts)

    def _bytes(self, count: int) -> bytes:
        while len(self._buffer) - self._offset < count:
            block = hashlib.sha256(self._key + struct.pack(">Q", self._counter)).digest()
            self._counter += 1
            self._buffer = self._buffer[self._offset :] + block
            self._offset = 0
        chunk = self._buffer[self._offset : self._offset + count]
        self._offset += count
        return chunk

    def random(self) -> float:
        """Uniform float in ``[0.0, 1.0)`` with 53 bits of entropy."""
        return (struct.unpack(">Q", self._bytes(8))[0] >> 11) / _SCALE53

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.random()

    def randint(self, low: int, high: int) -> int:
        """Uniform integer in the inclusive range ``[low, high]``."""
        if high < low:
            raise ValueError("empty range: %d > %d" % (low, high))
        span = high - low + 1
        # Rejection sampling keeps the distribution exactly uniform.
        limit = (1 << 64) - ((1 << 64) % span)
        while True:
            value = struct.unpack(">Q", self._bytes(8))[0]
            if value < limit:
                return low + value % span

    def chance(self, probability: float) -> bool:
        return self.random() < probability

    def choice(self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("cannot choose from an empty sequence")
        return items[self.randint(0, len(items) - 1)]

    def weighted_choice(self, items: Sequence[T], weights: Sequence[float]) -> T:
        if len(items) != len(weights):
            raise ValueError("items and weights must be the same length")
        total = float(sum(weights))
        if total <= 0:
            raise ValueError("weights must sum to a positive value")
        target = self.random() * total
        cumulative = 0.0
        for item, weight in zip(items, weights):
            cumulative += weight
            if target < cumulative:
                return item
        return items[-1]

    def shuffled(self, items: Iterable[T]) -> List[T]:
        result = list(items)
        for i in range(len(result) - 1, 0, -1):
            j = self.randint(0, i)
            result[i], result[j] = result[j], result[i]
        return result

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """Box-Muller transform; ``random()`` never returns exactly 0 twice here."""
        u1 = self.random()
        while u1 <= 1e-12:
            u1 = self.random()
        u2 = self.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
