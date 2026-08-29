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


def _por_minuto(tmp_path, minutos) -> dict[int, Path]:
    return {m: _criar_miniatura_de_teste(tmp_path / f"m{m}.jpg") for m in minutos}


class TestMontarSpriteDaHora:
    def test_sprite_hora_cheia_tem_o_tamanho_certo(self, tmp_path):
        montar_sprite_da_hora(_por_minuto(tmp_path, range(60)), tmp_path / "sprites" / "17.jpg")

        with Image.open(tmp_path / "sprites" / "17.jpg") as sprite:
            assert sprite.size == (1600, 540)  # 160×10, 90×6

    def test_hora_incompleta_nao_quebra(self, tmp_path):
        # frames só a partir do minuto 22 (fonte começou no meio da hora) — as células
        # 0-21 ficam em branco, não é erro; o sprite continua 10×6.
        montar_sprite_da_hora(_por_minuto(tmp_path, range(22, 60)), tmp_path / "sprites" / "17.jpg")

        with Image.open(tmp_path / "sprites" / "17.jpg") as sprite:
            assert sprite.size == (1600, 540)

    def test_minuto_fora_de_0_59_e_ignorado(self, tmp_path):
        destino = montar_sprite_da_hora(_por_minuto(tmp_path, [0, 59, 60, 999]), tmp_path / "s.jpg")

        with Image.open(destino) as sprite:
            assert sprite.size == (1600, 540)  # não cresce além da grade


class TestCues:
    def test_gerar_cues_sprite_sem_offset(self):
        cues = gerar_cues_sprite("http://x/sprite.jpg", quantidade=2)
        assert cues[0] == "00:00:00.000 --> 00:01:00.000\nhttp://x/sprite.jpg#xywh=0,0,160,90"
        assert cues[1] == "00:01:00.000 --> 00:02:00.000\nhttp://x/sprite.jpg#xywh=160,0,160,90"

    def test_gerar_cues_sprite_com_offset_de_hora(self):
        cues = gerar_cues_sprite("http://x/17.jpg", quantidade=1, offset_s=17 * 3600)
        assert cues[0].startswith("17:00:00.000 --> 17:01:00.000")

    def test_gerar_cues_avulsas_mapeia_pelo_minuto_real(self):
        # minuto 47 → cue às 13:47, não sequencial a partir de 0 (bug de campo 2026-08-29)
        cues = gerar_cues_avulsas({47: "http://x/a.jpg", 48: "http://x/b.jpg"}, offset_s=13 * 3600)
        assert cues[0].startswith("13:47:00.000 --> 13:48:00.000")
        assert cues[1].startswith("13:48:00.000 --> 13:49:00.000")

    def test_cue_da_decima_primeira_coluna_pula_pra_segunda_linha(self):
        cues = gerar_cues_sprite("http://x/s.jpg", quantidade=11)
        assert cues[10].endswith("#xywh=0,90,160,90")  # índice 10 = coluna 0, linha 1


def test_montar_webvtt():
    vtt = montar_webvtt(["00:00:00.000 --> 00:01:00.000\nhttp://x/a.jpg"])
    assert vtt.startswith("WEBVTT\n\n")
    assert "http://x/a.jpg" in vtt


def test_montar_webvtt_sem_cues():
    assert montar_webvtt([]) == "WEBVTT\n"


def test_celulas_por_sprite_e_60():
    assert CELULAS_POR_SPRITE == 60
