import time
from datetime import datetime, timedelta

from lupa_recorder.capture.segments import (
    garantir_pastas_do_dia,
    listar_parciais,
    padrao_saida_ffmpeg,
    pasta_do_dia,
    promover_segmentos_prontos,
    started_at_do_arquivo,
    ultimo_progresso_em,
)

HOJE = datetime(2026, 8, 28, 15, 0, 0)


def test_pasta_do_dia_formato_iso(tmp_path):
    pasta = pasta_do_dia(tmp_path, "tv-cultura", HOJE)
    assert pasta == tmp_path / "tv-cultura" / "2026-08-28"


def test_padrao_saida_ffmpeg_tem_strftime_pra_pasta_e_arquivo(tmp_path):
    padrao = padrao_saida_ffmpeg(tmp_path, "tv-cultura")
    assert padrao == str(tmp_path / "tv-cultura" / "%Y-%m-%d" / "%H%M%S.ts.part")


def test_garantir_pastas_do_dia_cria_hoje_e_amanha(tmp_path):
    garantir_pastas_do_dia(tmp_path, "radio-x", HOJE)

    assert pasta_do_dia(tmp_path, "radio-x", HOJE).is_dir()
    assert pasta_do_dia(tmp_path, "radio-x", HOJE + timedelta(days=1)).is_dir()


def test_garantir_pastas_do_dia_e_idempotente(tmp_path):
    garantir_pastas_do_dia(tmp_path, "radio-x", HOJE)
    garantir_pastas_do_dia(tmp_path, "radio-x", HOJE)  # não pode levantar erro


def _criar_parcial(pasta, nome, conteudo=b"x"):
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome
    caminho.write_bytes(conteudo)
    return caminho


class TestPromoverSegmentosProntos:
    def test_promove_tudo_menos_o_mais_recente(self, tmp_path):
        pasta = pasta_do_dia(tmp_path, "radio-x", HOJE)
        antigo = _criar_parcial(pasta, "150000.ts.part")
        time.sleep(0.01)
        recente = _criar_parcial(pasta, "150400.ts.part")

        promovidos = promover_segmentos_prontos(tmp_path, "radio-x", HOJE)

        assert promovidos == [pasta / "150000.ts"]
        assert not antigo.exists()
        assert (pasta / "150000.ts").exists()
        assert recente.exists()  # o mais recente continua .part — ainda sendo escrito

    def test_um_so_parcial_nao_promove_nada(self, tmp_path):
        pasta = pasta_do_dia(tmp_path, "radio-x", HOJE)
        _criar_parcial(pasta, "150000.ts.part")

        assert promover_segmentos_prontos(tmp_path, "radio-x", HOJE) == []

    def test_sem_nenhum_parcial_nao_quebra(self, tmp_path):
        assert promover_segmentos_prontos(tmp_path, "radio-x", HOJE) == []

    def test_promove_o_ultimo_parcial_de_ontem_na_virada_de_dia(self, tmp_path):
        # cenário real que a checklist da 1.2 pede: o processo é UM SÓ contínuo; o último
        # segmento de ontem antes da virada só é promovido quando o de hoje aparecer.
        ontem = HOJE - timedelta(days=1)
        pasta_ontem = pasta_do_dia(tmp_path, "radio-x", ontem)
        ultimo_de_ontem = _criar_parcial(pasta_ontem, "235600.ts.part")
        time.sleep(0.01)
        pasta_hoje = pasta_do_dia(tmp_path, "radio-x", HOJE)
        primeiro_de_hoje = _criar_parcial(pasta_hoje, "000000.ts.part")

        promovidos = promover_segmentos_prontos(tmp_path, "radio-x", HOJE)

        assert promovidos == [pasta_ontem / "235600.ts"]
        assert not ultimo_de_ontem.exists()
        assert primeiro_de_hoje.exists()  # o mais recente global — ainda .part


class TestUltimoProgressoEm:
    def test_sem_arquivo_nenhum_devolve_none(self, tmp_path):
        assert ultimo_progresso_em(tmp_path, "radio-x", HOJE) is None

    def test_devolve_mtime_do_mais_recente(self, tmp_path):
        pasta = pasta_do_dia(tmp_path, "radio-x", HOJE)
        _criar_parcial(pasta, "150000.ts.part")
        time.sleep(0.01)
        recente = _criar_parcial(pasta, "150400.ts.part")

        progresso = ultimo_progresso_em(tmp_path, "radio-x", HOJE)

        assert progresso == recente.stat().st_mtime

    def test_conta_ts_finalizado_e_part_junto(self, tmp_path):
        pasta = pasta_do_dia(tmp_path, "radio-x", HOJE)
        (pasta).mkdir(parents=True, exist_ok=True)
        (pasta / "150000.ts").write_bytes(b"x")  # já promovido
        time.sleep(0.01)
        part = _criar_parcial(pasta, "150400.ts.part")

        assert ultimo_progresso_em(tmp_path, "radio-x", HOJE) == part.stat().st_mtime


def test_listar_parciais_ordenado_por_mtime(tmp_path):
    pasta = pasta_do_dia(tmp_path, "radio-x", HOJE)
    b = _criar_parcial(pasta, "b.ts.part")
    time.sleep(0.01)
    a = _criar_parcial(pasta, "a.ts.part")  # nome não importa, mtime que ordena

    assert listar_parciais(tmp_path, "radio-x", HOJE) == [b, a]


class TestStartedAtDoArquivo:
    def test_extrai_data_e_hora_do_caminho(self, tmp_path):
        arquivo = pasta_do_dia(tmp_path, "radio-x", HOJE) / "170000.ts"
        assert started_at_do_arquivo(arquivo) == "2026-08-28T17:00:00"

    def test_nome_fora_do_padrao_devolve_none(self, tmp_path):
        arquivo = pasta_do_dia(tmp_path, "radio-x", HOJE) / "qualquer-coisa.ts"
        assert started_at_do_arquivo(arquivo) is None

    def test_arquivo_ainda_part_devolve_none(self, tmp_path):
        # o padrão só reconhece .ts fechado — .ts.part não é "started_at" ainda confirmado
        arquivo = pasta_do_dia(tmp_path, "radio-x", HOJE) / "170000.ts.part"
        assert started_at_do_arquivo(arquivo) is None
