"""One catalogue per request.

The shared catalogue list is mutated in place while other requests read it, so
without this an onboarding landing mid-request could put one list into the
first LLM prompt, a second into the other, and a third into the hash the run
manifest records. Readers run together and a writer waits for them and then
runs alone, which is what makes an append atomic against every reader.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class CatalogueLock:
    """A readers-writer lock over the shared catalogue list."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writing = False
        self._waiting_writers = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        """Hold the catalogue steady for the length of one request."""
        with self._condition:
            while self._writing or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        """Change the catalogue with no reader part-way through a request."""
        with self._condition:
            self._waiting_writers += 1
            while self._writing or self._readers:
                self._condition.wait()
            self._waiting_writers -= 1
            self._writing = True
        try:
            yield
        finally:
            with self._condition:
                self._writing = False
                self._condition.notify_all()
