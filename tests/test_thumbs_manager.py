from datetime import datetime

from PIL import Image

from lupa_recorder.thumbs.manager import (
    gerar_vtt_do_dia,
    montar_sprites_pendentes,
    pasta_sprites_do_dia,
    pasta_thumbs_do_dia,
)

HOJE_16H30 = datetime(2026, 8, 28, 16, 30, 0)


def _criar_miniaturas_da_hora(system_root, slug, quando, hora, minutos=range(60)):
    pasta = pasta_thumbs_do_dia(system_root, slug, quando)
    pasta.mkdir(parents=True, exist_ok=True)
    for m in minutos:
        nome = f"{hora:02d}{m:02d}00_000.jpg"
        Image.new("RGB", (160, 90)).save(pasta / nome, "JPEG")


class TestMontarSpritesPendentes:
    def test_monta_sprite_so_das_horas_ja_fechadas(self, tmp_path):
        _criar_miniaturas_da_hora(tmp_path, "tv-x", HOJE_16H30, hora=14)
        _criar_miniaturas_da_hora(tmp_path, "tv-x", HOJE_16H30, hora=16)  # hora corrente

        montados = montar_sprites_pendentes(tmp_path, "tv-x", HOJE_16H30)

        assert len(montados) == 1
        assert montados[0].name == "14.jpg"
        assert not (pasta_sprites_do_dia(tmp_path, "tv-x", HOJE_16H30) / "16.jpg").exists()

    def test_idempotente_nao_remonta_sprite_ja_pronto(self, tmp_path):
        _criar_miniaturas_da_hora(tmp_path, "tv-x", HOJE_16H30, hora=14)

        montar_sprites_pendentes(tmp_path, "tv-x", HOJE_16H30)
        segunda_vez = montar_sprites_pendentes(tmp_path, "tv-x", HOJE_16H30)

        assert segunda_vez == []

    def test_sem_pasta_nenhuma_devolve_vazio(self, tmp_path):
        assert montar_sprites_pendentes(tmp_path, "tv-inexistente", HOJE_16H30) == []


class TestGerarVttDoDia:
    def test_horas_fechadas_usam_sprite_hora_atual_usa_avulsas(self, tmp_path):
        _criar_miniaturas_da_hora(tmp_path, "tv-x", HOJE_16H30, hora=14)
        _criar_miniaturas_da_hora(tmp_path, "tv-x", HOJE_16H30, hora=16, minutos=range(31))
        montar_sprites_pendentes(tmp_path, "tv-x", HOJE_16H30)

        vtt = gerar_vtt_do_dia(tmp_path, "tv-x", HOJE_16H30)

        assert vtt.startswith("WEBVTT")
        assert "sprites/14.jpg" in vtt
        assert "14:00:00.000" in vtt  # cue da hora 14 desloca certo, não começa em 00:00
        assert "16:00:00.000" in vtt  # hora corrente, avulsa
        assert "160000_000.jpg" in vtt or "160000" in vtt

    def test_sem_nenhuma_miniatura_devolve_vtt_vazio_valido(self, tmp_path):
        vtt = gerar_vtt_do_dia(tmp_path, "tv-sem-dado", HOJE_16H30)
        assert vtt.startswith("WEBVTT")
