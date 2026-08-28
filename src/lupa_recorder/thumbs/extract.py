"""Miniaturas por segmento fechado — só `kind=tv` (rádio não tem frame). Plano §11.4.

4 seeks por segmento de 4 min (cadência de 1/min: `0, 60, 120, 180`), seek de **entrada**
(`-ss` antes do `-i` — pula pro keyframe mais próximo sem decodificar o segmento inteiro,
~0,1s por miniatura em vez de 5-10x mais caro). `nice 19`. Se a extração falhar ou a
máquina estiver sob pressão de CPU, o segmento fica com `has_thumbnails=false` — a
captura sempre ganha, isso nunca pode atrasar nem derrubar uma fonte.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CADENCIA_S = 60
LARGURA_PX = 160
QUALIDADE_JPEG = 6


def offsets_dos_seeks(segment_seconds: int, cadencia_s: int = CADENCIA_S) -> list[int]:
    """`[0, 60, 120, 180]` pra um segmento de 240s com cadência de 60s. Baseado na duração
    **configurada** da fonte (`segment_seconds`), não numa duração medida — evita precisar
    de `ffprobe` só pra saber quantos seeks tirar."""
    return list(range(0, segment_seconds, cadencia_s))


def nome_arquivo_miniatura(segmento: Path, offset_s: int) -> str:
    base = segmento.stem  # "170000" (sem .ts)
    return f"{base}_{offset_s:03d}.jpg"


def maquina_sob_pressao_de_cpu(limiar_por_nucleo: float = 0.9) -> bool:
    """Heurística simples — `nice 19` já ajuda, mas se a máquina já está no limite não
    vale a pena nem tentar. `os.getloadavg()` não existe no Windows, mas essa é uma
    limitação aceitável (o gravador é sempre Linux, plano §12)."""
    try:
        carga_1min, _, _ = os.getloadavg()
    except OSError:
        return False  # sem como medir — não bloqueia por precaução
    nucleos = os.cpu_count() or 1
    return (carga_1min / nucleos) > limiar_por_nucleo


def extrair_miniatura_sync(segmento: Path, offset_s: int, destino: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    resultado = subprocess.run(
        [
            "nice",
            "-n",
            "19",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            str(offset_s),
            "-i",
            str(segmento),
            "-frames:v",
            "1",
            "-vf",
            f"scale={LARGURA_PX}:-2",
            "-q:v",
            str(QUALIDADE_JPEG),
            str(destino),
        ],
        capture_output=True,
        timeout=15,
    )
    return resultado.returncode == 0 and destino.exists()


def extrair_miniaturas_do_segmento(
    segmento: Path, destino_dir: Path, segment_seconds: int, *, extrator=extrair_miniatura_sync
) -> list[Path]:
    """Devolve as miniaturas que deram certo — **nunca levanta**. Uma falha de extração é
    "sem miniatura pra esse ponto", nunca motivo pra derrubar a fonte."""
    geradas = []
    if maquina_sob_pressao_de_cpu():
        logger.info("pulando miniaturas de %s — máquina sob pressão de CPU", segmento.name)
        return geradas
    destino_dir.mkdir(parents=True, exist_ok=True)  # responsabilidade de quem orquestra,
    # não de cada extrator individual (um extrator customizado não devia precisar lembrar disso)
    for offset in offsets_dos_seeks(segment_seconds):
        destino = destino_dir / nome_arquivo_miniatura(segmento, offset)
        try:
            if extrator(segmento, offset, destino):
                geradas.append(destino)
        except Exception:
            logger.exception("falha extraindo miniatura de %s @ %ds", segmento.name, offset)
    return geradas
