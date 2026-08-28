"""`url_resolver=http_refresh` — busca a URL fresca num endpoint de API antes de cada
(re)start (plano §8.4). Só funciona quando a fonte tem um `url_refresh_url` de verdade
identificado (um XHR que devolve JSON, visto no Network do navegador) — nem toda fonte com
token tem isso; sem ele, a fonte precisa ficar em `url_resolver=static` por enquanto.

Decisão de 2026-08-28 (GRV-04 fechado): só o modo periódico existe — o refresh acontece
a cada `refresh_interval_seconds` (default 40min), não "a cada tentativa de conexão". A
tolerância real medida (≥3h) sobra folga enorme sobre isso.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from lupa_recorder.config import SourceConfig
from lupa_recorder.resolve.base import ResolvedInput, ResolveError


def extrair_por_caminho(dados: dict, caminho: str) -> str | None:
    """Navega um dict por um caminho tipo 'data.url' — suporta aninhamento simples."""
    atual: object = dados
    for parte in caminho.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return None
        atual = atual[parte]
    return atual if isinstance(atual, str) else None


def _buscar_url_fresca_sync(refresh_url: str, json_path: str, timeout_s: float = 10) -> str:
    req = urllib.request.Request(refresh_url, headers={"User-Agent": "lupa-recorder/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            dados = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ResolveError(f"não consegui buscar {refresh_url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolveError(f"{refresh_url} não devolveu JSON válido: {exc}") from exc

    url = extrair_por_caminho(dados, json_path)
    if not url:
        raise ResolveError(f"{refresh_url} não tinha a chave {json_path!r} no JSON de resposta.")
    return url


class HttpRefreshResolver:
    async def resolve(self, source: SourceConfig) -> ResolvedInput:
        if not source.url_refresh_url:
            raise ResolveError(f"fonte {source.slug!r}: url_resolver=http_refresh sem url_refresh_url.")
        url = await asyncio.to_thread(
            _buscar_url_fresca_sync, source.url_refresh_url, source.url_refresh_json_path
        )
        return ResolvedInput(urls=[url])
