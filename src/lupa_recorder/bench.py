"""`lupa-recorder bench` — mede os números de capacidade da máquina (plano §6.5) e
escreve `{system_root}/bench.json`. Princípio #5 do plano: nada de capacidade prometida
sem medição na máquina real.

Dos 5 números, esta fase mede os 2 que fazem sentido sem DVB nem VAD:

- `archive_bytes_per_day` — do bitrate **real** capturado de cada fonte (não o nominal).
  É o número acionável: a Lupa recusa vincular fontes que estourem `0,85 × disco_acervo`.
- `capture_budget` — estimativa a partir da folga de carga durante a captura. Captura
  `-c copy` é barata (bench.md: `load 0,46` com 7 fontes) — o teto real é disco/rede, não
  CPU; a estimativa vem com essa ressalva na saída.

`transcode_budget` / `dvb_adapters` / `vad_hours_per_day` ficam `null`/`0` até a 1.3 e a
Fase 3 — o schema JSON já nasce completo pra não migrar depois.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lupa_recorder.config import Config, Protocol, SourceConfig
from lupa_recorder.probe import ResultadoCaptura, resolver_youtube, testar_captura

Capturar = Callable[..., ResultadoCaptura]
CAPTURE_BUDGET_TETO = 99  # acima disso a estimativa não é útil — o gargalo vira disco/rede


def caminho_do_arquivo(system_root: Path) -> Path:
    return system_root / "bench.json"


@dataclass
class MedidaDeFonte:
    slug: str
    kind: str
    bitrate_bps: int | None = None
    gb_por_dia: float | None = None
    erro: str | None = None


@dataclass
class ResultadoBench:
    medido_em: str
    maquina: str
    duracao_teste_s: int
    fontes: list[MedidaDeFonte]
    capture_budget: int | None
    archive_bytes_per_day: int
    carga_1min_durante: float
    nucleos: int
    transcode_budget: None = None
    dvb_adapters: int = 0
    vad_hours_per_day: None = None
    notas: list[str] = field(default_factory=list)

    def para_json(self) -> dict:
        return {
            "medido_em": self.medido_em,
            "maquina": self.maquina,
            "duracao_teste_s": self.duracao_teste_s,
            "capture_budget": self.capture_budget,
            "transcode_budget": self.transcode_budget,
            "dvb_adapters": self.dvb_adapters,
            "vad_hours_per_day": self.vad_hours_per_day,
            "archive_bytes_per_day": self.archive_bytes_per_day,
            "fontes": [vars(m) for m in self.fontes],
            "carga": {"1min_durante": self.carga_1min_durante, "nucleos": self.nucleos},
            "notas": self.notas,
        }


def _medir_uma_fonte(
    source: SourceConfig,
    diretorio: Path,
    segundos: int,
    *,
    capturar: Capturar = testar_captura,
    resolver_yt=resolver_youtube,
) -> MedidaDeFonte:
    destino = diretorio / f"{source.slug}.ts"
    try:
        if source.protocol == Protocol.youtube:
            url_video, url_audio = resolver_yt(source.url or "", source.quality_profile or "480p")
            r = capturar(url_video, "youtube", destino, segundos, url_audio_youtube=url_audio)
        else:
            r = capturar(source.url or "", str(source.protocol), destino, segundos)
    except Exception as exc:  # noqa: BLE001 — bench nunca aborta por uma fonte; registra o erro
        return MedidaDeFonte(source.slug, str(source.kind), erro=str(exc))
    return MedidaDeFonte(
        slug=source.slug,
        kind=str(source.kind),
        bitrate_bps=round(r.bitrate_bps),
        gb_por_dia=round(r.gb_por_dia_real, 2),
    )


def _carga_1min() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return 0.0


def _estimar_capture_budget(
    carga_durante: float, carga_base: float, fontes_ok: int, nucleos: int
) -> int | None:
    if fontes_ok == 0:
        return None
    delta_por_fonte = max((carga_durante - carga_base) / fontes_ok, 0.0)
    if delta_por_fonte < 0.02:
        return CAPTURE_BUDGET_TETO  # captura praticamente de graça — teto é disco/rede
    return min(int(nucleos * 0.75 / delta_por_fonte), CAPTURE_BUDGET_TETO)


def rodar_bench(
    cfg: Config,
    diretorio_scratch: Path,
    *,
    segundos: int = 60,
    slugs: list[str] | None = None,
    medir_fonte: Callable[..., MedidaDeFonte] = _medir_uma_fonte,
    carga=_carga_1min,
) -> ResultadoBench:
    fontes = cfg.channels.sources
    if slugs:
        fontes = [f for f in fontes if f.slug in slugs]

    carga_base = carga()
    medidas = [medir_fonte(f, diretorio_scratch, segundos) for f in fontes]
    carga_durante = carga()

    nucleos = os.cpu_count() or 1
    ok = [m for m in medidas if m.erro is None and m.bitrate_bps]
    archive_bps = sum(m.bitrate_bps or 0 for m in ok)
    archive_bytes_dia = round(archive_bps / 8 * 86400)

    notas = [
        "transcode_budget / dvb_adapters / vad_hours_per_day não medidos nesta fase "
        "(GRV-01 — DVB adiado; VAD é Fase 3).",
        "capture_budget é estimativa pela folga de carga — o teto real de captura -c copy "
        "é throughput de disco e banda de rede, não CPU.",
    ]
    if any(m.erro for m in medidas):
        falhas = ", ".join(f"{m.slug} ({m.erro})" for m in medidas if m.erro)
        notas.append(f"fontes que não mediram: {falhas}")

    return ResultadoBench(
        medido_em=datetime.now(UTC).isoformat(timespec="seconds"),
        maquina=cfg.agent.agent.name,
        duracao_teste_s=segundos,
        fontes=medidas,
        capture_budget=_estimar_capture_budget(carga_durante, carga_base, len(ok), nucleos),
        archive_bytes_per_day=archive_bytes_dia,
        carga_1min_durante=carga_durante,
        nucleos=nucleos,
        notas=notas,
    )


def escrever(resultado: ResultadoBench, system_root: Path) -> Path:
    alvo = caminho_do_arquivo(system_root)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    tmp = alvo.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(resultado.para_json(), indent=2, ensure_ascii=False))
    tmp.replace(alvo)
    return alvo
