from __future__ import annotations

from lupa_recorder.capture.strategies.base import SourceStrategy
from lupa_recorder.resolve.base import ResolvedInput


class HttpProgressiveStrategy(SourceStrategy):
    """HTTP progressivo (a maioria das rádios da Fase 0 — Ouveai, Roraima, Band News).
    É a única estratégia que usa `-reconnect*` — pra HLS isso trava o ffmpeg (ver
    `hls.py`). Mesmo comando validado em campo, `comandos.md`."""

    def build_input(self, resolved: ResolvedInput) -> list[str]:
        return [
            "-reconnect",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "30",
            "-rw_timeout",
            "15000000",
            "-i",
            resolved.urls[0],
        ]
