"""Resolvedor de URL — decide, na hora de (re)iniciar uma captura, qual URL usar de verdade.

Existe porque nem toda fonte tem URL fixa (plano §8.4): `static` nunca muda, `http_refresh`
busca uma URL fresca num endpoint de API antes de cada (re)start, `yt_dlp` resolve as duas
URLs (vídeo+áudio) de uma live do YouTube via `yt-dlp -g`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from lupa_recorder.config import SourceConfig


class ResolveError(Exception):
    """Erro ao resolver a URL de uma fonte — mensagem já pronta pra log do supervisor."""


@dataclass
class ResolvedInput:
    """Uma ou mais URLs prontas pra virar `-i` do ffmpeg (mais de uma só no caso do
    YouTube: vídeo e áudio vêm separados — achado de campo da Fase 0).
    """

    urls: list[str]
    resolvido_em: float = field(default_factory=time.monotonic)


class Resolver(Protocol):
    async def resolve(self, source: SourceConfig) -> ResolvedInput: ...
