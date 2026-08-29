"""Playlist HLS sintética — geração pura (plano §20 pede 100% de cobertura aqui)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lupa_recorder.http.playlist import EntradaSegmento, montar_playlist

TZ = timezone(timedelta(hours=-3))


def _entradas(inicio: datetime, quantidade: int, *, passo_s: int = 240, duration_ms=None):
    return [
        EntradaSegmento(
            started_at=inicio + timedelta(seconds=i * passo_s),
            url=f"/v1/seg/x/{(inicio + timedelta(seconds=i * passo_s)):%Y-%m-%d}/"
            f"{(inicio + timedelta(seconds=i * passo_s)):%H%M%S}.ts?e=1&s=a",
            duration_ms=duration_ms,
        )
        for i in range(quantidade)
    ]


def test_dia_passado_e_vod_com_endlist():
    m3u8 = montar_playlist(
        _entradas(datetime(2026, 8, 28, 7, 0, tzinfo=TZ), 3),
        dia_corrente=False,
        segment_seconds=240,
    )

    assert "#EXT-X-PLAYLIST-TYPE:VOD" in m3u8
    assert m3u8.rstrip().endswith("#EXT-X-ENDLIST")
    assert m3u8.count("#EXTINF:") == 3


def test_dia_corrente_e_event_sem_endlist():
    m3u8 = montar_playlist(
        _entradas(datetime(2026, 8, 28, 0, 0, tzinfo=TZ), 2),
        dia_corrente=True,
        segment_seconds=240,
        agora=datetime(2026, 8, 28, 0, 7, 30, tzinfo=TZ),
    )

    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in m3u8
    assert "#EXT-X-ENDLIST" not in m3u8


def test_cada_segmento_tem_pdt_e_toda_borda_e_discontinuity():
    # os .ts são capturados com -reset_timestamps e cortados no relógio → timelines
    # independentes; sem DISCONTINUITY em cada borda o hls.js trava (bufferStalledError)
    m3u8 = montar_playlist(
        _entradas(datetime(2026, 8, 28, 7, 0, tzinfo=TZ), 3),
        dia_corrente=False,
        segment_seconds=240,
    )

    assert m3u8.count("#EXT-X-PROGRAM-DATE-TIME:") == 3  # um por segmento
    assert m3u8.count("#EXT-X-DISCONTINUITY\n") == 2  # entre os 3 segmentos (não antes do 1º)
    assert "#EXT-X-PROGRAM-DATE-TIME:2026-08-28T07:00:00.000-03:00" in m3u8
    assert "#EXT-X-PROGRAM-DATE-TIME:2026-08-28T07:04:00.000-03:00" in m3u8


def test_playlist_vazia_ainda_e_valida():
    passado = montar_playlist([], dia_corrente=False, segment_seconds=240)
    corrente = montar_playlist([], dia_corrente=True, segment_seconds=240)

    assert passado.startswith("#EXTM3U")
    assert "#EXT-X-ENDLIST" in passado
    assert "#EXT-X-ENDLIST" not in corrente
    assert "#EXT-X-TARGETDURATION:240" in passado


def test_duracao_vem_do_espacamento_quando_contiguo():
    m3u8 = montar_playlist(
        _entradas(datetime(2026, 8, 28, 7, 0, tzinfo=TZ), 2, passo_s=240),
        dia_corrente=False,
        segment_seconds=240,
    )
    assert "#EXTINF:240.000," in m3u8


def test_duracao_medida_tem_prioridade():
    m3u8 = montar_playlist(
        _entradas(datetime(2026, 8, 28, 7, 0, tzinfo=TZ), 2, duration_ms=238500),
        dia_corrente=False,
        segment_seconds=240,
    )
    assert "#EXTINF:238.500," in m3u8


def test_buraco_grande_cai_no_nominal_nao_no_delta():
    a = EntradaSegmento(datetime(2026, 8, 28, 7, 0, tzinfo=TZ), "/s/a.ts")
    b = EntradaSegmento(datetime(2026, 8, 28, 7, 4, tzinfo=TZ), "/s/b.ts")
    c = EntradaSegmento(datetime(2026, 8, 28, 7, 24, tzinfo=TZ), "/s/c.ts")  # 20 min depois

    m3u8 = montar_playlist([a, b, c], dia_corrente=False, segment_seconds=240)

    assert "#EXTINF:1200.000," not in m3u8  # o segmento antes do buraco fica no nominal
    assert "#EXTINF:240.000," in m3u8
    # o buraco aparece na timeline pelo salto do PROGRAM-DATE-TIME (7:04 → 7:24)
    assert "#EXT-X-PROGRAM-DATE-TIME:2026-08-28T07:24:00.000-03:00" in m3u8


def test_ultimo_segmento_do_event_limitado_por_agora():
    inicio = datetime(2026, 8, 28, 12, 0, tzinfo=TZ)
    m3u8 = montar_playlist(
        [EntradaSegmento(inicio, "/s/a.ts")],
        dia_corrente=True,
        segment_seconds=240,
        agora=inicio + timedelta(seconds=95),
    )
    assert "#EXTINF:95.000," in m3u8


def test_targetduration_cobre_o_maior_extinf():
    m3u8 = montar_playlist(
        _entradas(datetime(2026, 8, 28, 7, 0, tzinfo=TZ), 2, duration_ms=241900),
        dia_corrente=False,
        segment_seconds=240,
    )
    assert "#EXT-X-TARGETDURATION:242" in m3u8
