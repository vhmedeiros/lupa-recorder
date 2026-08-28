import sqlite3

import pytest

from lupa_recorder.catalog.db import conectar
from lupa_recorder.catalog.models import (
    Event,
    OutboxEntry,
    Segment,
    SegmentState,
    enfileirar_outbox,
    inserir_segmento,
    listar_eventos,
    listar_segmentos,
    marcar_estado,
    registrar_evento,
)


@pytest.fixture
def conn(tmp_path):
    conexao = conectar(tmp_path / "catalogo.sqlite3")
    yield conexao
    conexao.close()


def test_conectar_cria_arquivo_e_wal(tmp_path):
    caminho = tmp_path / "sub" / "catalogo.sqlite3"
    conexao = conectar(caminho)

    assert caminho.exists()
    modo = conexao.execute("PRAGMA journal_mode").fetchone()[0]
    assert modo == "wal"
    conexao.close()


def test_conectar_e_idempotente(tmp_path):
    caminho = tmp_path / "catalogo.sqlite3"
    conectar(caminho).close()
    conexao2 = conectar(caminho)  # não pode levantar "table already exists"
    conexao2.close()


class TestSegment:
    def test_insere_e_lista(self, conn):
        seg = Segment(
            source_slug="tv-cultura",
            path="/data/tv-cultura/2026-08-28/170000.ts",
            started_at="2026-08-28T17:00:00",
            bytes=27_000_000,
            duration_ms=240_000,
        )
        id_inserido = inserir_segmento(conn, seg)

        segmentos = listar_segmentos(conn, source_slug="tv-cultura")

        assert len(segmentos) == 1
        assert segmentos[0].id == id_inserido
        assert segmentos[0].state == SegmentState.ready
        assert segmentos[0].bytes == 27_000_000

    def test_insercao_duplicada_e_idempotente(self, conn):
        seg = Segment(
            source_slug="radio-x",
            path="/data/radio-x/2026-08-28/170000.ts",
            started_at="2026-08-28T17:00:00",
            bytes=1000,
        )
        id1 = inserir_segmento(conn, seg)
        id2 = inserir_segmento(conn, seg)  # mesmo (source_slug, started_at) — não duplica

        assert id1 == id2
        assert len(listar_segmentos(conn, source_slug="radio-x")) == 1

    def test_marcar_estado(self, conn):
        seg = Segment(
            source_slug="radio-x",
            path="/data/radio-x/2026-08-28/170000.ts",
            started_at="2026-08-28T17:00:00",
            bytes=1000,
        )
        inserir_segmento(conn, seg)

        marcar_estado(conn, "radio-x", "2026-08-28T17:00:00", SegmentState.partial)

        segmentos = listar_segmentos(conn, source_slug="radio-x")
        assert segmentos[0].state == SegmentState.partial

    def test_filtra_por_estado(self, conn):
        for i, estado in enumerate([SegmentState.ready, SegmentState.partial, SegmentState.ready]):
            seg = Segment(
                source_slug="radio-x",
                path=f"/data/radio-x/2026-08-28/{i}.ts",
                started_at=f"2026-08-28T17:0{i}:00",
                bytes=1000,
                state=estado,
            )
            inserir_segmento(conn, seg)

        prontos = listar_segmentos(conn, source_slug="radio-x", estado=SegmentState.ready)

        assert len(prontos) == 2

    def test_started_at_precisa_ser_unico_por_fonte_mas_nao_entre_fontes(self, conn):
        inserir_segmento(
            conn,
            Segment(source_slug="a", path="/a.ts", started_at="2026-08-28T17:00:00", bytes=1),
        )
        inserir_segmento(
            conn,
            Segment(source_slug="b", path="/b.ts", started_at="2026-08-28T17:00:00", bytes=1),
        )

        assert len(listar_segmentos(conn)) == 2

    def test_state_invalido_e_rejeitado_pelo_banco(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO segment (source_slug, path, started_at, bytes, state) "
                "VALUES ('x', '/x.ts', 'agora', 1, 'estado-invalido')"
            )


class TestEvent:
    def test_registra_e_lista(self, conn):
        registrar_evento(conn, Event(source_slug="tv-cultura", kind="restart", message="processo morreu"))
        registrar_evento(conn, Event(source_slug="radio-x", kind="flapping", message="6+ restarts/h"))

        todos = listar_eventos(conn)
        so_tv_cultura = listar_eventos(conn, source_slug="tv-cultura")

        assert len(todos) == 2
        assert len(so_tv_cultura) == 1
        assert so_tv_cultura[0].kind == "restart"

    def test_limite_de_linhas(self, conn):
        for i in range(5):
            registrar_evento(conn, Event(kind="x", message=str(i)))

        assert len(listar_eventos(conn, limite=3)) == 3


class TestOutbox:
    def test_enfileira_sem_erro(self, conn):
        id_ = enfileirar_outbox(conn, OutboxEntry(kind="segment", payload={"path": "/x.ts"}))

        assert id_ is not None
        linha = conn.execute("SELECT kind, payload FROM outbox WHERE id = ?", (id_,)).fetchone()
        assert linha["kind"] == "segment"
        assert '"path"' in linha["payload"]
