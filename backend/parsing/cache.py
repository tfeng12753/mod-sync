"""Bounded, process-local cache of validated parses, never order or inventory decisions."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from hashlib import sha256
import json
import os
from threading import RLock
from time import monotonic
from typing import Callable


class ParseCache:
    def __init__(self, max_entries=256, ttl_seconds=600, clock=monotonic):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Parse cache capacity and lifetime must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries = OrderedDict()
        self._pending: dict[str, Future] = {}
        self._lock = RLock()

    def get_or_compute(self, key: str, compute: Callable[[], dict]) -> tuple[dict, bool]:
        with self._lock:
            now = self.clock()
            for expired in [k for k, (until, _) in self._entries.items() if until <= now]:
                del self._entries[expired]
            if key in self._entries:
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key][1]), True
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = self._pending[key] = Future()

        # Identical concurrent requests share one provider call; other requests do not block.
        if not owner:
            return deepcopy(pending.result()), True
        try:
            value = deepcopy(compute())
        except BaseException as exc:
            with self._lock:
                self._pending.pop(key, None)
                pending.set_exception(exc)
            raise
        with self._lock:
            self._entries[key] = (self.clock() + self.ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            self._pending.pop(key, None)
            pending.set_result(value)
        return deepcopy(value), False


_PARSE_CACHE = ParseCache()


def parse_with_cache(request, *, client=None, cache=None):
    from .parser import OUTPUT_SCHEMA, SYSTEM_PROMPT, ParseResult, configured_model, parse_modifications, validate_result

    if os.getenv("PARSER_CACHE_ENABLED", "1").lower() in ("0", "false", "off"):
        return parse_modifications(request, client=client), False
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if client is None and not api_key:
        raise RuntimeError("Set the OPENROUTER_API_KEY environment variable")
    # Full context is significant: do not normalize away negation, quantities, or prior replies.
    context = {
        "request": request.model_dump(mode="json"),
        "model": configured_model(),
        "system_prompt": SYSTEM_PROMPT,
        "output_schema": OUTPUT_SCHEMA,
        "credential_scope": sha256(api_key.encode()).hexdigest(),
    }
    key = sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    active_cache = cache if cache is not None else _PARSE_CACHE
    data, hit = active_cache.get_or_compute(
        key, lambda: parse_modifications(request, client=client).model_dump(mode="json"))
    # Every hit reruns current schema checks and the original-text/clarification safety backstop.
    return validate_result(request, ParseResult.model_validate(data)), hit
