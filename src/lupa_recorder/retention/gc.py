"""GC por pressão + teto de idade (plano §6.4, ajuste 2026-08-28). Dois mecanismos
independentes, não um só:

- **Teto de idade incondicional por tier** (`critical` 7d, `standard` 5d, `background` 2d) —
  roda sempre, mesmo com disco vazio. Existe porque a retenção curta também é redução de
  risco jurídico (GRV-03), não só economia de espaço.
- **Pressão por watermark** — só entra em ação se o teto de idade sozinho não bastou (ex.:
  canal novo cadastrado sem recalcular projeção, mudança de bitrate da emissora). Ordem de
  sacrifício: `background` → `standard` → `critical`, mais antigo primeiro dentro do tier.

**Nunca toca em segmento com `hold_until` no futuro** — nem idade, nem pressão. É o gancho
local pro "sob relato de falha do operador" (plano §6.4); "clipado"/"com menção" são conceito
de Fase 2/3 (servidor), não existem ainda localmente.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from sqlite3 import Connection

from lupa_recorder.catalog.models import (
    Event,
    Segment,
    SegmentState,
    listar_segmentos,
    marcar_estado,
    registrar_evento,
)
from lupa_recorder.config import RETENCAO_DIAS_POR_TIER, Tier

ORDEM_SACRIFICIO: tuple[Tier, ...] = (Tier.background, Tier.standard, Tier.critical)


def _segmento_protegido(seg: Segment, agora: datetime) -> bool:
    if not seg.hold_until:
        return False
    try:
        return datetime.fromisoformat(seg.hold_until) > agora
    except ValueError:
        return False  # hold_until inválido não protege — falha segura pro lado de apagar


def uso_do_disco_pct(caminho: Path) -> float:
    uso = shutil.disk_usage(caminho)
    return uso.used / uso.total


def _apagar_segmento(conn: Connection, seg: Segment, motivo: str) -> int:
    """Apaga o arquivo (se existir) e marca `purged` no catálogo — a linha nunca some do
    banco, só o arquivo do disco. Devolve bytes liberados (0 se o arquivo já não existia)."""
    caminho = Path(seg.path)
    liberados = 0
    if caminho.exists():
        liberados = caminho.stat().st_size
        caminho.unlink()
    marcar_estado(conn, seg.source_slug, seg.started_at, SegmentState.purged)
    registrar_evento(
        conn, Event(source_slug=seg.source_slug, kind="gc_purge", message=f"{caminho.name}: {motivo}")
    )
    return liberados


@dataclass
class ResultadoGC:
    apagados_por_idade: list[Segment] = field(default_factory=list)
    apagados_por_pressao: list[Segment] = field(default_factory=list)
    bytes_liberados: int = 0


def purgar_expirados_por_idade(
    conn: Connection, tier_por_fonte: dict[str, Tier], agora: datetime | None = None
) -> list[Segment]:
    agora = agora or datetime.now()
    apagados = []
    for slug, tier in tier_por_fonte.items():
        limite = agora - timedelta(days=RETENCAO_DIAS_POR_TIER[tier])
        for seg in listar_segmentos(conn, source_slug=slug, estado=SegmentState.ready):
            if _segmento_protegido(seg, agora):
                continue
            if datetime.fromisoformat(seg.started_at) < limite:
                _apagar_segmento(conn, seg, f"idade > {RETENCAO_DIAS_POR_TIER[tier]}d (tier {tier})")
                apagados.append(seg)
    return apagados


def purgar_por_pressao(
    conn: Connection, tier_por_fonte: dict[str, Tier], bytes_a_liberar: int, agora: datetime | None = None
) -> list[Segment]:
    """Sacrifica na ordem `background → standard → critical`, mais antigo primeiro dentro
    de cada tier, até liberar `bytes_a_liberar` (ou esgotar o que dá pra sacrificar)."""
    agora = agora or datetime.now()
    apagados: list[Segment] = []
    liberado = 0

    for tier in ORDEM_SACRIFICIO:
        if liberado >= bytes_a_liberar:
            break
        fontes_do_tier = [slug for slug, t in tier_por_fonte.items() if t == tier]
        candidatos: list[Segment] = []
        for slug in fontes_do_tier:
            candidatos.extend(listar_segmentos(conn, source_slug=slug, estado=SegmentState.ready))
        candidatos.sort(key=lambda s: s.started_at)  # mais antigo primeiro

        for seg in candidatos:
            if liberado >= bytes_a_liberar:
                break
            if _segmento_protegido(seg, agora):
                continue
            liberado += _apagar_segmento(conn, seg, "pressão de disco (watermark)")
            apagados.append(seg)

    return apagados


def executar_ciclo(
    conn: Connection,
    data_root: Path,
    tier_por_fonte: dict[str, Tier],
    *,
    watermark_high: float = 0.85,
    watermark_low: float = 0.70,
    agora: datetime | None = None,
    medir_uso_pct=uso_do_disco_pct,
    medir_total_bytes=lambda p: shutil.disk_usage(p).total,
) -> ResultadoGC:
    """`medir_uso_pct`/`medir_total_bytes` são injetáveis de propósito — a sub-etapa pede
    testar "enchendo os discos artificialmente (tmpfs pequeno de teste)", que é um teste de
    máquina real, não de CI; aqui dá pra testar a lógica de disparo do watermark sem
    precisar montar um tmpfs de verdade."""
    agora = agora or datetime.now()
    resultado = ResultadoGC()

    resultado.apagados_por_idade = purgar_expirados_por_idade(conn, tier_por_fonte, agora)

    uso = medir_uso_pct(data_root)
    if uso > watermark_high:
        total = medir_total_bytes(data_root)
        bytes_a_liberar = int((uso - watermark_low) * total)
        resultado.apagados_por_pressao = purgar_por_pressao(conn, tier_por_fonte, bytes_a_liberar, agora)
        registrar_evento(
            conn,
            Event(
                kind="disk_pressure",
                message=f"uso {uso:.0%} > watermark {watermark_high:.0%} — {len(resultado.apagados_por_pressao)} segmento(s) sacrificado(s)",
            ),
        )

    resultado.bytes_liberados = sum(
        s.bytes for s in resultado.apagados_por_idade + resultado.apagados_por_pressao
    )
    return resultado


POLL_INTERVAL_S = 300.0  # "a cada 5 min" — plano §6.4


async def executar_loop(
    conn: Connection,
    data_root: Path,
    tier_por_fonte: dict[str, Tier],
    stop_event: asyncio.Event,
    *,
    poll_interval_s: float = POLL_INTERVAL_S,
    sleep=None,
    **kwargs_executar_ciclo,
) -> None:
    """Roda `executar_ciclo` a cada `poll_interval_s`, até `stop_event` disparar.

    **`ionice -c3` real fica pendente** — GC roda no mesmo processo/event loop das
    capturas; aplicar `ionice` por PID afetaria a escrita de captura também, não só o GC
    (precisaria de processo/thread separado pra isolar, o que não compensa nesta fase — o
    trabalho do GC já é leve, só deleção de arquivo, sem leitura pesada, e roda em rajadas
    curtas por natureza). Documentado como simplificação deliberada, não esquecimento.
    """
    sleep = sleep or asyncio.sleep
    while not stop_event.is_set():
        try:
            executar_ciclo(conn, data_root, tier_por_fonte, **kwargs_executar_ciclo)
        except Exception:
            logging.getLogger(__name__).exception("gc: erro inesperado no ciclo")

        tarefa_parar = asyncio.ensure_future(stop_event.wait())
        tarefa_dormir = asyncio.ensure_future(sleep(poll_interval_s))
        try:
            await asyncio.wait({tarefa_parar, tarefa_dormir}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for tarefa in (tarefa_parar, tarefa_dormir):
                if not tarefa.done():
                    tarefa.cancel()
