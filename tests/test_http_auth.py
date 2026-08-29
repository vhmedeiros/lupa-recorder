"""Token HMAC da query (`?e=&s=`) — puro, relógio injetado."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from lupa_recorder.http.auth import assinar, assinar_url, verificar

SEGREDO = "segredo-de-teste-com-mais-de-16-chars"


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_roundtrip_assina_e_verifica():
    url = assinar_url(SEGREDO, "/v1/play/radio-x/2026-08-29.m3u8", ttl_s=3600, agora=1000)
    caminho = urlsplit(url).path

    assert verificar(SEGREDO, caminho, _params(url), agora=1000)
    assert verificar(SEGREDO, caminho, _params(url), agora=1000 + 3599)


def test_token_expirado_recusado():
    url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)

    assert not verificar(SEGREDO, "/v1/status", _params(url), agora=1000 + 3600)
    assert not verificar(SEGREDO, "/v1/status", _params(url), agora=1000 + 9999)


def test_assinatura_adulterada_recusada():
    url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)
    params = _params(url)
    params["s"] = params["s"][:-1] + ("0" if params["s"][-1] != "0" else "1")

    assert not verificar(SEGREDO, "/v1/status", params, agora=1000)


def test_caminho_diferente_do_assinado_recusado():
    url = assinar_url(SEGREDO, "/v1/seg/radio-x/2026-08-29/000000.ts", ttl_s=3600, agora=1000)

    assert not verificar(SEGREDO, "/v1/seg/radio-x/2026-08-29/000400.ts", _params(url), agora=1000)


def test_segredo_diferente_recusado():
    url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)

    assert not verificar("outro-segredo-de-16-caracteres-ok", "/v1/status", _params(url), agora=1000)


def test_params_ausentes_ou_malformados_recusados():
    assert not verificar(SEGREDO, "/v1/status", {}, agora=1000)
    assert not verificar(SEGREDO, "/v1/status", {"e": "1000"}, agora=1000)
    assert not verificar(SEGREDO, "/v1/status", {"s": "abc"}, agora=1000)
    assert not verificar(
        SEGREDO, "/v1/status", {"e": "nao-e-numero", "s": assinar(SEGREDO, "/v1/status", 2000)}, agora=1000
    )
