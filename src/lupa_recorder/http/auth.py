"""Token HMAC de curta duração na query string — `?e=<expira>&s=<hmac_hex>` (plano §11.3).

Na query, e não em header, porque nem o `hls.js` nem as `<img>` da filmstrip mandam
header customizado em cada request de segmento/sprite.

O que é assinado: `f"{path}\n{expira}"`, onde `path` é o caminho da URL **sem** query e
`expira` é um unix timestamp inteiro. Quem emite a URL assina (o agente, ao montar a
playlist, assina cada linha de segmento; na Fase 2 a Lupa assina os pontos de entrada
com o mesmo segredo, negociado via `/api/recorders/me/config`). Quem verifica é sempre
o agente.

Puro: sem I/O, relógio injetável — `test_http_auth.py` cobre 100%.
"""

from __future__ import annotations

import hashlib
import hmac
import time

TTL_PADRAO_S = 6 * 3600  # 6h — mesma vida do token que a Lupa emite na Fase 2 (plano §11.5)


def assinar(secret: str, path: str, expira_em: int) -> str:
    mensagem = f"{path}\n{expira_em}".encode()
    return hmac.new(secret.encode(), mensagem, hashlib.sha256).hexdigest()


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
    """`params` são os parâmetros da query já parseados (primeiro valor de cada chave).
    Recusa: `e`/`s` ausente ou malformado, token expirado, assinatura que não bate
    (comparada com `compare_digest` — sem vazamento por timing)."""
    agora = int(time.time()) if agora is None else agora
    e_bruto = params.get("e")
    assinatura = params.get("s")
    if not e_bruto or not assinatura:
        return False
    try:
        expira_em = int(e_bruto)
    except ValueError:
        return False
    if agora >= expira_em:
        return False
    return hmac.compare_digest(assinar(secret, path, expira_em), assinatura)
