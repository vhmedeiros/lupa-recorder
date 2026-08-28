from datetime import datetime
from pathlib import Path

import pytest

from lupa_recorder.capture.segments import pasta_do_dia
from lupa_recorder.catalog.db import conectar
from lupa_recorder.catalog.models import SegmentState, listar_eventos, listar_segmentos
from lupa_recorder.catalog.recover import (
    RemuxError,
    reconstruir_catalogo_da_fonte,
    recuperar_orfaos,
)

HOJE = datetime(2026, 8, 28, 17, 0, 0)


@pytest.fixture
def conn(tmp_path):
    conexao = conectar(tmp_path / "system" / "catalogo.sqlite3")
    yield conexao
    conexao.close()


@pytest.fixture
def data_root(tmp_path):
    return tmp_path / "data"


def _criar_arquivo(pasta: Path, nome: str, tamanho: int = 1000) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome
    caminho.write_bytes(b"x" * tamanho)
    return caminho


class TestRecuperarOrfaos:
    def test_remux_bem_sucedido_vira_partial_no_catalogo(self, conn, data_root):
        pasta = pasta_do_dia(data_root, "radio-x", HOJE)
        orfao = _criar_arquivo(pasta, "170000.ts.part", tamanho=5000)

        def remuxer_falso(caminho: Path) -> Path:
            destino = caminho.with_suffix("")
            destino.write_bytes(caminho.read_bytes())  # simula remux "perfeito"
            caminho.unlink()
            return destino

        resultado = recuperar_orfaos(conn, data_root, "radio-x", remuxer=remuxer_falso)

        assert len(resultado.recuperados) == 1
        assert not orfao.exists()
        assert (pasta / "170000.ts").exists()

        segmentos = listar_segmentos(conn, source_slug="radio-x")
        assert len(segmentos) == 1
        assert segmentos[0].state == SegmentState.partial
        assert segmentos[0].started_at == "2026-08-28T17:00:00"

        eventos = listar_eventos(conn, source_slug="radio-x")
        assert any(e.kind == "recover_partial" for e in eventos)

    def test_remux_falho_descarta_o_arquivo_e_registra_evento(self, conn, data_root):
        pasta = pasta_do_dia(data_root, "radio-x", HOJE)
        orfao = _criar_arquivo(pasta, "170000.ts.part")

        def remuxer_que_falha(caminho: Path) -> Path:
            raise RemuxError("simulação de corrupção total")

        resultado = recuperar_orfaos(conn, data_root, "radio-x", remuxer=remuxer_que_falha)

        assert resultado.recuperados == []
        assert len(resultado.descartados) == 1
        assert not orfao.exists()  # descartado de verdade — plano §1.4: "descarta o resto"

        eventos = listar_eventos(conn, source_slug="radio-x")
        assert any(e.kind == "recover_failed" for e in eventos)
        assert len(listar_segmentos(conn, source_slug="radio-x")) == 0

    def test_sem_nenhum_orfao_nao_faz_nada(self, conn, data_root):
        resultado = recuperar_orfaos(conn, data_root, "radio-x")
        assert resultado.recuperados == []
        assert resultado.descartados == []

    def test_so_o_mais_recente_nunca_vira_orfao(self, conn, data_root):
        # cenário real: se tem 2 .part, o mais antigo já devia ter sido promovido em
        # operação normal (capture/segments.py) — mas se o processo caiu no meio, os DOIS
        # aparecem como órfãos aqui, e ambos passam pelo remux (comportamento correto:
        # recover não sabe distinguir "travou no meio" de "operação normal interrompida").
        pasta = pasta_do_dia(data_root, "radio-x", HOJE)
        _criar_arquivo(pasta, "170000.ts.part")
        _criar_arquivo(pasta, "170400.ts.part")

        def remuxer_falso(caminho: Path) -> Path:
            destino = caminho.with_suffix("")
            destino.write_bytes(b"remuxado")
            caminho.unlink()
            return destino

        resultado = recuperar_orfaos(conn, data_root, "radio-x", remuxer=remuxer_falso)

        assert len(resultado.recuperados) == 2


