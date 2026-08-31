"""`lupa-recorder recover` — roda no boot (plano §1.4): varre `.part` órfão (sobrou de uma
queda de energia/crash, sem processo vivo pra promover sozinho como `capture/segments.py`
faz em operação normal), tenta remuxar com `ffmpeg -c copy` (MPEG-TS quase sempre
sobrevive a um corte no meio), marca `PARTIAL` o que salvou, descarta o resto.

Também reconstrói o catálogo a partir dos nomes de arquivo — "um `ls` do diretório é um
backup do banco" (plano §1.4). O SQLite nunca é a única fonte de verdade.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection

from lupa_recorder.capture.segments import (
    listar_parciais_orfaos,
    pasta_base,
    started_at_do_arquivo,
)
from lupa_recorder.catalog.models import (
    Event,
    Segment,
    SegmentState,
    inserir_segmento,
    registrar_evento,
    segmento_existe,
)


class RemuxError(Exception):
    """Remux falhou — mensagem pronta pra virar evento no catálogo."""


def remuxar_sync(orfao: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise RemuxError("ffmpeg não encontrado no PATH.")
    destino = orfao.with_suffix("")  # tira só o .part, mantém o .ts
    resultado = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(orfao), "-c", "copy", str(destino)],
        capture_output=True,
        timeout=60,
    )
    if resultado.returncode != 0 or not destino.exists():
        stderr = resultado.stderr.decode(errors="replace").strip()
        # achado ao vivo (2026-08-28): um ffmpeg que segfaulta morre antes de escrever
        # stderr nenhum — sem o returncode na mensagem, o evento fica sem pista nenhuma
        # do que aconteceu.
        detalhe = stderr or f"sem saída de erro (returncode={resultado.returncode})"
        raise RemuxError(f"remux de {orfao.name} falhou: {detalhe}")
    # achado ao vivo (2026-08-28): sem isso, o .part original ficava pra sempre do lado
    # do .ts remuxado — duplica espaço em disco e todo `recover` seguinte tentava
    # remuxar de novo o mesmo órfão, sem nunca convergir.
    orfao.unlink()
    return destino


def obter_duracao_ms_via_ffprobe(arquivo: Path) -> int | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        saida = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(arquivo)],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        duracao_s = float(json.loads(saida.stdout)["format"]["duration"])
        return round(duracao_s * 1000)
    except Exception:
        return None


@dataclass
class ResultadoRecover:
    recuperados: list[Path] = field(default_factory=list)
    descartados: list[Path] = field(default_factory=list)


def recuperar_orfaos(
    conn: Connection, data_root: Path, slug: str, *, remuxer=remuxar_sync
) -> ResultadoRecover:
    """Assume que **nenhum processo de captura está escrevendo nessa fonte agora** — todo
    `.ts.part` encontrado é órfão por definição. Chamado no boot, antes do supervisor subir
    (nunca ao mesmo tempo — os dois mexendo no mesmo `.part` seria uma corrida)."""
    resultado = ResultadoRecover()
    for orfao in listar_parciais_orfaos(data_root, slug):
        try:
            recuperado = remuxer(orfao)
        except RemuxError as exc:
            registrar_evento(conn, Event(source_slug=slug, kind="recover_failed", message=str(exc)))
            orfao.unlink(missing_ok=True)
            resultado.descartados.append(orfao)
            continue

        iniciado_em = started_at_do_arquivo(recuperado)
        if iniciado_em is not None:
            inserir_segmento(
                conn,
                Segment(
                    source_slug=slug,
                    path=str(recuperado),
                    started_at=iniciado_em,
                    bytes=recuperado.stat().st_size,
                    duration_ms=obter_duracao_ms_via_ffprobe(recuperado),
                    state=SegmentState.partial,
                ),
            )
        registrar_evento(
            conn,
            Event(source_slug=slug, kind="recover_partial", message=f"{recuperado.name} recuperado"),
        )
        resultado.recuperados.append(recuperado)
    return resultado


def reconstruir_catalogo_da_fonte(
    conn: Connection, data_root: Path, slug: str, *, obter_duracao_ms=obter_duracao_ms_via_ffprobe
) -> tuple[int, int]:
    """Varre todo `.ts` já promovido e garante que está no catálogo — idempotente. Devolve
    `(novos, já_catalogados)`. Serve tanto pro boot normal (confirmar que nada ficou de
    fora) quanto pro cenário "apaguei o SQLite de propósito" (plano §1.4)."""
    pasta = pasta_base(data_root, slug)
    if not pasta.is_dir():
        return (0, 0)

    novos = ja_catalogados = 0
    for pasta_dia in sorted(p for p in pasta.iterdir() if p.is_dir()):
        for arquivo in sorted(pasta_dia.glob("*.ts")):
            iniciado_em = started_at_do_arquivo(arquivo)
            if iniciado_em is None:
                continue
            if segmento_existe(conn, slug, iniciado_em):
                ja_catalogados += 1
                continue
            inserir_segmento(
                conn,
                Segment(
                    source_slug=slug,
                    path=str(arquivo),
                    started_at=iniciado_em,
                    bytes=arquivo.stat().st_size,
                    duration_ms=obter_duracao_ms(arquivo),
                ),
            )
            novos += 1
    return (novos, ja_catalogados)
