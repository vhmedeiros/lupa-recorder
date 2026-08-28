"""`SourceStrategy` — o ponto de extensão por tipo de fonte (plano §1.2). Cada protocolo tem
manhas próprias descobertas na Fase 0 (`-reconnect*` mata HLS, YouTube precisa de duas
entradas com `-thread_queue_size`...) — a estratégia isola isso, o supervisor não precisa
saber o detalhe de cada uma. É também o que vai acomodar DVB depois sem reescrever o
supervisor (`preflight`/`teardown` existem principalmente pra isso — hoje, pras fontes de
rede, praticamente não fazem nada).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lupa_recorder.config import SourceConfig, SourceKind
from lupa_recorder.resolve.base import ResolvedInput


class StrategyError(Exception):
    """Erro de pré-condição da estratégia (preflight) — mensagem pronta pra log."""


class SourceStrategy(ABC):
    def __init__(self, source: SourceConfig) -> None:
        self.source = source

    async def preflight(self) -> None:  # noqa: B027 — no-op intencional, gancho pro DVB
        """Confere pré-condições antes de tentar capturar. Sem-op pras estratégias de
        rede — existe principalmente como o gancho que o DVB vai usar (reservar
        adaptador, esperar FE_HAS_LOCK)."""

    @abstractmethod
    def build_input(self, resolved: ResolvedInput) -> list[str]:
        """As flags de entrada do ffmpeg (tudo antes do primeiro `-i`, mais os `-i` em
        si) — específico de cada protocolo."""

    def map_args(self) -> list[str]:
        """As flags `-map` — depende só de `kind` pra quase todo mundo (rádio = só
        áudio); estratégias com entrada múltipla (YouTube) sobrescrevem."""
        if self.source.kind == SourceKind.radio:
            return ["-map", "0:a:0"]
        return ["-map", "0:v:0", "-map", "0:a:0"]

    async def teardown(self) -> None:  # noqa: B027 — no-op intencional, gancho pro DVB
        """Libera recursos reservados no preflight. Sem-op pras estratégias de rede."""
