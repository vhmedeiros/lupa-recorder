from pathlib import Path

from PIL import Image

from lupa_recorder.thumbs.sprite import (
    CELULAS_POR_SPRITE,
    gerar_cues_avulsas,
    gerar_cues_sprite,
    montar_sprite_da_hora,
    montar_webvtt,
)


def _criar_miniatura_de_teste(caminho: Path, cor=(255, 0, 0)) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), color=cor).save(caminho, "JPEG")
    return caminho


class TestMontarSpriteDaHora:
    def test_sprite_60_miniaturas_tem_o_tamanho_certo(self, tmp_path):
        miniaturas = [_criar_miniatura_de_teste(tmp_path / f"m{i}.jpg") for i in range(60)]
        destino = tmp_path / "sprites" / "17.jpg"

        montar_sprite_da_hora(miniaturas, destino)

        assert destino.exists()
        with Image.open(destino) as sprite:
            assert sprite.size == (1600, 540)  # 160×10, 90×6

    def test_hora_incompleta_nao_quebra(self, tmp_path):
        # 20 miniaturas em vez de 60 — comum (fonte começou no meio da hora, alguma
        # extração falhou) — as células que sobram ficam em branco, não é erro.
        miniaturas = [_criar_miniatura_de_teste(tmp_path / f"m{i}.jpg") for i in range(20)]
        destino = tmp_path / "sprites" / "17.jpg"

        montar_sprite_da_hora(miniaturas, destino)

        with Image.open(destino) as sprite:
            assert sprite.size == (1600, 540)

    def test_mais_de_60_ignora_o_excedente(self, tmp_path):
        miniaturas = [_criar_miniatura_de_teste(tmp_path / f"m{i}.jpg") for i in range(70)]
        assert len(miniaturas) > CELULAS_POR_SPRITE

        destino = montar_sprite_da_hora(miniaturas, tmp_path / "sprites" / "17.jpg")

        with Image.open(destino) as sprite:
            assert sprite.size == (1600, 540)  # não cresce além da grade 10×6


class TestCues:
    def test_gerar_cues_sprite_sem_offset(self):
        cues = gerar_cues_sprite("http://x/sprite.jpg", quantidade=2)
        assert cues[0] == "00:00:00.000 --> 00:01:00.000\nhttp://x/sprite.jpg#xywh=0,0,160,90"
        assert cues[1] == "00:01:00.000 --> 00:02:00.000\nhttp://x/sprite.jpg#xywh=160,0,160,90"

    def test_gerar_cues_sprite_com_offset_de_hora(self):
        # achado ao pensar no manager.py: sem offset, toda hora começaria em 00:00 e as
        # cues de horas diferentes se sobrepunham no VTT do dia inteiro.
        cues = gerar_cues_sprite("http://x/17.jpg", quantidade=1, offset_s=17 * 3600)
        assert cues[0].startswith("17:00:00.000 --> 17:01:00.000")

    def test_gerar_cues_avulsas_com_offset(self):
        cues = gerar_cues_avulsas(["http://x/a.jpg", "http://x/b.jpg"], offset_s=3600)
        assert cues[0].startswith("01:00:00.000 --> 01:01:00.000")
        assert cues[1].startswith("01:01:00.000 --> 01:02:00.000")

    def test_cue_da_decima_primeira_coluna_pula_pra_segunda_linha(self):
        cues = gerar_cues_sprite("http://x/s.jpg", quantidade=11)
        assert cues[10].endswith("#xywh=0,90,160,90")  # índice 10 = coluna 0, linha 1


def test_montar_webvtt():
    vtt = montar_webvtt(["00:00:00.000 --> 00:01:00.000\nhttp://x/a.jpg"])
    assert vtt.startswith("WEBVTT\n\n")
    assert "http://x/a.jpg" in vtt


def test_montar_webvtt_sem_cues():
    assert montar_webvtt([]) == "WEBVTT\n"
