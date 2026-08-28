"""O supervisor — um `ffmpeg` por fonte, vigiado (plano §1.2). É a peça que automatiza o
que a Fase 0 fazia na mão: perceber que caiu/travou e reiniciar sozinho.

Duas formas de falha, tratadas diferente (achado de campo, 2026-08-27):
- **O processo morre de vez** (ex.: DNS falhou, `-reconnect*` se esgotou) → reinicia com
  backoff. Aconteceu de verdade com 3 rádios simultâneas na Fase 0.
- **O processo fica "vivo mas mudo"** (travado num loop de reconexão morto, sem produzir
  segmento novo) → o watchdog mata (`SIGTERM` com prazo curto, escalando pra `SIGKILL`) e
  reinicia. Aconteceu de verdade com o Bloomberg e, depois, com a TV Cultura.

Dependências (launcher, relógio, sleep) são injetáveis de propósito — dá pra testar o
laço inteiro (backoff, flapping, watchdog matando processo travado) com um processo falso,
sem precisar de rede nem `ffmpeg` de verdade.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import signal
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lupa_recorder.capture.policy import BackoffPolicy, FlappingTracker
from lupa_recorder.capture.segments import (
    garantir_pastas_do_dia,
    padrao_saida_ffmpeg,
    promover_segmentos_prontos,
    started_at_do_arquivo,
    ultimo_progresso_em,
)
from lupa_recorder.capture.strategies import criar_estrategia
from lupa_recorder.capture.strategies.base import SourceStrategy
from lupa_recorder.catalog.models import Event, Segment, inserir_segmento, registrar_evento
from lupa_recorder.config import SourceConfig, UrlResolver
from lupa_recorder.resolve import criar_resolver
from lupa_recorder.resolve.base import ResolvedInput, Resolver

logger = logging.getLogger(__name__)

WATCHDOG_TIMEOUT_S = 90.0  # "sem byte novo em 90s com o processo vivo" — plano §1.2
POLL_INTERVAL_S = 5.0
SIGTERM_GRACE_S = 5.0  # achado de campo (Bloomberg): SIGTERM sozinho não basta, escalar
YOUTUBE_RESTART_INTERVAL_S = 3 * 3600  # ajuste 2026-08-28 — metade da janela medida (~6h)
TEMPO_SAUDAVEL_PRA_RESETAR_BACKOFF_S = 300.0


class EstadoSupervisor(enum.StrEnum):
    parado = "parado"
    resolvendo = "resolvendo"
    rodando = "rodando"
    reiniciando = "reiniciando"
    flapping = "flapping"


class MotivoParada(enum.StrEnum):
    processo_morreu = "processo_morreu"
    travado = "travado"
    parado_externamente = "parado_externamente"
    reinicio_planejado = "reinicio_planejado"


class SupervisorError(Exception):
    """Erro não recuperável (bug de programação, não falha de rede) — propaga de verdade."""


# ── abstração de processo, pra dar pra testar sem ffmpeg de verdade ────────────


class ProcessoAsync(Protocol):
    @property
    def returncode(self) -> int | None: ...
    def send_signal(self, sig: int) -> None: ...
    async def wait(self) -> int: ...


class Launcher(Protocol):
    async def iniciar(self, comando: list[str]) -> ProcessoAsync: ...


class AsyncioSubprocessLauncher:
    async def iniciar(self, comando: list[str]) -> ProcessoAsync:
        return await asyncio.create_subprocess_exec(
            *comando,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )


# ── o supervisor ────────────────────────────────────────────────────────────


@dataclass
class ResultadoCiclo:
    motivo: MotivoParada
    tentativas_desde_ultimo_sucesso: int


class SourceSupervisor:
    def __init__(
        self,
        source: SourceConfig,
        data_root: Path,
        *,
        strategy: SourceStrategy | None = None,
        resolver: Resolver | None = None,
        launcher: Launcher | None = None,
        watchdog_timeout_s: float = WATCHDOG_TIMEOUT_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        sigterm_grace_s: float = SIGTERM_GRACE_S,
        backoff: BackoffPolicy | None = None,
        flapping: FlappingTracker | None = None,
        catalog_conn: sqlite3.Connection | None = None,
        sleep=asyncio.sleep,
        agora=time.time,
    ) -> None:
        self.source = source
        self.data_root = data_root
        self.strategy = strategy or criar_estrategia(source)
        self.resolver = resolver or criar_resolver(source)
        self.launcher = launcher or AsyncioSubprocessLauncher()
        self.watchdog_timeout_s = watchdog_timeout_s
        self.poll_interval_s = poll_interval_s
        self.sigterm_grace_s = sigterm_grace_s
        self.backoff = backoff or BackoffPolicy()
        self.flapping = flapping or FlappingTracker()
        # opcional de propósito: os testes do supervisor não passam catálogo nenhum, e o
        # laço continua funcionando igual — só não fica nada gravado (log ainda funciona).
        self.catalog_conn = catalog_conn
        self._sleep = sleep
        self._agora = agora

        self.estado = EstadoSupervisor.parado
        self.tentativas = 0

    def _offset_restart_planejado_s(self) -> int:
        # "deslocado por fonte" (plano §7.4) — não faz todo mundo reiniciar junto.
        return hash(self.source.id) % max(self.source.segment_seconds, 1)

    def _evento(self, kind: str, message: str, *, nivel: int = logging.WARNING) -> None:
        """Loga (sempre) e grava no catálogo (se houver um configurado) — mesma linha de
        auditoria nos dois lugares, sem precisar chamar os dois toda vez."""
        logger.log(nivel, "fonte %s: %s", self.source.slug, message)
        if self.catalog_conn is not None:
            try:
                registrar_evento(self.catalog_conn, Event(source_slug=self.source.slug, kind=kind, message=message))
            except sqlite3.Error:
                logger.exception("fonte %s: falha gravando evento no catálogo", self.source.slug)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                resultado = await self._ciclo(stop_event)
            except Exception as exc:
                # qualquer coisa inesperada (ffmpeg ausente, bug de resolver, o que for)
                # não pode derrubar o processo inteiro — as outras fontes continuam
                # supervisionadas independente desta ter batido um erro que a gente não
                # previu. Achado de campo real (2026-08-28): ffmpeg faltando no PATH
                # subia como FileNotFoundError e matava o `asyncio.gather` de todo mundo.
                logger.exception("fonte %s: erro inesperado no ciclo de captura", self.source.slug)
                self._evento("erro_inesperado", str(exc), nivel=logging.ERROR)
                resultado = ResultadoCiclo(
                    motivo=MotivoParada.processo_morreu, tentativas_desde_ultimo_sucesso=self.tentativas
                )
            if stop_event.is_set():
                self.estado = EstadoSupervisor.parado
                return

            if resultado.motivo == MotivoParada.reinicio_planejado:
                # não é falha — reinicia na hora, sem contar pra backoff/flapping.
                self.estado = EstadoSupervisor.reiniciando
                continue

            self.tentativas += 1
            self.flapping.registrar_restart(self._agora())
            self.estado = (
                EstadoSupervisor.flapping
                if self.flapping.esta_flapping(self._agora())
                else EstadoSupervisor.reiniciando
            )
            atraso = self.backoff.atraso_para_tentativa(self.tentativas)
            self._evento(
                "restart",
                f"parou ({resultado.motivo}), tentativa {self.tentativas}, esperando {atraso:.0f}s",
            )
            if atraso:
                await self._dormir_ou_parar(atraso, stop_event)

    async def _ciclo(self, stop_event: asyncio.Event) -> ResultadoCiclo:
        self.estado = EstadoSupervisor.resolvendo
        await self.strategy.preflight()
        resolved = await self.resolver.resolve(self.source)

        garantir_pastas_do_dia(self.data_root, self.source.slug)
        comando = self._montar_comando(resolved)
        processo = await self.launcher.iniciar(comando)
        self.estado = EstadoSupervisor.rodando
        inicio = self._agora()

        try:
            motivo = await self._monitorar(processo, inicio, stop_event)
        finally:
            await self.strategy.teardown()

        return ResultadoCiclo(motivo=motivo, tentativas_desde_ultimo_sucesso=self.tentativas)

    def _montar_comando(self, resolved: ResolvedInput) -> list[str]:
        return (
            ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin"]
            + self.strategy.build_input(resolved)
            + self.strategy.map_args()
            + [
                "-c",
                "copy",
                "-f",
                "segment",
                "-segment_time",
                str(self.source.segment_seconds),
                "-segment_atclocktime",
                "1",
                "-segment_format",
                "mpegts",
                "-reset_timestamps",
                "1",
                "-strftime",
                "1",
                padrao_saida_ffmpeg(self.data_root, self.source.slug),
            ]
        )

    async def _monitorar(
        self, processo: ProcessoAsync, inicio: float, stop_event: asyncio.Event
    ) -> MotivoParada:
        ultimo_progresso = inicio
        restart_planejado_em = (
            inicio + YOUTUBE_RESTART_INTERVAL_S + self._offset_restart_planejado_s()
            if self.source.url_resolver == UrlResolver.yt_dlp
            else None
        )

        while True:
            if stop_event.is_set():
                await self._matar(processo)
                return MotivoParada.parado_externamente

            if processo.returncode is not None:
                return MotivoParada.processo_morreu

            garantir_pastas_do_dia(self.data_root, self.source.slug)
            for promovido in promover_segmentos_prontos(self.data_root, self.source.slug):
                self._catalogar_segmento(promovido)
            progresso = ultimo_progresso_em(self.data_root, self.source.slug)
            if progresso is not None:
                ultimo_progresso = max(ultimo_progresso, progresso)

            agora = self._agora()

            if agora - ultimo_progresso > self.watchdog_timeout_s:
                self._evento("watchdog", f"sem segmento novo há >{self.watchdog_timeout_s:.0f}s, matando processo travado")
                await self._matar(processo)
                return MotivoParada.travado

            if restart_planejado_em is not None and agora >= restart_planejado_em:
                await self._matar(processo)
                return MotivoParada.reinicio_planejado

            if agora - inicio > TEMPO_SAUDAVEL_PRA_RESETAR_BACKOFF_S:
                self.tentativas = 0

            await self._dormir_ou_parar(self.poll_interval_s, stop_event)

    async def _dormir_ou_parar(self, segundos: float, stop_event: asyncio.Event) -> None:
        """Espera `segundos` (via `self._sleep`, testável/fake) OU até `stop_event`
        disparar — o que vier primeiro. Sem isso, um `SIGTERM` chegando bem no meio de
        um backoff de até 60s (ou de um poll comum) só seria notado depois do atraso
        inteiro passar — achado real (2026-08-28): a parada graciosa demorava até um
        minuto pra responder."""
        if segundos <= 0:
            return
        tarefa_parar = asyncio.ensure_future(stop_event.wait())
        tarefa_dormir = asyncio.ensure_future(self._sleep(segundos))
        try:
            await asyncio.wait({tarefa_parar, tarefa_dormir}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for tarefa in (tarefa_parar, tarefa_dormir):
                if not tarefa.done():
                    tarefa.cancel()

    async def _matar(self, processo: ProcessoAsync) -> None:
        if processo.returncode is not None:
            return
        processo.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(processo.wait(), timeout=self.sigterm_grace_s)
        except TimeoutError:
            # achado de campo (Bloomberg, TV Cultura): SIGTERM sozinho não mata um
            # ffmpeg preso em loop de reconexão morto — precisa escalar.
            self._evento("sigkill", "SIGTERM não bastou, escalando pra SIGKILL")
            processo.send_signal(signal.SIGKILL)
            await processo.wait()

    def _catalogar_segmento(self, caminho: Path) -> None:
        if self.catalog_conn is None:
            return
        iniciado_em = started_at_do_arquivo(caminho)
        if iniciado_em is None:
            return
        try:
            inserir_segmento(
                self.catalog_conn,
                Segment(
                    source_slug=self.source.slug,
                    path=str(caminho),
                    started_at=iniciado_em,
                    bytes=caminho.stat().st_size,
                    archive_profile=self.source.archive_profile,
                ),
            )
        except (sqlite3.Error, FileNotFoundError):
            # não pode derrubar a captura por causa de um problema no catálogo — o
            # arquivo em si já está seguro em disco, o catálogo é reconstruível dele
            # de qualquer jeito (`catalog/recover.py`).
            logger.exception("fonte %s: falha catalogando %s", self.source.slug, caminho)
