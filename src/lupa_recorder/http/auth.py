"""Token HMAC de curta duração na query string — `?e=<expira>&s=<hmac_hex>` (plano §11.3).

Na query, e não em header, porque nem o `hls.js` nem as `<img>` da filmstrip mandam
header customizado em cada request de segmento/sprite.

**Dois escopos de token:**

- **Escopo de conteúdo** (`assinar_escopo`) — assina `(fonte, dia)`. **Um** token vale pra
  playlist, todos os segmentos e todas as miniaturas daquele dia daquela fonte. A playlist
  e o VTT **ecoam o mesmo token** que o cliente usou pra buscá-los — então as URLs são
  idênticas em toda recarga da playlist `EVENT` (senão o player acha que a lista "deslizou"
  e re-baixa tudo — bug pego na validação de campo 2026-08-29). Na Fase 2 a Lupa emite esse
  token com o segredo negociado via `/api/recorders/me/config`.
- **Escopo de path** (`assinar`) — assina o caminho exato. Pros endpoints sem `(fonte, dia)`
  natural: `/v1/status`, `/v1/probe`.

`/v1/health` não pede token (liveness pro monitoramento).

Puro: sem I/O, relógio injetável — `test_http_auth.py` cobre 100%.
"""

from __future__ import annotations

import hashlib
import hmac
import time

TTL_PADRAO_S = 6 * 3600  # 6h — mesma vida do token que a Lupa emite na Fase 2 (plano §11.5)


def _hmac(secret: str, mensagem: str) -> str:
    return hmac.new(secret.encode(), mensagem.encode(), hashlib.sha256).hexdigest()


def _validar_e_extrair_expira(params: dict[str, str], agora: int | None) -> int | None:
    agora = int(time.time()) if agora is None else agora
    e_bruto, assinatura = params.get("e"), params.get("s")
    if not e_bruto or not assinatura:
        return None
    try:
        expira_em = int(e_bruto)
    except ValueError:
        return None
    return expira_em if agora < expira_em else None


# ── escopo de path (status, probe) ───────────────────────────────────────────


def assinar(secret: str, path: str, expira_em: int) -> str:
    return _hmac(secret, f"{path}\n{expira_em}")


def assinar_url(
    secret: str, path: str, *, ttl_s: int = TTL_PADRAO_S, agora: int | None = None
) -> str:
    """`path` → `path?e=<agora+ttl>&s=<hmac>`. `path` não pode já ter query."""
    agora = int(time.time()) if agora is None else agora
    expira_em = agora + ttl_s
    return f"{path}?e={expira_em}&s={assinar(secret, path, expira_em)}"


def verificar(
    secret: str, path: str, params: dict[str, str], *, agora: int | None = None
) -> bool:
    expira_em = _validar_e_extrair_expira(params, agora)
    if expira_em is None:
        return False
    return hmac.compare_digest(assinar(secret, path, expira_em), params["s"])


# ── escopo de conteúdo (play, seg, thumbs) ───────────────────────────────────


def assinar_escopo(secret: str, fonte: str, dia: str, expira_em: int) -> str:
    return _hmac(secret, f"v1\n{fonte}\n{dia}\n{expira_em}")


def query_escopo_assinada(
    secret: str, fonte: str, dia: str, *, ttl_s: int = TTL_PADRAO_S, agora: int | None = None
) -> str:
    """Só a query (`e=<...>&s=<...>`) — o chamador prefixa com o path da rota."""
    agora = int(time.time()) if agora is None else agora
    expira_em = agora + ttl_s
    return f"e={expira_em}&s={assinar_escopo(secret, fonte, dia, expira_em)}"


def verificar_escopo(
    secret: str, fonte: str, dia: str, params: dict[str, str], *, agora: int | None = None
) -> bool:
    expira_em = _validar_e_extrair_expira(params, agora)
    if expira_em is None:
        return False
    return hmac.compare_digest(assinar_escopo(secret, fonte, dia, expira_em), params["s"])
