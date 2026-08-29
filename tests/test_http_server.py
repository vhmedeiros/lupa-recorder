"""Servidor HTTP local rodando de verdade num socket efêmero — bate nele com http.client."""

from __future__ import annotations

import http.client
import json
import threading
from datetime import date

from lupa_recorder.http.app import encerrar_servidores, iniciar_servidores
from lupa_recorder.http.auth import assinar_url, query_escopo_assinada

SEGREDO = "segredo-de-teste-com-mais-de-16-chars"
HOJE = date.today().isoformat()


def _req(srv, metodo, caminho, *, corpo=None, headers=None):
    host, porta = srv.server_address
    conn = http.client.HTTPConnection(host, porta, timeout=5)
    try:
        conn.request(metodo, caminho, body=corpo, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def _assinado(caminho: str) -> str:
    """Token de path — status/probe."""
    return assinar_url(SEGREDO, caminho, ttl_s=3600)


def _q(slug: str, data: str) -> str:
    """Token de escopo (fonte, dia) — play/seg/thumbs."""
    return query_escopo_assinada(SEGREDO, slug, data, ttl_s=3600)


def _play(slug: str, data: str) -> str:
    return f"/v1/play/{slug}/{data}.m3u8?{_q(slug, data)}"


def _seg(slug: str, data: str, hhmmss: str) -> str:
    return f"/v1/seg/{slug}/{data}/{hhmmss}.ts?{_q(slug, data)}"


def _thumbs(slug: str, resto: str) -> str:
    data = resto.split("/")[0].removesuffix(".vtt")
    return f"/v1/thumbs/{slug}/{resto}?{_q(slug, data)}"


# ── health (sem auth) ────────────────────────────────────────────────────────


def test_health_responde_sem_token(servidor):
    status, headers, corpo = _req(servidor, "GET", "/v1/health")

    assert status in (200, 503)  # 503 se faltar ffmpeg no PATH — ambos são "vivo"
    dados = json.loads(corpo)
    assert dados["status"] in ("ok", "degraded")
    assert "uptime_s" in dados
    assert headers["content-type"].startswith("application/json")


def test_cors_liberado_em_toda_resposta(servidor):
    # o Estúdio/hls.js buscam de outra origem — sem isso o browser bloqueia tudo
    for metodo, caminho in [("GET", "/v1/health"), ("GET", "/v1/status"), ("OPTIONS", "/v1/seg/x/y")]:
        status, headers, _ = _req(servidor, metodo, caminho)
        assert headers["access-control-allow-origin"] == "*", (metodo, caminho, status)
    status_opt, headers_opt, _ = _req(servidor, "OPTIONS", "/v1/play/radio-x/2020-01-01.m3u8")
    assert status_opt == 204
    assert "Range" in headers_opt["access-control-allow-headers"]


# ── auth ─────────────────────────────────────────────────────────────────────


def test_status_sem_token_da_401(servidor):
    status, _, _ = _req(servidor, "GET", "/v1/status")
    assert status == 401


def test_status_com_token_ok(servidor):
    status, _, corpo = _req(servidor, "GET", _assinado("/v1/status"))

    assert status == 200
    dados = json.loads(corpo)
    assert dados["volumes"]["acervo"]["total"] > 0
    slugs = {f["slug"] for f in dados["fontes"]}
    assert slugs == {"radio-x", "tv-y"}
    radio = next(f for f in dados["fontes"] if f["slug"] == "radio-x")
    assert radio["segmentos"] == 3


# ── playlist ─────────────────────────────────────────────────────────────────


def test_play_dia_corrente_e_event(servidor):
    status, headers, corpo = _req(servidor, "GET", _play("radio-x", HOJE))

    assert status == 200
    assert headers["content-type"] == "application/vnd.apple.mpegurl"
    texto = corpo.decode()
    assert texto.startswith("#EXTM3U")
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in texto
    assert "#EXT-X-ENDLIST" not in texto
    assert texto.count("#EXTINF:") == 3
    assert "/v1/seg/radio-x/" in texto and "e=" in texto  # segmentos vêm assinados


def test_play_dia_passado_e_vod_com_endlist(servidor):
    status, _, corpo = _req(servidor, "GET", _play("radio-x", "2020-01-01"))

    assert status == 200
    texto = corpo.decode()
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in texto
    assert texto.rstrip().endswith("#EXT-X-ENDLIST")


def test_play_fonte_desconhecida_da_404(servidor):
    status, _, _ = _req(servidor, "GET", _play("nao-existe", HOJE))
    assert status == 404


# ── segmentos + Range ────────────────────────────────────────────────────────


def _primeiro_segmento_url(servidor) -> str:
    _, _, corpo = _req(servidor, "GET", _play("radio-x", HOJE))
    for linha in corpo.decode().splitlines():
        if linha.startswith("/v1/seg/"):
            return linha
    raise AssertionError("playlist sem segmento")


def test_segmento_inteiro(servidor):
    url = _primeiro_segmento_url(servidor)
    status, headers, corpo = _req(servidor, "GET", url)

    assert status == 200
    assert headers["content-type"] == "video/mp2t"
    assert headers["accept-ranges"] == "bytes"
    assert int(headers["content-length"]) == len(corpo)
    assert corpo == b"MPEGTS-FAKE" * 100


def test_segmento_com_range(servidor):
    url = _primeiro_segmento_url(servidor)
    status, headers, corpo = _req(servidor, "GET", url, headers={"Range": "bytes=0-9"})

    assert status == 206
    assert corpo == (b"MPEGTS-FAKE" * 100)[:10]
    assert headers["content-range"].startswith("bytes 0-9/")
    assert headers["content-length"] == "10"


def test_segmento_range_invalido_da_416(servidor):
    url = _primeiro_segmento_url(servidor)
    status, headers, _ = _req(servidor, "GET", url, headers={"Range": "bytes=999999-"})

    assert status == 416
    assert headers["content-range"].startswith("bytes */")


def test_segmento_inexistente_da_404(servidor):
    status, _, _ = _req(servidor, "GET", _seg("radio-x", HOJE, "235900"))
    assert status == 404


# ── miniaturas ───────────────────────────────────────────────────────────────


def test_vtt_do_dia_assina_as_urls_das_imagens(servidor):
    # URL canônica sem pasta do dia — o servidor traduz pra {data}/{data}.vtt no disco
    status, headers, corpo = _req(servidor, "GET", _thumbs("tv-y", f"{HOJE}.vtt"))

    assert status == 200
    assert headers["content-type"].startswith("text/vtt")
    texto = corpo.decode()
    assert texto.startswith("WEBVTT")
    # cada URL de imagem no VTT sai assinada (?e=&s= antes do #fragment), senão o player
    # toma 401 em toda imagem da filmstrip
    linha_img = next(ln for ln in texto.splitlines() if ln.startswith("/v1/thumb"))
    assert "?e=" in linha_img and "&s=" in linha_img
    assert linha_img.index("?e=") < linha_img.index("#")  # query antes do fragment

    # e a URL assinada realmente serve o sprite
    url_sprite = linha_img.split("#")[0]
    status_img, headers_img, _ = _req(servidor, "GET", url_sprite)
    assert status_img == 200
    assert headers_img["content-type"] == "image/jpeg"


def test_sprite_de_hora(servidor):
    status, headers, corpo = _req(servidor, "GET", _thumbs("tv-y", f"{HOJE}/sprites/00.jpg"))

    assert status == 200
    assert headers["content-type"] == "image/jpeg"
    assert corpo.startswith(b"\xff\xd8\xff")


def test_thumbs_com_componente_traversal_da_400(servidor):
    status, _, _ = _req(servidor, "GET", _thumbs("tv-y", "a/../b.jpg"))
    assert status == 400


# ── probe / rotas adiadas ────────────────────────────────────────────────────


def test_probe_sem_token_da_401(servidor):
    status, _, _ = _req(servidor, "POST", "/v1/probe", corpo=b'{"url":"http://x"}')
    assert status == 401


def test_probe_com_token_usa_probe_fn(servidor):
    status, _, corpo = _req(
        servidor,
        "POST",
        _assinado("/v1/probe"),
        corpo=json.dumps({"url": "http://exemplo/stream", "sem_captura": True}).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert status == 200
    dados = json.loads(corpo)
    assert dados["url"] == "http://exemplo/stream"
    assert dados["protocolo_detectado"] == "hls"
    assert "cadastro_sugerido" in dados


def test_probe_sem_url_da_400(servidor):
    status, _, _ = _req(servidor, "POST", _assinado("/v1/probe"), corpo=b"{}")
    assert status == 400


def test_scan_e_clip_dao_501(servidor):
    assert _req(servidor, "POST", _assinado("/v1/scan"), corpo=b"{}")[0] == 501
    assert _req(servidor, "POST", _assinado("/v1/clip"), corpo=b"{}")[0] == 501


# ── bind ─────────────────────────────────────────────────────────────────────


def test_iniciar_servidores_nunca_bind_0000(contexto):
    contexto.config.agent.http.port = 0  # porta efêmera — não colide com serviço real
    servidores = iniciar_servidores(contexto)  # ambiente de teste: bind_tailnet = false
    try:
        assert len(servidores) == 1
        assert servidores[0].server_address[0] == "127.0.0.1"
    finally:
        encerrar_servidores(servidores)


def test_encerrar_servidores_retorna_sem_travar(contexto):
    contexto.config.agent.http.port = 0
    antes = threading.active_count()
    servidores = iniciar_servidores(contexto)
    assert threading.active_count() > antes  # subiu a thread do serve_forever
    encerrar_servidores(servidores)
