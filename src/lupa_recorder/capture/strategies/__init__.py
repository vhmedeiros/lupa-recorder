from __future__ import annotations

from lupa_recorder.capture.strategies.base import SourceStrategy, StrategyError
from lupa_recorder.capture.strategies.hls import HlsStrategy
from lupa_recorder.capture.strategies.http_progressive import HttpProgressiveStrategy
from lupa_recorder.capture.strategies.rtsp import RtspStrategy
from lupa_recorder.capture.strategies.youtube import YoutubeStrategy
from lupa_recorder.config import Protocol, SourceConfig

_ESTRATEGIA_POR_PROTOCOLO: dict[Protocol, type[SourceStrategy]] = {
    Protocol.hls: HlsStrategy,
    Protocol.http: HttpProgressiveStrategy,
    Protocol.rtsp: RtspStrategy,
    Protocol.youtube: YoutubeStrategy,
}


def criar_estrategia(source: SourceConfig) -> SourceStrategy:
    """`protocol=dvb` nunca chega aqui — `SourceConfig` já rejeita no cadastro (GRV-01)."""
    classe = _ESTRATEGIA_POR_PROTOCOLO.get(source.protocol)
    if classe is None:
        raise StrategyError(f"fonte {source.slug!r}: sem estratégia pra protocol={source.protocol}.")
    return classe(source)


__all__ = [
    "HlsStrategy",
    "HttpProgressiveStrategy",
    "RtspStrategy",
    "SourceStrategy",
    "StrategyError",
    "YoutubeStrategy",
    "criar_estrategia",
]
