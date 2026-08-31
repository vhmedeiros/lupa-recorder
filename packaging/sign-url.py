#!/usr/bin/env python3
"""Assina uma URL do HTTP local do agente pra teste/debug de campo (sub-etapa 1.7).

Sem dependência nenhuma — só a stdlib. Por padrão lê o `hmac_secret` do
`/var/lib/lupa-recorder/agent.toml` (precisa de `sudo`, o arquivo é 600):

    DIA=$(date +%F)

    # token de caminho (/v1/status; /v1/health não precisa):
    curl -s "http://127.0.0.1:8383$(sudo python3 sign-url.py path /v1/status)"

    # token de escopo (playlist e VTT — amarra fonte+dia, estável entre recargas):
    curl -s "http://127.0.0.1:8383/v1/play/tv-cultura/$DIA.m3u8?$(sudo python3 sign-url.py scope tv-cultura "$DIA")"

Atalho pra playlist: o `run` já imprime uma URL assinada (TTL 24 h) por fonte no boot —
`journalctl -u lupa-recorder | grep 'playlist de hoje'`.

Passe `--secret <valor>` pra não ler o arquivo, ou `--config <caminho>` pra outro agent.toml.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import sys
import time
import tomllib
from pathlib import Path

TTL_S = 3600
CONFIG_PADRAO = Path("/var/lib/lupa-recorder/agent.toml")


def _secret_do_config(caminho: Path) -> str:
    try:
        dados = tomllib.loads(caminho.read_text())
    except FileNotFoundError:
        sys.exit(f"agent.toml não encontrado em {caminho} — passe --config ou --secret.")
    except PermissionError:
        sys.exit(f"sem permissão pra ler {caminho} — rode com sudo, ou passe --secret.")
    try:
        return dados["security"]["hmac_secret"]
    except KeyError:
        sys.exit(f"{caminho} não tem [security].hmac_secret.")


def _assinar(secret: str, mensagem: str) -> str:
    return hmac.new(secret.encode(), mensagem.encode(), hashlib.sha256).hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sign-url.py", description=__doc__)
    parser.add_argument("--secret", help="Usa este segredo em vez de ler o agent.toml.")
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO, help="agent.toml a ler.")
    sub = parser.add_subparsers(dest="modo", required=True)

    p_path = sub.add_parser("path", help="Token de caminho (/v1/status, /v1/probe).")
    p_path.add_argument("caminho")

    p_scope = sub.add_parser("scope", help="Token de escopo (playlist, segmentos, miniaturas).")
    p_scope.add_argument("fonte")
    p_scope.add_argument("dia", help="AAAA-MM-DD")

    args = parser.parse_args(argv)
    secret = args.secret or _secret_do_config(args.config)
    expira = int(time.time()) + TTL_S

    if args.modo == "path":
        assinatura = _assinar(secret, f"{args.caminho}\n{expira}")
        print(f"{args.caminho}?e={expira}&s={assinatura}")
    else:
        assinatura = _assinar(secret, f"v1\n{args.fonte}\n{args.dia}\n{expira}")
        print(f"e={expira}&s={assinatura}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
