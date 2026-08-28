from pathlib import Path

from lupa_recorder.thumbs.extract import (
    extrair_miniaturas_do_segmento,
    nome_arquivo_miniatura,
    offsets_dos_seeks,
)


class TestOffsetsDosSeeks:
    def test_segmento_de_240s_gera_4_offsets(self):
        assert offsets_dos_seeks(240) == [0, 60, 120, 180]

    def test_segmento_de_60s_gera_1_offset(self):
        assert offsets_dos_seeks(60) == [0]

    def test_cadencia_customizada(self):
        assert offsets_dos_seeks(120, cadencia_s=30) == [0, 30, 60, 90]


def test_nome_arquivo_miniatura():
    assert nome_arquivo_miniatura(Path("/x/170000.ts"), 120) == "170000_120.jpg"


class TestExtrairMiniaturasDoSegmento:
    def test_extrator_falso_gera_arquivos_esperados(self, tmp_path):
        segmento = tmp_path / "170000.ts"
        segmento.write_bytes(b"x")
        destino_dir = tmp_path / "thumbs"

        def extrator_falso(seg, offset, destino):
            destino.write_bytes(b"jpeg-fake")
            return True

        geradas = extrair_miniaturas_do_segmento(segmento, destino_dir, 240, extrator=extrator_falso)

        assert len(geradas) == 4
        assert all(g.exists() for g in geradas)

    def test_extrator_que_falha_nao_quebra_e_pula_o_resto(self, tmp_path):
        segmento = tmp_path / "170000.ts"
        segmento.write_bytes(b"x")

        def extrator_que_falha_na_segunda(seg, offset, destino):
            if offset == 60:
                raise RuntimeError("simulação de erro")
            destino.write_bytes(b"jpeg-fake")
            return True

        geradas = extrair_miniaturas_do_segmento(
            segmento, tmp_path / "thumbs", 240, extrator=extrator_que_falha_na_segunda
        )

        assert len(geradas) == 3  # 4 offsets, 1 falhou, 3 sobraram

    def test_extrator_que_devolve_false_nao_conta(self, tmp_path):
        geradas = extrair_miniaturas_do_segmento(
            tmp_path / "170000.ts", tmp_path / "thumbs", 240, extrator=lambda s, o, d: False
        )
        assert geradas == []
