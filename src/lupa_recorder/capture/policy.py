"""Backoff exponencial e detecção de `FLAPPING` — mesmo espírito do backoff do Celery já
usado no projeto Lupa (`countdown=60 * (2**tentativas)`, teto, nunca delay fixo). Pura,
sem I/O — o "agora" é injetado, pra dar pra testar sem esperar tempo real."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BackoffPolicy:
    base_s: float = 1.0
    max_s: float = 60.0

    def atraso_para_tentativa(self, tentativa: int) -> float:
        if tentativa <= 0:
            return 0.0
        return min(self.max_s, self.base_s * (2 ** (tentativa - 1)))


@dataclass
class FlappingTracker:
    """`FLAPPING` acima de N restarts numa janela de tempo (plano §1.2: 6 restarts/hora).
    Continua deixando reiniciar — só marca o estado, pra "parar de fazer barulho" em vez
    de gerar evento novo a cada tentativa."""

    janela_s: float = 3600.0
    limite: int = 6
    _eventos: list[float] = field(default_factory=list)

    def registrar_restart(self, agora: float) -> None:
        self._eventos.append(agora)
        self._podar(agora)

    def esta_flapping(self, agora: float) -> bool:
        self._podar(agora)
        return len(self._eventos) > self.limite

    def _podar(self, agora: float) -> None:
        self._eventos = [t for t in self._eventos if agora - t <= self.janela_s]
