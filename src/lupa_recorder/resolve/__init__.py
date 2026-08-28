from __future__ import annotations

from lupa_recorder.config import SourceConfig, UrlResolver
from lupa_recorder.resolve.base import ResolvedInput, ResolveError, Resolver
from lupa_recorder.resolve.http_refresh import HttpRefreshResolver
from lupa_recorder.resolve.static import StaticResolver
from lupa_recorder.resolve.ytdlp import YtDlpResolver

_RESOLVER_POR_TIPO: dict[UrlResolver, Resolver] = {
    UrlResolver.static: StaticResolver(),
    UrlResolver.http_refresh: HttpRefreshResolver(),
    UrlResolver.yt_dlp: YtDlpResolver(),
}


def criar_resolver(source: SourceConfig) -> Resolver:
    return _RESOLVER_POR_TIPO[source.url_resolver]


__all__ = [
    "HttpRefreshResolver",
    "ResolveError",
    "ResolvedInput",
    "Resolver",
    "StaticResolver",
    "YtDlpResolver",
    "criar_resolver",
]
