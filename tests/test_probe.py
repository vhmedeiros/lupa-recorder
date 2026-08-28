"""Testa só a lógica pura de probe.py — nada aqui toca rede nem chama ffmpeg/yt-dlp de
verdade (isso foi validado manualmente contra fontes reais, ver notas no PR/commit).
"""

from lupa_recorder.probe import (
    Rendition,
    ResultadoCaptura,
    calcular_projecao,
    desembrulhar_pls,
    detectar_protocolo,
    parsear_master_playlist,
    recomendar_rendition,
    tem_parametro_de_token,
)

MASTER_PLAYLIST_EXEMPLO = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5800000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
1080p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2900000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480,CODECS="avc1.42001f,mp4a.40.2"
480p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.42001e,mp4a.40.2"
360p/index.m3u8
"""


class TestDetectarProtocolo:
    def test_hls(self):
        assert detectar_protocolo("https://exemplo.com/live/stream.m3u8") == "hls"

    def test_pls(self):
        assert detectar_protocolo("https://exemplo.com/radio.pls") == "pls"

    def test_youtube_dominio_completo(self):
        assert detectar_protocolo("https://www.youtube.com/watch?v=abc") == "youtube"

    def test_youtube_dominio_curto(self):
        assert detectar_protocolo("https://youtu.be/abc123") == "youtube"

    def test_rtsp(self):
        assert detectar_protocolo("rtsp://192.168.1.10/canal1") == "rtsp"

    def test_http_progressivo_por_exclusao(self):
        assert detectar_protocolo("http://stream.radio.com.br:8000/live") == "http"


class TestDetectarToken:
    def test_parametro_conhecido(self):
        assert tem_parametro_de_token("https://x.com/stream?zt=abc123")

    def test_valor_longo_tipo_jwt(self):
        url = "https://x.com/stream?zt=eyJhbGciOiJIUzI1NiJ9.eyJzdHJlYW0iOiJ2bGNyYWlqYzZ5aXV2In0.6sohqebMxyc6OV"
        assert tem_parametro_de_token(url)

    def test_sem_token(self):
        assert not tem_parametro_de_token("https://stream01.ouveai.com.br:1072/stream")

    def test_query_curta_nao_conta_como_token(self):
        assert not tem_parametro_de_token("https://x.com/stream?vhost=player.uol.com.br")

    def test_hostname_longo_sem_digito_nao_conta_como_token(self):
        # achado real (TV Cultura): vhost=player-tvcultura.stream.uol.com.br tem >20 chars
        # e batia na heurística genérica antes do ajuste — é hostname, não token.
        url = "https://x.com/live.m3u8?vhost=player-tvcultura.stream.uol.com.br"
        assert not tem_parametro_de_token(url)


class TestParsearMasterPlaylist:
    def test_extrai_quatro_renditions(self):
        renditions = parsear_master_playlist(
            MASTER_PLAYLIST_EXEMPLO, "https://cdn.exemplo.com.br/live/master.m3u8"
        )
        assert len(renditions) == 4

    def test_bandwidth_e_resolucao_corretos(self):
        renditions = parsear_master_playlist(MASTER_PLAYLIST_EXEMPLO, "https://cdn.exemplo.com.br/live/master.m3u8")
        maior = max(renditions, key=lambda r: r.bandwidth_bps)
        assert maior.bandwidth_bps == 5_800_000
        assert maior.resolution == "1920x1080"
        assert maior.altura == 1080

    def test_uri_relativa_vira_absoluta(self):
        renditions = parsear_master_playlist(MASTER_PLAYLIST_EXEMPLO, "https://cdn.exemplo.com.br/live/master.m3u8")
        assert all(r.uri.startswith("https://cdn.exemplo.com.br/live/") for r in renditions)

    def test_uri_absoluta_no_playlist_fica_como_esta(self):
        texto = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
https://outro-cdn.com/variante.m3u8
"""
        renditions = parsear_master_playlist(texto, "https://cdn.exemplo.com.br/live/master.m3u8")
        assert renditions[0].uri == "https://outro-cdn.com/variante.m3u8"

    def test_playlist_de_variante_unica_sem_stream_inf_devolve_vazio(self):
        # master "achatado" (renditions.md do achado real: TV Cultura só tem 1 opção)
        texto = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg001.ts\n"
        assert parsear_master_playlist(texto, "https://x.com/live/master.m3u8") == []


class TestRecomendarRendition:
    def test_recomenda_a_menor_com_altura_maior_igual_480(self):
        renditions = parsear_master_playlist(MASTER_PLAYLIST_EXEMPLO, "https://x.com/live/master.m3u8")
        recomendada = recomendar_rendition(renditions)
        assert recomendada.resolution == "854x480"

    def test_sem_nenhuma_480_cai_pra_menor_disponivel(self):
        renditions = [
            Rendition(uri="a", bandwidth_bps=2_000_000, resolution="1280x720"),
            Rendition(uri="b", bandwidth_bps=5_000_000, resolution="1920x1080"),
        ]
        recomendada = recomendar_rendition(renditions)
        assert recomendada.resolution == "1280x720"

    def test_rendition_unica_sem_resolucao_ainda_e_recomendada(self):
        # caso real: TV Cultura tem 1 rendition só, sem info de RESOLUTION explícita
        renditions = [Rendition(uri="a", bandwidth_bps=963_000, resolution=None)]
        assert recomendar_rendition(renditions) is renditions[0]

    def test_lista_vazia_devolve_none(self):
        assert recomendar_rendition([]) is None


class TestDesembrulharPls:
    def test_extrai_file1(self):
        texto = "[playlist]\nNumberOfEntries=1\nFile1=http://exemplo.com/stream.mp3\nTitle1=Radio\n"
        assert desembrulhar_pls(texto) == "http://exemplo.com/stream.mp3"

    def test_sem_file_devolve_none(self):
        assert desembrulhar_pls("[playlist]\nNumberOfEntries=0\n") is None


class TestProjecaoDeDisco:
    def test_cabe(self):
        projecao, cabe = calcular_projecao(gb_por_dia=10, dias=5, disco_livre_gb=100)
        assert projecao == 50
        assert cabe is True

    def test_nao_cabe(self):
        projecao, cabe = calcular_projecao(gb_por_dia=200, dias=5, disco_livre_gb=880)
        assert projecao == 1000
        assert cabe is False


class TestResultadoCaptura:
    def test_bitrate_e_projecao(self):
        # 1.38 Mbps medido de verdade na TV Cultura (bench.md) — confere a conta com esse número real
        captura = ResultadoCaptura(duracao_s=20, bytes_escritos=int(1_380_000 / 8 * 20))
        assert 1_370_000 < captura.bitrate_bps < 1_390_000
        assert 13.8 < captura.gb_por_dia_real < 14.0

    def test_duracao_zero_nao_quebra(self):
        assert ResultadoCaptura(duracao_s=0, bytes_escritos=100).bitrate_bps == 0.0
