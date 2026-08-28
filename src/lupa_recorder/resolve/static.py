from __future__ import annotations

from lupa_recorder.config import SourceConfig
from lupa_recorder.resolve.base import ResolvedInput, ResolveError


class StaticResolver:
    """`url_resolver=static` — a URL do channels.yaml nunca muda. É o caso comum
    (a maioria das rádios/TVs da Fase 0)."""

    async def resolve(self, source: SourceConfig) -> ResolvedInput:
        if not source.url:
            raise ResolveError(f"fonte {source.slug!r}: sem url configurada.")
        return ResolvedInput(urls=[source.url])