class TestReconstruirCatalogoDaFonte:
    def test_reconstroi_do_zero_a_partir_dos_arquivos(self, conn, data_root):
        pasta = pasta_do_dia(data_root, "tv-cultura", HOJE)
        _criar_arquivo(pasta, "170000.ts")
        _criar_arquivo(pasta, "170400.ts")

        novos, ja_catalogados = reconstruir_catalogo_da_fonte(
            conn, data_root, "tv-cultura", obter_duracao_ms=lambda _: None
        )

        assert novos == 2
        assert ja_catalogados == 0
        assert len(listar_segmentos(conn, source_slug="tv-cultura")) == 2

    def test_e_idempotente_rodando_de_novo(self, conn, data_root):
        pasta = pasta_do_dia(data_root, "tv-cultura", HOJE)
        _criar_arquivo(pasta, "170000.ts")

        reconstruir_catalogo_da_fonte(conn, data_root, "tv-cultura", obter_duracao_ms=lambda _: None)
        novos, ja_catalogados = reconstruir_catalogo_da_fonte(
            conn, data_root, "tv-cultura", obter_duracao_ms=lambda _: None
        )

        assert novos == 0
        assert ja_catalogados == 1
        assert len(listar_segmentos(conn, source_slug="tv-cultura")) == 1

    def test_ignora_part_ainda_em_escrita(self, conn, data_root):
        pasta = pasta_do_dia(data_root, "tv-cultura", HOJE)
        _criar_arquivo(pasta, "170000.ts")
        _criar_arquivo(pasta, "170400.ts.part")  # ainda sendo escrito — não é .ts

        novos, _ = reconstruir_catalogo_da_fonte(
            conn, data_root, "tv-cultura", obter_duracao_ms=lambda _: None
        )

        assert novos == 1  # só o .ts fechado

    def test_fonte_sem_nenhuma_pasta_ainda_devolve_zero(self, conn, data_root):
        assert reconstruir_catalogo_da_fonte(conn, data_root, "fonte-nova") == (0, 0)

    def test_apagar_sqlite_e_reconstruir_recupera_tudo(self, data_root, tmp_path):
        # o teste que a checklist da 1.4 pede explicitamente: "confirmar que o catálogo é
        # reconstruível a partir dos nomes de arquivo — testar apagando o SQLite de propósito".
        pasta = pasta_do_dia(data_root, "tv-cultura", HOJE)
        _criar_arquivo(pasta, "170000.ts")
        _criar_arquivo(pasta, "170400.ts")
        _criar_arquivo(pasta, "170800.ts")

        caminho_db = tmp_path / "system" / "catalogo.sqlite3"
        conn1 = conectar(caminho_db)
        reconstruir_catalogo_da_fonte(conn1, data_root, "tv-cultura", obter_duracao_ms=lambda _: None)
        assert len(listar_segmentos(conn1, source_slug="tv-cultura")) == 3
        conn1.close()

        caminho_db.unlink()  # "apagando o SQLite de propósito"
        (caminho_db.parent / f"{caminho_db.name}-wal").unlink(missing_ok=True)
        (caminho_db.parent / f"{caminho_db.name}-shm").unlink(missing_ok=True)

        conn2 = conectar(caminho_db)  # recria o schema do zero
        assert listar_segmentos(conn2, source_slug="tv-cultura") == []  # banco novo, vazio

        novos, _ = reconstruir_catalogo_da_fonte(
            conn2, data_root, "tv-cultura", obter_duracao_ms=lambda _: None
        )

        assert novos == 3
        assert len(listar_segmentos(conn2, source_slug="tv-cultura")) == 3
        conn2.close()
