import pytest

from lupa_recorder.capture.strategies import (
    HlsStrategy,
    HttpProgressiveStrategy,
    RtspStrategy,
    StrategyError,
    YoutubeStrategy,
    criar_estrategia,
)
from lupa_recorder.config import SourceConfig
from lupa_recorder.resolve.base import ResolvedInput

FONTE_RADIO_HTTP = {
    "id": 1,
    "slug": "radio-teste",
    "kind": "radio",
    "protocol": "http",
    "url": "http://exemplo.com/stream",
}

FONTE_TV_HLS = {
    "id": 2,
    "slug": "tv-teste",
    "kind": "tv",
    "protocol": "hls",
    "url": "https://exemplo.com/live.m3u8",
}

FONTE_TV_YOUTUBE = {
    "id": 3,
    "slug": "tv-youtube-teste",
    "kind": "tv",
    "protocol": "youtube",
    "url": "https://youtube.com/watch?v=abc",
    "url_resolver": "yt_dlp",
}


class TestHlsStrategy:
    def test_nunca_usa_reconnect(self):
        estrategia = HlsStrategy(SourceConfig(**FONTE_TV_HLS))
        cmd = estrategia.build_input(ResolvedInput(urls=["https://x.com/live.m3u8"]))
        assert "-reconnect" not in cmd
        assert cmd == ["-i", "https://x.com/live.m3u8"]

    def test_map_args_tv(self):
        estrategia = HlsStrategy(SourceConfig(**FONTE_TV_HLS))
        assert estrategia.map_args() == ["-map", "0:v:0", "-map", "0:a:0"]


class TestHttpProgressiveStrategy:
    def test_usa_reconnect(self):
        estrategia = HttpProgressiveStrategy(SourceConfig(**FONTE_RADIO_HTTP))
        cmd = estrategia.build_input(ResolvedInput(urls=["http://exemplo.com/stream"]))
        assert "-reconnect" in cmd
        assert cmd[-2:] == ["-i", "http://exemplo.com/stream"]

    def test_map_args_radio_so_audio(self):
        estrategia = HttpProgressiveStrategy(SourceConfig(**FONTE_RADIO_HTTP))
        assert estrategia.map_args() == ["-map", "0:a:0"]


class TestRtspStrategy:
    def test_usa_rtsp_transport_tcp(self):
        fonte = SourceConfig(**{**FONTE_TV_HLS, "protocol": "rtsp", "url": "rtsp://exemplo.com/canal"})
        estrategia = RtspStrategy(fonte)
        cmd = estrategia.build_input(ResolvedInput(urls=["rtsp://exemplo.com/canal"]))
        assert "-rtsp_transport" in cmd
        assert "tcp" in cmd


class TestYoutubeStrategy:
    def test_duas_entradas_com_thread_queue_size(self):
        estrategia = YoutubeStrategy(SourceConfig(**FONTE_TV_YOUTUBE))
        cmd = estrategia.build_input(ResolvedInput(urls=["https://video.url", "https://audio.url"]))
        assert cmd.count("-thread_queue_size") == 2
        assert cmd.count("1024") == 2
        assert "https://video.url" in cmd
        assert "https://audio.url" in cmd

    def test_map_args_video_e_audio_de_inputs_diferentes(self):
        estrategia = YoutubeStrategy(SourceConfig(**FONTE_TV_YOUTUBE))
        assert estrategia.map_args() == ["-map", "0:v:0", "-map", "1:a:0"]

    def test_numero_errado_de_urls_levanta_erro(self):
        estrategia = YoutubeStrategy(SourceConfig(**FONTE_TV_YOUTUBE))
        with pytest.raises(ValueError, match="2 URLs"):
            estrategia.build_input(ResolvedInput(urls=["só-uma-url"]))


class TestCriarEstrategia:
    @pytest.mark.parametrize(
        ("protocolo", "fonte_base", "classe_esperada"),
        [
            ("hls", FONTE_TV_HLS, HlsStrategy),
            ("http", FONTE_RADIO_HTTP, HttpProgressiveStrategy),
            ("rtsp", {**FONTE_TV_HLS, "url": "rtsp://x.com/c"}, RtspStrategy),
            ("youtube", FONTE_TV_YOUTUBE, YoutubeStrategy),
        ],
    )
    def test_mapeia_protocolo_pra_estrategia_certa(self, protocolo, fonte_base, classe_esperada):
        fonte = SourceConfig(**{**fonte_base, "protocol": protocolo})
        assert isinstance(criar_estrategia(fonte), classe_esperada)

    def test_protocolo_sem_estrategia_levanta_erro_claro(self):
        # dvb é rejeitado no cadastro (SourceConfig) antes de chegar aqui — simulamos um
        # protocolo desconhecido só pra confirmar que criar_estrategia() não engole em
        # silêncio um caso não mapeado.
        fonte = SourceConfig(**FONTE_TV_HLS).model_copy(update={"protocol": "desconhecido"})
        with pytest.raises(StrategyError, match="sem estratégia"):
            criar_estrategia(fonte)


async def test_preflight_e_teardown_sao_no_op_por_padrao():
    estrategia = HlsStrategy(SourceConfig(**FONTE_TV_HLS))
    await estrategia.preflight()
    await estrategia.teardown()
