"""CLI do lupa-recorder — `lupa-recorder <comando>`.

`probe` (1.1) e `run` (1.2) estão completos. `status`/`recover` (1.4) e `bench` (1.8)
ainda não — avisam claramente que não fazem nada, nenhum comando finge funcionar.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from lupa_recorder import __version__
from lupa_recorder.capture.supervisor import SourceSupervisor
from lupa_recorder.config import Config, ConfigError
from lupa_recorder.probe import ProbeError, ResultadoProbe, probe

COMANDOS_AINDA_NAO_IMPLEMENTADOS = {
    "status": "sub-etapa 1.4 (catálogo)",
    "doctor": "sub-etapa 1.8 (lista completa) — versão parcial já roda, ver abaixo",
    "recover": "sub-etapa 1.4 (catálogo)",
    "bench": "sub-etapa 1.8",
}


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lupa-recorder", description="Gravador autônomo de TV/rádio da Lupa.")
    parser.add_argument("--version", action="version", version=f"lupa-recorder {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_run = sub.add_parser("run", help="Roda o supervisor de captura (sub-etapa 1.2).")
    p_run.add_argument("--config", default="agent.toml")
    p_run.add_argument("--channels", default="channels.yaml")

    sub.add_parser("status", help="Mostra o estado atual das capturas (sub-etapa 1.4).")

    p_doctor = sub.add_parser("doctor", help="Confere pré-condições da máquina.")
    p_doctor.add_argument("--config", default="agent.toml")
    p_doctor.add_argument("--channels", default="channels.yaml")

    sub.add_parser("recover", help="Varre .part órfão e reconstrói o catálogo (sub-etapa 1.4).")
    sub.add_parser("bench", help="Mede capture_budget/vad_hours_per_day (sub-etapa 1.8).")

    p_probe = sub.add_parser("probe", help="Testa uma URL de fonte e sugere o cadastro em channels.yaml.")
    p_probe.add_argument("url")
    p_probe.add_argument("--dvb", action="store_true", help="Reservado — DVB ainda não suportado (GRV-01).")
    p_probe.add_argument("--json", action="store_true", help="Saída em JSON (pra tela da Lupa consumir).")
    p_probe.add_argument("--config", default="agent.toml", help="Pra saber o disco livre e projetar retenção.")
    p_probe.add_argument("--segundos", type=int, default=20, help="Duração do teste de captura real.")
    p_probe.add_argument("--sem-captura", action="store_true", help="Pula o teste de captura real (só metadata).")

    return parser


def _comando_probe(args: argparse.Namespace) -> int:
    if args.dvb:
        print("--dvb ainda não é suportado nesta fase (GRV-01 — placa adiada por custo).", file=sys.stderr)
        return 1

    disco_livre_gb = None
    dias_retencao = 5
    config_path = Path(args.config)
    if config_path.exists():
        try:
            cfg = Config.load(agent_path=config_path, channels_path=_channels_path_ao_lado_do_agent(config_path))
            disco_livre_gb = cfg.agent.paths.disco_do_acervo_disponivel_gb()
        except ConfigError:
            pass  # probe funciona sem config — só perde a projeção de disco

    with tempfile.TemporaryDirectory(prefix="lupa-recorder-probe-") as tmp:
        try:
            resultado = probe(
                args.url,
                disco_livre_gb=disco_livre_gb,
                dias_retencao=dias_retencao,
                testar_captura_real=not args.sem_captura,
                segundos_teste=args.segundos,
                diretorio_scratch=Path(tmp),
            )
        except ProbeError as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(_resultado_para_json(resultado), ensure_ascii=False, indent=2))
    else:
        _imprimir_resultado_probe(resultado)

    return 1 if resultado.erro and not resultado.alcancavel else 0


def _channels_path_ao_lado_do_agent(agent_path: Path) -> Path:
    candidato = agent_path.parent / "channels.yaml"
    return candidato


def _resultado_para_json(r: ResultadoProbe) -> dict:
    d = dataclasses.asdict(r)
    d["gb_por_dia_projetado"] = r.gb_por_dia_projetado
    d["cabe_no_disco"] = r.cabe_no_disco
    d["cadastro_sugerido"] = r.cadastro_sugerido()
    return d


def _imprimir_resultado_probe(r: ResultadoProbe) -> None:
    print(f"▸ Resolvendo {r.url}")
    print(f"  tipo detectado ......... {r.protocolo_detectado}")
    print(f"  alcançável .............. {'sim' if r.alcancavel else 'não'}" + (
        f" (HTTP, {r.latencia_ms:.0f} ms)" if r.latencia_ms else ""
    ))
    if r.erro:
        print(f"  erro .................... {r.erro}")
    print(f"  token na URL ............ {'sim' if r.tem_token else 'não'}")

    if r.renditions:
        print(f"  renditions disponíveis ({len(r.renditions)}):")
        for rend in r.renditions:
            marca = "  ← recomendada" if rend is r.rendition_recomendada else ""
            resolucao = rend.resolution or "—"
            bw = f"{rend.bandwidth_bps / 1_000_000:.1f} Mbps" if rend.bandwidth_bps else "—"
            print(f"     {resolucao:>10}   {bw:>10}{marca}")

    if r.faixas_audio is not None:
        print(f"  faixas de áudio ......... {r.faixas_audio}")
    if r.legendas is not None:
        print(f"  legendas ................ {r.legendas}")

    if r.captura:
        print(f"\n▸ Teste de captura ({r.captura.duracao_s:.0f}s)")
        print(f"  bitrate real medido ..... {r.captura.bitrate_bps / 1_000_000:.2f} Mbps")
        print(f"  projeção nesta máquina .. {r.captura.gb_por_dia_real:.1f} GB/dia")

    if r.gb_por_dia_projetado is not None:
        dias = r.dias_retencao
        projecao = r.gb_por_dia_projetado * dias
        status = "✅ cabe" if r.cabe_no_disco else "❌ não cabe"
        livre = f"{r.disco_livre_gb:.0f} GB" if r.disco_livre_gb is not None else "?"
        print(f"                           {r.gb_por_dia_projetado:.1f} GB/dia × {dias} dias = {projecao:.0f} GB")
        print(f"                           acervo livre: {livre}   {status}")

    elif r.captura is None and r.renditions and r.rendition_recomendada and r.rendition_recomendada.bandwidth_bps == 0:
        print("\n  (projeção de disco indisponível — rode sem --sem-captura pra medir o bitrate real)")

    print("\n▸ Cadastro sugerido:")
    for chave, valor in r.cadastro_sugerido().items():
        print(f"    {chave:<16} {valor}")


def _comando_doctor(args: argparse.Namespace) -> int:
    print("== lupa-recorder doctor (versão parcial — completa na sub-etapa 1.8) ==\n")
    problemas: list[str] = []

    for ferramenta in ("ffmpeg", "ffprobe"):
        if shutil.which(ferramenta):
            print(f"✅ {ferramenta} encontrado")
        else:
            print(f"❌ {ferramenta} não encontrado no PATH")
            problemas.append(f"{ferramenta} ausente")

    if shutil.which("yt-dlp"):
        print("✅ yt-dlp encontrado")
    else:
        print("⚠️  yt-dlp não encontrado (só bloqueia fontes protocol=youtube)")

    config_path = Path(args.config)
    channels_path = Path(args.channels)
    if not config_path.exists():
        print(f"❌ {config_path} não existe (copie de agent.toml.example)")
        problemas.append("agent.toml ausente")
    if not channels_path.exists():
        print(f"❌ {channels_path} não existe (copie de channels.yaml.example)")
        problemas.append("channels.yaml ausente")

    if not problemas:
        try:
            cfg = Config.load(config_path, channels_path)
            print(f"✅ {config_path} e {channels_path} válidos ({len(cfg.channels.sources)} fonte(s))")
            ambiente = cfg.validate_environment()
            if ambiente:
                for p in ambiente:
                    print(f"❌ {p}")
                problemas.extend(ambiente)
            else:
                print("✅ data_root e system_root existem e têm espaço")
        except ConfigError as exc:
            print(f"❌ {exc}")
            problemas.append(str(exc))

    print()
    if problemas:
        print(f"{len(problemas)} problema(s) encontrado(s).")
        return 1
    print("Tudo certo (do que já é verificável nesta sub-etapa).")
    return 0


def _comando_run(args: argparse.Namespace) -> int:
    # achado ao vivo (2026-08-28): sem isso, `run_forever`/`_matar` logavam sem hora
    # nenhuma — reconstruir a linha do tempo de um `kill -9` real exigia adivinhar pela
    # data de modificação dos arquivos de segmento em vez de olhar o log.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config_path = Path(args.config)
    channels_path = Path(args.channels)
    try:
        cfg = Config.load(config_path, channels_path)
    except ConfigError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    problemas = cfg.validate_environment()
    if problemas:
        for p in problemas:
            print(f"Erro: {p}", file=sys.stderr)
        return 1

    if not cfg.channels.sources:
        print("channels.yaml não tem nenhuma fonte cadastrada — nada pra gravar.", file=sys.stderr)
        return 1

    return asyncio.run(_supervisionar_todas_as_fontes(cfg))


async def _supervisionar_todas_as_fontes(cfg: Config) -> int:
    data_root = cfg.agent.paths.data_root
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sinal, stop_event.set)

    supervisores = [SourceSupervisor(fonte, data_root) for fonte in cfg.channels.sources]
    for sup in supervisores:
        print(f"iniciando {sup.source.slug} ({sup.source.protocol})")

    await asyncio.gather(*(sup.run_forever(stop_event) for sup in supervisores))
    print("parado.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _montar_parser()
    args = parser.parse_args(argv)

    if args.comando == "probe":
        return _comando_probe(args)
    if args.comando == "doctor":
        return _comando_doctor(args)
    if args.comando == "run":
        return _comando_run(args)

    aviso = COMANDOS_AINDA_NAO_IMPLEMENTADOS.get(args.comando)
    print(f"`lupa-recorder {args.comando}` ainda não está implementado — chega na {aviso}.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
