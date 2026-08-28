"""Orquestra `extract.py` + `sprite.py`: onde as miniaturas vivem no disco, quando uma hora
"fechou" (vira sprite) e a montagem do VTT do dia — sprite pras horas fechadas, miniatura
avulsa pra hora corrente (plano §11.4). Vive no **SSD** (`system_root`), não no acervo —
milhares de arquivo pequeno com leitura aleatória é o perfil onde SSD ganha e HD sofre.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from lupa_recorder.thumbs.sprite import (
    gerar_cues_avulsas,
    gerar_cues_sprite,
    montar_sprite_da_hora,
    montar_webvtt,
)

_RE_MINIATURA = re.compile(r"^(\d{2})(\d{2})(\d{2})_(\d{3})\.jpg$")


def pasta_thumbs_do_dia(system_root: Path, slug: str, quando: datetime) -> Path:
    return system_root / "thumbs" / slug / quando.strftime("%Y-%m-%d")


def pasta_sprites_do_dia(system_root: Path, slug: str, quando: datetime) -> Path:
    return pasta_thumbs_do_dia(system_root, slug, quando) / "sprites"


def _hora_da_miniatura(nome: str) -> int | None:
    m = _RE_MINIATURA.match(nome)
    return int(m.group(1)) if m else None


def _minuto_da_miniatura(nome: str) -> int | None:
    m = _RE_MINIATURA.match(nome)
    if not m:
        return None
    return int(m.group(2))


def montar_sprites_pendentes(system_root: Path, slug: str, quando: datetime) -> list[Path]:
    """Monta o sprite de toda hora **já fechada** (hora < hora atual) que ainda não tem
    sprite — idempotente, seguro rodar de novo a qualquer momento."""
    pasta = pasta_thumbs_do_dia(system_root, slug, quando)
    if not pasta.is_dir():
        return []
    pasta_sprites = pasta_sprites_do_dia(system_root, slug, quando)

    por_hora: dict[int, list[Path]] = {}
    for arquivo in sorted(pasta.glob("*.jpg")):
        hora = _hora_da_miniatura(arquivo.name)
        if hora is not None:
            por_hora.setdefault(hora, []).append(arquivo)

    montados = []
    for hora, arquivos in sorted(por_hora.items()):
        if hora >= quando.hour:
            continue  # hora corrente (ou futura, não devia acontecer) — ainda avulsa
        destino = pasta_sprites / f"{hora:02d}.jpg"
        if destino.exists():
            continue
        arquivos.sort(key=lambda a: _minuto_da_miniatura(a.name) or 0)
        montar_sprite_da_hora(arquivos, destino)
        montados.append(destino)
    return montados


def gerar_vtt_do_dia(
    system_root: Path, slug: str, quando: datetime, *, url_base: str = "/v1/thumbs"
) -> str:
    """Sprite pras 24 horas possíveis (as que já fecharam têm sprite; as que não chegaram
    ainda simplesmente não geram cue nenhuma) + avulsas pra hora corrente."""
    pasta = pasta_thumbs_do_dia(system_root, slug, quando)
    pasta_sprites = pasta_sprites_do_dia(system_root, slug, quando)
    data_str = quando.strftime("%Y-%m-%d")

    cues: list[str] = []
    for hora in range(quando.hour + 1):
        offset_s = hora * 3600
        sprite = pasta_sprites / f"{hora:02d}.jpg"
        if sprite.exists():
            url = f"{url_base}/{slug}/{data_str}/sprites/{hora:02d}.jpg"
            cues.extend(gerar_cues_sprite(url, quantidade=60, offset_s=offset_s))
        elif hora == quando.hour and pasta.is_dir():
            arquivos_da_hora = sorted(
                (a for a in pasta.glob(f"{hora:02d}*.jpg")),
                key=lambda a: _minuto_da_miniatura(a.name) or 0,
            )
            urls = [f"{url_base}/{slug}/{data_str}/{a.name}" for a in arquivos_da_hora]
            cues.extend(gerar_cues_avulsas(urls, offset_s=offset_s))

    return montar_webvtt(cues)


def atualizar_dia(system_root: Path, slug: str, quando: datetime | None = None) -> None:
    """Monta sprites de hora fechada pendentes e reescreve o VTT do dia — reescrever tudo
    (não só anexar) é simples e barato: no máximo 1440 cues, ~10KB (plano §11.4)."""
    quando = quando or datetime.now()
    montar_sprites_pendentes(system_root, slug, quando)
    vtt = gerar_vtt_do_dia(system_root, slug, quando)
    pasta_thumbs_do_dia(system_root, slug, quando).mkdir(parents=True, exist_ok=True)
    (pasta_thumbs_do_dia(system_root, slug, quando) / f"{quando:%Y-%m-%d}.vtt").write_text(vtt)


POLL_INTERVAL_S = 300.0  # a cada 5min — mesma cadência do GC, dado leve


async def executar_loop(
    system_root: Path,
    slugs_tv: list[str],
    stop_event: asyncio.Event,
    *,
    poll_interval_s: float = POLL_INTERVAL_S,
    sleep=None,
) -> None:
    """Uma fonte quebrada aqui não pode afetar as outras nem a captura — mesma disciplina
    do supervisor (2026-08-27) e do GC (1.5)."""
    sleep = sleep or asyncio.sleep
    while not stop_event.is_set():
        for slug in slugs_tv:
            try:
                atualizar_dia(system_root, slug)
            except Exception:
                logging.getLogger(__name__).exception("thumbs: erro atualizando dia de %s", slug)

        tarefa_parar = asyncio.ensure_future(stop_event.wait())
        tarefa_dormir = asyncio.ensure_future(sleep(poll_interval_s))
        try:
            await asyncio.wait({tarefa_parar, tarefa_dormir}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for tarefa in (tarefa_parar, tarefa_dormir):
                if not tarefa.done():
                    tarefa.cancel()
