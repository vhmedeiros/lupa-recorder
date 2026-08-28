from __future__ import annotations

from lupa_recorder.capture.strategies.base import SourceStrategy
from lupa_recorder.resolve.base import ResolvedInput


class HlsStrategy(SourceStrategy):
    """HLS (`.m3u8`). **Nunca `-reconnect*` aqui** — achado de campo (2026-08-26, Rádio
    Cultura/UOL, repetido 3x em testes diferentes): essas flags conflitam com o demuxer
    HLS e prendem o ffmpeg relendo o master playlist pra sempre, sem nunca abrir a
    variante com os segmentos de verdade. Plano §7.3."""

    def build_input(self, resolved: ResolvedInput) -> list[str]:
        return ["-i", resolved.urls[0]]
