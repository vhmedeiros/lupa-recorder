"""Conexão SQLite do catálogo — WAL, `synchronous=FULL`, no SSD (`system_root`, não
`data_root` — plano §1.4). O catálogo é redundante por construção, nunca fonte única: como
`-strftime 1` já põe o timestamp no nome do arquivo, dá pra reconstruir tudo a partir do
filesystem se o banco corromper (`catalog/recover.py`).

Schema evolui por **migração de verdade** (`PRAGMA user_version`, mecanismo nativo do
SQLite), não só `CREATE TABLE IF NOT EXISTS` — achado ao vivo (2026-08-28): um catálogo
criado antes da coluna `hold_until` existir nunca ganhava ela, porque `IF NOT EXISTS` não
altera tabela que já existe. Cada entrada de `MIGRACOES` é aplicada uma vez, em ordem, e
nunca mais — editar uma migração antiga depois que alguém já rodou é o mesmo erro que
editar uma migration do Django já aplicada em produção.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRACOES: list[str] = [
    # 1 — schema inicial (plano §1.4): segment, day, event, outbox.
    """
    CREATE TABLE IF NOT EXISTS segment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_slug TEXT NOT NULL,
        path TEXT NOT NULL,
        started_at TEXT NOT NULL,
        duration_ms INTEGER,
        bytes INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('ready', 'partial', 'purged')),
        archive_profile TEXT NOT NULL DEFAULT 'copy',
        has_thumbnails INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (source_slug, started_at)
    );
    CREATE INDEX IF NOT EXISTS idx_segment_source_started ON segment (source_slug, started_at);
    CREATE INDEX IF NOT EXISTS idx_segment_state ON segment (state);

    CREATE TABLE IF NOT EXISTS day (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_slug TEXT NOT NULL,
        date TEXT NOT NULL,
        covered_seconds INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        UNIQUE (source_slug, date)
    );

    CREATE TABLE IF NOT EXISTS event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_slug TEXT,
        kind TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    CREATE INDEX IF NOT EXISTS idx_event_created ON event (created_at);

    -- Fila de metadata a enviar pra Lupa — sem consumidor até a Fase 2, schema nasce aqui
    -- de propósito (plano §1.4: "pra não migrar depois").
    CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    """,
    # 2 — hold_until em segment (sub-etapa 1.5): protege contra o GC. "Clipado"/"com
    # menção" são conceito de Fase 2/3 (servidor); localmente só existe esse hold manual,
    # gancho pro "sob relato de falha do operador" que ainda não tem UI nenhuma.
    "ALTER TABLE segment ADD COLUMN hold_until TEXT;",
]


def _aplicar_migracoes(conn: sqlite3.Connection) -> None:
    versao_atual = conn.execute("PRAGMA user_version").fetchone()[0]
    for indice, sql in enumerate(MIGRACOES[versao_atual:], start=versao_atual):
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {indice + 1}")


def conectar(caminho_db: Path) -> sqlite3.Connection:
    caminho_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho_db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    _aplicar_migracoes(conn)
    return conn
