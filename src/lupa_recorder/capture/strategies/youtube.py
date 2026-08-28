from __future__ import annotations

from lupa_recorder.capture.strategies.base import SourceStrategy
from lupa_recorder.resolve.base import ResolvedInput


class YoutubeStrategy(SourceStrategy):
    """YouTube ao vivo. Achado de campo (2026-08-26, canal SBT): não tem formato
    combinado — precisa de **duas** entradas (vídeo e áudio resolvidos separados via
    `yt-dlp`, ver `resolve/ytdlp.py`) e **`-thread_queue_size 1024` em cada uma**, senão a
    fila padrão (8) estoura e corrompe pacote de áudio. Plano §8.5.

    Restart planejado a cada 3h fica no supervisor (não aqui) — a estratégia só monta o
    comando, não decide quando reiniciar.
    """

    def build_input(self, resolved: ResolvedInput) -> list[str]:
        if len(resolved.urls) != 2:
            raise ValueError(
                f"YoutubeStrategy precisa de 2 URLs (vídeo, áudio), recebeu {len(resolved.urls)}."
            )
        url_video, url_audio = resolved.urls
        return [
            "-thread_queue_size",
            "1024",
            "-i",
            url_video,
            "-thread_queue_size",
            "1024",
            "-i",
            url_audio,
        ]

    def map_args(self) -> list[str]:
        return ["-map", "0:v:0", "-map", "1:a:0"]
