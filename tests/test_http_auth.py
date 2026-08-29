"""Tokens HMAC da query (`?e=&s=`) — puros, relógio injetado.

Dois escopos: de path (status/probe) e de conteúdo `(fonte, dia)` (play/seg/thumbs).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from lupa_recorder.http.auth import (
    assinar,
    assinar_url,
    query_escopo_assinada,
    verificar,
    verificar_escopo,
)

SEGREDO = "segredo-de-teste-com-mais-de-16-chars"


def _params(query_ou_url: str) -> dict[str, str]:
    query = urlsplit(query_ou_url).query or query_ou_url
    return {k: v[0] for k, v in parse_qs(query).items()}


# ── escopo de path (status, probe) ───────────────────────────────────────────


class TestTokenDePath:
    def test_roundtrip(self):
        url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)
        assert verificar(SEGREDO, "/v1/status", _params(url), agora=1000)
        assert verificar(SEGREDO, "/v1/status", _params(url), agora=1000 + 3599)

    def test_expirado(self):
        url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)
        assert not verificar(SEGREDO, "/v1/status", _params(url), agora=1000 + 3600)

    def test_path_diferente(self):
        url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)
        assert not verificar(SEGREDO, "/v1/probe", _params(url), agora=1000)

    def test_assinatura_adulterada(self):
        url = assinar_url(SEGREDO, "/v1/status", ttl_s=3600, agora=1000)
        p = _params(url)
        p["s"] = p["s"][:-1] + ("0" if p["s"][-1] != "0" else "1")
        assert not verificar(SEGREDO, "/v1/status", p, agora=1000)

    def test_params_ausentes_ou_malformados(self):
        assert not verificar(SEGREDO, "/v1/status", {}, agora=1000)
        assert not verificar(SEGREDO, "/v1/status", {"e": "1000"}, agora=1000)
        assert not verificar(
            SEGREDO, "/v1/status", {"e": "xx", "s": assinar(SEGREDO, "/v1/status", 2000)}, agora=1000
        )


# ── escopo de conteúdo (play, seg, thumbs) ───────────────────────────────────


class TestTokenDeEscopo:
    def test_um_token_vale_pra_fonte_e_dia_inteiros(self):
        # o mesmo ?e=&s= autoriza a playlist e todo segmento/miniatura daquele dia
        q = query_escopo_assinada(SEGREDO, "tv-cultura", "2026-08-29", ttl_s=3600, agora=1000)
        p = _params(q)
        assert verificar_escopo(SEGREDO, "tv-cultura", "2026-08-29", p, agora=1000)
        assert verificar_escopo(SEGREDO, "tv-cultura", "2026-08-29", p, agora=1000 + 3599)

    def test_estavel_entre_recargas_no_mesmo_instante(self):
        # a playlist EVENT ecoa esse token — precisa ser idêntico byte a byte
        a = query_escopo_assinada(SEGREDO, "tv-cultura", "2026-08-29", ttl_s=3600, agora=1000)
        b = query_escopo_assinada(SEGREDO, "tv-cultura", "2026-08-29", ttl_s=3600, agora=1000)
        assert a == b

    def test_fonte_ou_dia_diferente_nao_vale(self):
        q = query_escopo_assinada(SEGREDO, "tv-cultura", "2026-08-29", ttl_s=3600, agora=1000)
        p = _params(q)
        assert not verificar_escopo(SEGREDO, "radio-ouveai", "2026-08-29", p, agora=1000)
        assert not verificar_escopo(SEGREDO, "tv-cultura", "2026-08-28", p, agora=1000)

    def test_expirado(self):
        q = query_escopo_assinada(SEGREDO, "tv-cultura", "2026-08-29", ttl_s=3600, agora=1000)
        assert not verificar_escopo(SEGREDO, "tv-cultura", "2026-08-29", _params(q), agora=1000 + 3600)

    def test_segredo_diferente(self):
        q = query_escopo_assinada(SEGREDO, "tv-cultura", "2026-08-29", ttl_s=3600, agora=1000)
        assert not verificar_escopo(
            "outro-segredo-de-16-caracteres", "tv-cultura", "2026-08-29", _params(q), agora=1000
        )

    def test_token_de_path_nao_serve_como_escopo(self):
        url = assinar_url(SEGREDO, "/v1/play/tv-cultura/2026-08-29.m3u8", ttl_s=3600, agora=1000)
        assert not verificar_escopo(SEGREDO, "tv-cultura", "2026-08-29", _params(url), agora=1000)
