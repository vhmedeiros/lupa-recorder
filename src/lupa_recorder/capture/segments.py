"""Gestão de arquivo de segmento — pasta por dia, promoção de `.ts.part` pra `.ts`, e o
"último progresso" que o watchdog do supervisor usa.

Tudo aqui é I/O de filesystem local, sem rede/subprocesso — testável de verdade com
`tmp_path`, sem precisar de ffmpeg.

Layout de disco (ajuste 2026-08-28, `fase1-gravador-autonomo.md`):
    {data_root}/{slug}/{AAAA-MM-DD}/HHMMSS.ts

O `ffmpeg` escreve com sufixo `.ts.part` (via `-strftime 1` no padrão de saída) e roda
como **um processo só, de longa duração** — o `%Y-%m-%d` do próprio padrão de saída do
ffmpeg já rola pra pasta do dia seguinte sozinho na virada. O que precisa existir de
antemão é só a PASTA (`ffmpeg` não cria diretório) — por isso `garantir_pastas_do_dia`
sempre prepara hoje E amanhã.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

SUFIXO_PARCIAL = ".part"
FORMATO_PASTA_DIA = "%Y-%m-%d"
PADRAO_NOME_SEGMENTO = "%H%M%S.ts" + SUFIXO_PARCIAL

_RE_NOME_SEGMENTO_FECHADO = re.compile(r"^(\d{2})(\d{2})(\d{2})\.ts$")


def started_at_do_arquivo(arquivo: Path) -> str | None:
    """`{AAAA-MM-DD}/HHMMSS.ts` → `"AAAA-MM-DDTHH:MM:SS"` (ISO 8601, sem fuso — o relógio
    da máquina já é o que importa aqui). `None` se o nome não bate com o padrão que o
    supervisor gera (arquivo estranho, não é um segmento nosso)."""
    m = _RE_NOME_SEGMENTO_FECHADO.match(arquivo.name)
    if not m:
        return None
    hh, mm, ss = m.groups()
    return f"{arquivo.parent.name}T{hh}:{mm}:{ss}"


def pasta_base(data_root: Path, slug: str) -> Path:
    return data_root / slug


def pasta_do_dia(data_root: Path, slug: str, quando: datetime | None = None) -> Path:
    quando = quando or datetime.now()
    return pasta_base(data_root, slug) / quando.strftime(FORMATO_PASTA_DIA)


def padrao_saida_ffmpeg(data_root: Path, slug: str) -> str:
    """O padrão `-strftime 1` completo — ffmpeg resolve `%Y-%m-%d`/`%H%M%S` sozinho a
    cada segmento novo, inclusive na virada de dia (por isso não precisa reiniciar o
    processo à meia-noite — só a pasta já precisa existir, ver `garantir_pastas_do_dia`)."""
    return str(pasta_base(data_root, slug) / f"%Y-%m-%d/{PADRAO_NOME_SEGMENTO}")


def garantir_pastas_do_dia(data_root: Path, slug: str, quando: datetime | None = None) -> None:
    """Cria a pasta de hoje E de amanhã — chamado a cada tick do supervisor (idempotente,
    barato). Sem isso a captura falha exatamente na virada de meia-noite."""
    quando = quando or datetime.now()
    for dia in (quando, quando + timedelta(days=1)):
        pasta_do_dia(data_root, slug, dia).mkdir(parents=True, exist_ok=True)


def _pastas_recentes(data_root: Path, slug: str, quando: datetime | None = None) -> list[Path]:
    """Hoje + ontem — cobre o segmento que ainda podia estar `.part` bem na virada de dia
    (o processo é um só, contínuo; o arquivo mais recente pode estar numa pasta ou noutra
    dependendo de exatamente quando o poll roda em relação à virada)."""
    quando = quando or datetime.now()
    return [
        pasta_do_dia(data_root, slug, quando),
        pasta_do_dia(data_root, slug, quando - timedelta(days=1)),
    ]


def listar_parciais(data_root: Path, slug: str, quando: datetime | None = None) -> list[Path]:
    parciais: list[Path] = []
    for pasta in _pastas_recentes(data_root, slug, quando):
        if pasta.is_dir():
            parciais.extend(pasta.glob(f"*{SUFIXO_PARCIAL}"))
    return sorted(parciais, key=lambda p: p.stat().st_mtime)


def listar_parciais_orfaos(data_root: Path, slug: str) -> list[Path]:
    """Todo `.ts.part` de **qualquer** pasta de dia da fonte, ordenado por mtime.

    Usado só pelo `recover` no boot: ali nenhum processo de captura está escrevendo, então
    um `.part` de dias/semanas atrás (máquina que ficou desligada muito tempo depois de uma
    queda de energia) também é órfão e precisa ser remuxado ou descartado — senão fica pra
    sempre do lado do `.ts`, ocupando disco. A operação normal usa `listar_parciais` (só
    hoje/ontem), porque lá o `.part` mais recente pode estar sendo escrito neste instante.
    """
    base = pasta_base(data_root, slug)
    if not base.is_dir():
        return []
    parciais = [
        arquivo
        for pasta_dia in base.iterdir()
        if pasta_dia.is_dir()
        for arquivo in pasta_dia.glob(f"*{SUFIXO_PARCIAL}")
    ]
    return sorted(parciais, key=lambda p: p.stat().st_mtime)


def promover_segmentos_prontos(data_root: Path, slug: str, quando: datetime | None = None) -> list[Path]:
    """Renomeia todo `.ts.part` que **não** seja o mais recente pra `.ts` — o ffmpeg só
    escreve num arquivo por vez, então qualquer `.part` que não seja o mais novo já
    terminou de ser escrito. Devolve o que foi promovido (pra log/evento)."""
    parciais = listar_parciais(data_root, slug, quando)
    if len(parciais) <= 1:
        return []
    promovidos = []
    for parcial in parciais[:-1]:  # todos menos o mais recente (já ordenados por mtime)
        destino = parcial.with_suffix("")  # tira só o .part final, mantém o .ts
        parcial.rename(destino)
        promovidos.append(destino)
    return promovidos


def ultimo_progresso_em(data_root: Path, slug: str, quando: datetime | None = None) -> float | None:
    """`mtime` do arquivo (`.ts` ou `.ts.part`) mais recentemente modificado — o que o
    watchdog usa pra decidir "tem byte novo chegando ou não". `None` se não achou nada
    ainda (processo acabou de iniciar, nenhum segmento fechou)."""
    quando = quando or datetime.now()
    candidatos: list[Path] = []
    for pasta in _pastas_recentes(data_root, slug, quando):
        if pasta.is_dir():
            candidatos.extend(pasta.glob("*.ts"))
            candidatos.extend(pasta.glob(f"*{SUFIXO_PARCIAL}"))
    if not candidatos:
        return None
    return max(c.stat().st_mtime for c in candidatos)
