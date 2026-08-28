"""Dataclasses + funções de CRUD do catálogo. Sem ORM — `sqlite3` puro (stdlib), bate com
"gravador leve, dependências mínimas" (plano §12). Datas em ISO 8601 (texto), sempre UTC
implícito na hora de gravar (`strftime('...Z', 'now')` do SQLite já é UTC).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum


class SegmentState(StrEnum):
    ready = "ready"
    partial = "partial"
    purged = "purged"


@dataclass
class Segment:
    source_slug: str
    path: str
    started_at: str  # ISO 8601
    bytes: int
    state: SegmentState = SegmentState.ready
    duration_ms: int | None = None
    archive_profile: str = "copy"
    has_thumbnails: bool = False
    sha256: str | None = None
    hold_until: str | None = None  # ISO 8601 — protege do GC enquanto no futuro
    id: int | None = None


def inserir_segmento(conn: sqlite3.Connection, seg: Segment) -> int:
    """Idempotente por natureza (igual a Fase 2 vai fazer do lado servidor, §2.2): reenviar
    o mesmo `(source_slug, started_at)` não duplica, só ignora — importante pro `recover`
    poder rodar de novo sem medo."""
    cur = conn.execute(
        """
        INSERT INTO segment (source_slug, path, started_at, duration_ms, bytes, state,
                              archive_profile, has_thumbnails, sha256, hold_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_slug, started_at) DO NOTHING
        """,
        (
            seg.source_slug,
            seg.path,
            seg.started_at,
            seg.duration_ms,
            seg.bytes,
            seg.state.value,
            seg.archive_profile,
            int(seg.has_thumbnails),
            seg.sha256,
            seg.hold_until,
        ),
    )
    if cur.lastrowid and cur.rowcount:
        return cur.lastrowid
    linha = conn.execute(
        "SELECT id FROM segment WHERE source_slug = ? AND started_at = ?",
        (seg.source_slug, seg.started_at),
    ).fetchone()
    return linha["id"]


def segmento_existe(conn: sqlite3.Connection, source_slug: str, started_at: str) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM segment WHERE source_slug = ? AND started_at = ?", (source_slug, started_at)
    ).fetchone()
    return linha is not None


def marcar_estado(conn: sqlite3.Connection, source_slug: str, started_at: str, estado: SegmentState) -> None:
    conn.execute(
        "UPDATE segment SET state = ? WHERE source_slug = ? AND started_at = ?",
        (estado.value, source_slug, started_at),
    )


def marcar_has_thumbnails(conn: sqlite3.Connection, source_slug: str, started_at: str, valor: bool) -> None:
    conn.execute(
        "UPDATE segment SET has_thumbnails = ? WHERE source_slug = ? AND started_at = ?",
        (int(valor), source_slug, started_at),
    )


def definir_hold_until(conn: sqlite3.Connection, source_slug: str, started_at: str, hold_until: str | None) -> None:
    conn.execute(
        "UPDATE segment SET hold_until = ? WHERE source_slug = ? AND started_at = ?",
        (hold_until, source_slug, started_at),
    )


def listar_segmentos(
    conn: sqlite3.Connection, source_slug: str | None = None, estado: SegmentState | None = None
) -> list[Segment]:
    query = "SELECT * FROM segment WHERE 1=1"
    params: list[object] = []
    if source_slug is not None:
        query += " AND source_slug = ?"
        params.append(source_slug)
    if estado is not None:
        query += " AND state = ?"
        params.append(estado.value)
    query += " ORDER BY started_at"
    linhas = conn.execute(query, params).fetchall()
    return [_linha_para_segmento(linha) for linha in linhas]


def _linha_para_segmento(linha: sqlite3.Row) -> Segment:
    return Segment(
        id=linha["id"],
        source_slug=linha["source_slug"],
        path=linha["path"],
        started_at=linha["started_at"],
        duration_ms=linha["duration_ms"],
        bytes=linha["bytes"],
        state=SegmentState(linha["state"]),
        archive_profile=linha["archive_profile"],
        has_thumbnails=bool(linha["has_thumbnails"]),
        sha256=linha["sha256"],
        hold_until=linha["hold_until"],
    )


@dataclass
class Event:
    kind: str
    message: str
    source_slug: str | None = None
    id: int | None = None


def registrar_evento(conn: sqlite3.Connection, evento: Event) -> int:
    cur = conn.execute(
        "INSERT INTO event (source_slug, kind, message) VALUES (?, ?, ?)",
        (evento.source_slug, evento.kind, evento.message),
    )
    return cur.lastrowid


def listar_eventos(conn: sqlite3.Connection, source_slug: str | None = None, limite: int = 100) -> list[Event]:
    if source_slug is not None:
        linhas = conn.execute(
            "SELECT * FROM event WHERE source_slug = ? ORDER BY created_at DESC LIMIT ?",
            (source_slug, limite),
        ).fetchall()
    else:
        linhas = conn.execute(
            "SELECT * FROM event ORDER BY created_at DESC LIMIT ?", (limite,)
        ).fetchall()
    return [
        Event(id=linha["id"], source_slug=linha["source_slug"], kind=linha["kind"], message=linha["message"])
        for linha in linhas
    ]


@dataclass
class OutboxEntry:
    kind: str
    payload: dict = field(default_factory=dict)
    id: int | None = None


def enfileirar_outbox(conn: sqlite3.Connection, entrada: OutboxEntry) -> int:
    """Só grava — nada drena esta fila ainda (Fase 2, `sync/client.py`). Existe pra o
    schema já nascer certo, não migrar depois (plano §1.4)."""
    cur = conn.execute(
        "INSERT INTO outbox (kind, payload) VALUES (?, ?)",
        (entrada.kind, json.dumps(entrada.payload, ensure_ascii=False)),
    )
    return cur.lastrowid
