"""Playlist HLS sintética a partir do catálogo (plano §11.1).

Os `.ts` alinhados ao relógio **já são segmentos HLS válidos** — o agente sintetiza a
playlist sob demanda, sem concatenar, transcodificar nem gerar arquivo. Dia passado →
`VOD` com `#EXT-X-ENDLIST`; dia corrente → `EVENT` sem `ENDLIST` (o player recarrega e
acrescenta os segmentos novos ao fim — a barra cresce sozinha). Buraco de gravação vira
`#EXT-X-DISCONTINUITY`.

Geração pura, sem I/O — plano §20 pede 100% de cobertura aqui. O chamador (`app.py`) lê
o catálogo, monta as `EntradaSegmento` (com a URL já assinada) e passa pra cá.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# Folga entre o espaçamento real de dois segmentos e o nominal (`segment_seconds`) dentro
# da qual eles ainda contam como contíguos. Acima disso é buraco → DISCONTINUITY. Os
# cortes são `-segment_atclocktime`, então o espaçamento real fica coladinho no nominal
# em operação normal; 12s cobre jitter de relógio/CDN sem mascarar um gap de verdade.
TOLERANCIA_GAP_S = 12.0


@dataclass
class EntradaSegmento:
    started_at: datetime  # de preferência tz-aware (pro PROGRAM-DATE-TIME sair correto)
    url: str  # caminho já assinado (`/v1/seg/...?e=&s=`) — playlist não assina nada
    duration_ms: int | None = None  # medido (recover/ffprobe); None em captura normal
    partial: bool = False


def _duracoes_e_gaps(
    entradas: list[EntradaSegmento],
    segment_seconds: int,
    tolerancia_gap_s: float,
    agora: datetime | None,
) -> tuple[list[float], list[bool]]:
    """Devolve `(duracoes, gap_antes)` — `gap_antes[i]` = há buraco imediatamente antes
    do segmento `i` (→ `#EXT-X-DISCONTINUITY`). `duracoes[i]` em segundos."""
    duracoes: list[float] = []
    gap_antes: list[bool] = [False] * len(entradas)
    nominal = float(segment_seconds)

    for i, entrada in enumerate(entradas):
        medida = entrada.duration_ms / 1000 if entrada.duration_ms else None
        if i + 1 < len(entradas):
            delta = (entradas[i + 1].started_at - entrada.started_at).total_seconds()
            if medida is not None:
                dur = medida
            elif abs(delta - nominal) <= tolerancia_gap_s:
                dur = delta  # contíguo — o espaçamento real é a duração real
            else:
                dur = nominal  # gap logo depois, ou relógio estranho — não invento duração
            if delta > nominal + tolerancia_gap_s:
                gap_antes[i + 1] = True
        elif medida is not None:
            dur = medida
        elif agora is not None:
            # borda ao vivo da playlist EVENT: o último segmento pode ainda estar sendo
            # escrito — cobre só até agora, no máximo o nominal.
            dur = max(0.0, min((agora - entrada.started_at).total_seconds(), nominal))
        else:
            dur = nominal
        duracoes.append(round(dur, 3))
    return duracoes, gap_antes


def montar_playlist(
    entradas: list[EntradaSegmento],
    *,
    dia_corrente: bool,
    segment_seconds: int,
    tolerancia_gap_s: float = TOLERANCIA_GAP_S,
    agora: datetime | None = None,
) -> str:
    duracoes, gap_antes = _duracoes_e_gaps(entradas, segment_seconds, tolerancia_gap_s, agora)
    target = math.ceil(max(duracoes)) if duracoes else segment_seconds

    linhas = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-PLAYLIST-TYPE:{'EVENT' if dia_corrente else 'VOD'}",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]

    for i, entrada in enumerate(entradas):
        if gap_antes[i]:
            linhas.append("#EXT-X-DISCONTINUITY")
        if i == 0 or gap_antes[i]:
            linhas.append(f"#EXT-X-PROGRAM-DATE-TIME:{entrada.started_at.isoformat(timespec='milliseconds')}")
        linhas.append(f"#EXTINF:{duracoes[i]:.3f},")
        linhas.append(entrada.url)

    if not dia_corrente:
        linhas.append("#EXT-X-ENDLIST")

    return "\n".join(linhas) + "\n"
