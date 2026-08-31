#!/usr/bin/env python3
"""Assina uma URL do HTTP local do agente pra teste/debug de campo (sub-etapa 1.7).

Sem dependência nenhuma — só a stdlib. Copie pra máquina e rode:

    SECRET=$(sudo grep hmac_secret /var/lib/lupa-recorder/agent.toml | cut -d'"' -f2)

    # token de caminho (/v1/status, /v1/health não precisa):
    curl -s "http://127.0.0.1:8383$(python3 sign-url.py path /v1/status "$SECRET")"

    # token de escopo (playlist e VTT — amarra fonte+dia, estável entre recargas):
    DIA=$(date +%F)
    curl -s "http://127.0.0.1:8383/v1/play/radio-ouveai/$DIA.m3u8?$(python3 sign-url.py scope radio-ouveai "$DIA" "$SECRET")"

O `run` também imprime uma URL de playlist já assinada (TTL 24h) por fonte no boot —
`journalctl -u lupa-recorder | grep 'playlist de hoje'` costuma ser mais rápido.
"""

from __future__ import annotations

import hashlib
import hmac
import sys
import time

TTL_S = 3600


def _assinar(secret: bytes, mensagem: str) -> str:
    return hmac.new(secret, mensagem.encode(), hashlib.sha256).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    modo = argv[0]
    expira = int(time.time()) + TTL_S

    if modo == "path" and len(argv) == 3:
        caminho, secret = argv[1], argv[2].encode()
        assinatura = _assinar(secret, f"{caminho}\n{expira}")
        print(f"{caminho}?e={expira}&s={assinatura}")
        return 0

    if modo == "scope" and len(argv) == 4:
        fonte, dia, secret = argv[1], argv[2], argv[3].encode()
        assinatura = _assinar(secret, f"v1\n{fonte}\n{dia}\n{expira}")
        print(f"e={expira}&s={assinatura}")
        return 0

    print(
        "uso: sign-url.py path <caminho> <secret>\n"
        "     sign-url.py scope <fonte> <dia-AAAA-MM-DD> <secret>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
