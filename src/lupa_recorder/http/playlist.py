"""Playlist HLS sintética a partir do catálogo (plano §11.1).

Os `.ts` alinhados ao relógio **já são segmentos HLS válidos** — o agente sintetiza a
playlist sob demanda, sem concatenar, transcodificar nem gerar arquivo. Dia passado →
`VOD` com `#EXT-X-ENDLIST`; dia corrente → `EVENT` sem `ENDLIST` (o player recarrega e
acrescenta os segmentos novos ao fim — a barra cresce sozinha).

**Cada segmento leva `#EXT-X-PROGRAM-DATE-TIME` + `#EXT-X-DISCONTINUITY`** (menos o
primeiro, que não precisa do DISCONTINUITY). Os segmentos são capturados com
`-reset_timestamps 1` e cortados no relógio (`-segment_atclocktime`), então cada `.ts`
tem timestamps internos independentes e o GOP não alinha na borda — sem marcar toda
borda como descontinuidade o `hls.js` tenta emendar timelines sobrepostas e trava
(`bufferStalledError`, achado de campo 2026-08-29). Com PDT em cada um, o player
posiciona cada segmento pela hora absoluta e não depende da continuidade de PTS.

Geração pura, sem I/O — plano §20 pede 100% de cobertura aqui. O chamador (`app.py`) lê
o catálogo, monta as `EntradaSegmento` (com a URL já assinada) e passa pra cá.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# Folga entre o espaçamento real de dois segmentos e o nominal (`segment_seconds`) dentro
# da qual o espaçamento conta como a duração real do segmento. Os cortes são
# `-segment_atclocktime`, então em operação normal o espaçamento fica coladinho no nominal.
TOLERANCIA_S = 12.0


@dataclass
class EntradaSegmento:
    started_at: datetime  # de preferência tz-aware (pro PROGRAM-DATE-TIME sair correto)
    url: str  # caminho já assinado (`/v1/seg/...?e=&s=`) — playlist não assina nada
    duration_ms: int | None = None  # medido (recover/ffprobe); None em captura normal
    partial: bool = False


def _duracoes(
    entradas: list[EntradaSegmento],
    segment_seconds: int,
    tolerancia_s: float,
    agora: datetime | None,
) -> list[float]:
    """Duração de cada segmento em segundos. Medida quando o catálogo tem (`recover`);
    senão o espaçamento até o próximo (contíguo) ou o nominal."""
    duracoes: list[float] = []
    nominal = float(segment_seconds)

    for i, entrada in enumerate(entradas):
        medida = entrada.duration_ms / 1000 if entrada.duration_ms else None
        if medida is not None:
            dur = medida
        elif i + 1 < len(entradas):
            delta = (entradas[i + 1].started_at - entrada.started_at).total_seconds()
            dur = delta if 0 < delta <= nominal + tolerancia_s else nominal
        elif agora is not None:
            # borda ao vivo da playlist EVENT: o último segmento pode ainda estar sendo
            # escrito — cobre só até agora, no máximo o nominal.
            dur = max(0.0, min((agora - entrada.started_at).total_seconds(), nominal))
        else:
            dur = nominal
        duracoes.append(round(dur, 3))
    return duracoes


def montar_playlist(
    entradas: list[EntradaSegmento],
    *,
    dia_corrente: bool,
    segment_seconds: int,
    tolerancia_s: float = TOLERANCIA_S,
    agora: datetime | None = None,
) -> str:
    duracoes = _duracoes(entradas, segment_seconds, tolerancia_s, agora)
    target = math.ceil(max(duracoes)) if duracoes else segment_seconds

    linhas = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-PLAYLIST-TYPE:{'EVENT' if dia_corrente else 'VOD'}",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-DISCONTINUITY-SEQUENCE:0",
    ]

    for i, entrada in enumerate(entradas):
        if i > 0:
            linhas.append("#EXT-X-DISCONTINUITY")
        linhas.append(
            f"#EXT-X-PROGRAM-DATE-TIME:{entrada.started_at.isoformat(timespec='milliseconds')}"
        )
        linhas.append(f"#EXTINF:{duracoes[i]:.3f},")
        linhas.append(entrada.url)

    if not dia_corrente:
        linhas.append("#EXT-X-ENDLIST")

    return "\n".join(linhas) + "\n"
