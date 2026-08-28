"""`url_resolver=yt_dlp` — resolve as duas URLs (vídeo + áudio) de uma live do YouTube via
`yt-dlp -g`. Achado de campo da Fase 0 (canal SBT): lives normalmente não têm formato
combinado, então não dá pra usar uma URL só.

Esta é a mesma lógica usada pelo `lupa-recorder probe` (`probe.py` importa daqui — não
duplicar). Aqui ganha só o wrapper assíncrono que o supervisor usa.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from lupa_recorder.config import SourceConfig
from lupa_recorder.resolve.base import ResolvedInput, ResolveError


def resolve_youtube_urls_sync(url: str, quality_profile: str = "480p") -> tuple[str, str]:
    if not shutil.which("yt-dlp"):
        raise ResolveError("yt-dlp não encontrado no PATH — rode o bootstrap.sh primeiro.")
    altura = quality_profile.rstrip("p") or "480"
    try:
        video = subprocess.run(
            ["yt-dlp", "-g", "-f", f"bestvideo[height<={altura}]", url],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
        audio = subprocess.run(
            ["yt-dlp", "-g", "-f", "bestaudio", url],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ResolveError(f"yt-dlp falhou ao resolver {url}: {exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ResolveError(f"yt-dlp demorou demais resolvendo {url}: {exc}") from exc
    if not video or not audio:
        raise ResolveError(f"yt-dlp não devolveu URL de vídeo/áudio pra {url} (live fora do ar?).")
    return video, audio


class YtDlpResolver:
    async def resolve(self, source: SourceConfig) -> ResolvedInput:
        if not source.url:
            raise ResolveError(f"fonte {source.slug!r}: sem url configurada.")
        video, audio = await asyncio.to_thread(
            resolve_youtube_urls_sync, source.url, source.quality_profile or "480p"
        )
        return ResolvedInput(urls=[video, audio])
