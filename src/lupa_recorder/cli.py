"""CLI do lupa-recorder — `lupa-recorder <comando>`.

`probe`/`run`/`status`/`recover`/`doctor`/`bench` completos. `scan`/`signal` (DVB) não
existem — só fazem sentido quando a placa existir (GRV-01).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import tempfile
from datetime import date
from pathlib import Path

from lupa_recorder import __version__
from lupa_recorder.bench import escrever as escrever_bench
from lupa_recorder.bench import rodar_bench
from lupa_recorder.capture.supervisor import SourceSupervisor
from lupa_recorder.catalog.db import conectar
from lupa_recorder.catalog.models import (
    Event,
    SegmentState,
    listar_eventos,
    listar_segmentos,
    registrar_evento,
)
from lupa_recorder.catalog.recover import reconstruir_catalogo_da_fonte, recuperar_orfaos
from lupa_recorder.config import Config, ConfigError
from lupa_recorder.health.checks import (
    Status,
    checar_relogio,
    linha_de_evento,
    resumir,
    rodar_todas,
)
from lupa_recorder.http.app import (
    ContextoServidor,
    encerrar_servidores,
    iniciar_servidores,
    url_player_assinada,
    url_playlist_assinada,
)
from lupa_recorder.probe import ProbeError, ResultadoProbe, probe, resultado_para_json
from lupa_recorder.retention.gc import executar_loop as executar_loop_gc
from lupa_recorder.thumbs.manager import executar_loop as executar_loop_thumbs

NOME_ARQUIVO_CATALOGO = "catalog.sqlite3"

# Layout de instalação (bootstrap.sh / systemd). Um `lupa-recorder doctor` rodado à mão,
# fora de qualquer diretório, ainda tem que achar a config — o systemd resolve isso com
# WorkingDirectory, mas o operador digitando o comando não.
DIRETORIO_CONFIG_SISTEMA = Path("/var/lib/lupa-recorder")


def _resolver_caminho_config(valor: str) -> Path:
    """`--config`/`--channels`: um caminho absoluto ou que já existe no diretório atual vale
    como veio; o default relativo (`agent.toml`, `channels.yaml`), quando não está aqui, cai
    pro layout de instalação em /var/lib/lupa-recorder."""
    p = Path(valor)
    if p.is_absolute() or p.exists():
        return p
    do_sistema = DIRETORIO_CONFIG_SISTEMA / p.name
    return do_sistema if do_sistema.exists() else p

# Comandos DVB — só fazem sentido quando a placa existir (GRV-01). Ficam registrados
# pra dar uma mensagem clara em vez de "invalid choice" do argparse.
COMANDOS_DVB = {"scan", "signal"}


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lupa-recorder", description="Gravador autônomo de TV/rádio da Lupa.")
    parser.add_argument("--version", action="version", version=f"lupa-recorder {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_run = sub.add_parser("run", help="Grava as fontes do channels.yaml e sobe o HTTP local (:8383).")
    p_run.add_argument("--config", default="agent.toml")
    p_run.add_argument("--channels", default="channels.yaml")
    p_run.add_argument(
        "--ignorar-relogio",
        action="store_true",
        help="Sobe a captura mesmo com o relógio fora de sincronia (default: recusa — gap 2).",
    )

    p_status = sub.add_parser("status", help="Mostra o estado atual das capturas.")
    p_status.add_argument("--config", default="agent.toml")
    p_status.add_argument("--channels", default="channels.yaml")

    p_doctor = sub.add_parser("doctor", help="Confere as pré-condições da máquina.")
    p_doctor.add_argument("--config", default="agent.toml")
    p_doctor.add_argument("--channels", default="channels.yaml")
    p_doctor.add_argument("--json", action="store_true", help="Saída em JSON.")
    p_doctor.add_argument(
        "--sem-rede", action="store_true", help="Pula DNS/tailscale (útil pro timer systemd)."
    )

    p_recover = sub.add_parser("recover", help="Varre .part órfão e reconstrói o catálogo.")
    p_recover.add_argument("--config", default="agent.toml")
    p_recover.add_argument("--channels", default="channels.yaml")

    p_bench = sub.add_parser("bench", help="Mede a capacidade da máquina → system_root/bench.json.")
    p_bench.add_argument("--config", default="agent.toml")
    p_bench.add_argument("--channels", default="channels.yaml")
    p_bench.add_argument("--segundos", type=int, default=60, help="Duração da captura de teste por fonte.")
    p_bench.add_argument("--fontes", help="Só estas fontes (slugs separados por vírgula).")

    for dvb in sorted(COMANDOS_DVB):
        sub.add_parser(dvb, help="DVB — indisponível até a placa existir (GRV-01).")

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
    config_path = _resolver_caminho_config(args.config)
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
        print(json.dumps(resultado_para_json(resultado), ensure_ascii=False, indent=2))
    else:
        _imprimir_resultado_probe(resultado)

    return 1 if resultado.erro and not resultado.alcancavel else 0


def _channels_path_ao_lado_do_agent(agent_path: Path) -> Path:
    candidato = agent_path.parent / "channels.yaml"
    return candidato


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


def _carregar_config_tolerante(args: argparse.Namespace) -> tuple[Config | None, str | None]:
    try:
        cfg = Config.load(
            _resolver_caminho_config(args.config), _resolver_caminho_config(args.channels)
        )
        return cfg, None
    except ConfigError as exc:
        return None, str(exc)


def _comando_doctor(args: argparse.Namespace) -> int:
    cfg, erro = _carregar_config_tolerante(args)
    checagens = rodar_todas(cfg, erro_carga_config=erro, incluir_rede=not args.sem_rede)
    ok, avisos, falhas = resumir(checagens)

    if args.json:
        print(
            json.dumps(
                {
                    "resumo": {"ok": ok, "avisos": avisos, "falhas": falhas},
                    "checagens": [
                        {"nome": c.nome, "status": str(c.status), "detalhe": c.detalhe}
                        for c in checagens
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("== lupa-recorder doctor ==\n")
        for c in checagens:
            print(f"{c.simbolo} {c.nome}: {c.detalhe}")
        print(f"\n{ok} ok · {avisos} aviso(s) · {falhas} falha(s)")

    # registra o resultado no catálogo (alimenta o /v1/health da 1.7) — best-effort
    if cfg is not None:
        try:
            conn = conectar(cfg.agent.paths.system_root / NOME_ARQUIVO_CATALOGO)
            registrar_evento(conn, Event(kind="doctor", message=linha_de_evento(checagens)))
            conn.close()
        except Exception:  # noqa: BLE001 — doctor não pode falhar por causa do catálogo
            pass

    return 1 if falhas else 0


def _comando_run(args: argparse.Namespace) -> int:
    # achado ao vivo (2026-08-28): sem isso, `run_forever`/`_matar` logavam sem hora
    # nenhuma — reconstruir a linha do tempo de um `kill -9` real exigia adivinhar pela
    # data de modificação dos arquivos de segmento em vez de olhar o log.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = _carregar_config_ou_falhar(args)
    if cfg is None:
        return 1

    problemas = cfg.validate_environment()
    if problemas:
        for p in problemas:
            print(f"Erro: {p}", file=sys.stderr)
        return 1

    if not cfg.channels.sources:
        print("channels.yaml não tem nenhuma fonte cadastrada — nada pra gravar.", file=sys.stderr)
        return 1

    # gate de relógio (plano §19 gap 2): um gravador com relógio errado nomeia os
    # segmentos na hora errada e a barra do dia fica mentindo — melhor não gravar.
    relogio = checar_relogio()
    if relogio.status == Status.falha and not args.ignorar_relogio:
        print(f"Erro: relógio fora de sincronia ({relogio.detalhe}).", file=sys.stderr)
        print("       Espere o chrony convergir, ou rode com --ignorar-relogio.", file=sys.stderr)
        return 1
    if relogio.status != Status.ok:
        logging.warning("relógio: %s", relogio.detalhe)

    return asyncio.run(_supervisionar_todas_as_fontes(cfg))


def _comando_bench(args: argparse.Namespace) -> int:
    cfg = _carregar_config_ou_falhar(args)
    if cfg is None:
        return 1
    problemas = cfg.validate_environment()
    if problemas:
        for p in problemas:
            print(f"Erro: {p}", file=sys.stderr)
        return 1

    slugs = [s.strip() for s in args.fontes.split(",")] if args.fontes else None
    fontes = [f for f in cfg.channels.sources if slugs is None or f.slug in slugs]
    if not fontes:
        print("nenhuma fonte pra medir.", file=sys.stderr)
        return 1

    print(f"medindo {len(fontes)} fonte(s) por {args.segundos}s cada — pode demorar…")
    with tempfile.TemporaryDirectory(prefix="lupa-recorder-bench-") as tmp:
        resultado = rodar_bench(cfg, Path(tmp), segundos=args.segundos, slugs=slugs)
    alvo = escrever_bench(resultado, cfg.agent.paths.system_root)

    print(json.dumps(resultado.para_json(), ensure_ascii=False, indent=2))
    print(f"\nescrito em {alvo}")
    return 0


async def _supervisionar_todas_as_fontes(cfg: Config) -> int:
    data_root = cfg.agent.paths.data_root
    catalog_conn = conectar(cfg.agent.paths.system_root / NOME_ARQUIVO_CATALOGO)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sinal, stop_event.set)

    # recover roda automaticamente no boot (plano §1.4) — antes do supervisor subir,
    # nunca ao mesmo tempo (os dois mexendo no mesmo .part seria uma corrida).
    for fonte in cfg.channels.sources:
        resultado = recuperar_orfaos(catalog_conn, data_root, fonte.slug)
        if resultado.recuperados or resultado.descartados:
            print(
                f"{fonte.slug}: recover — {len(resultado.recuperados)} recuperado(s), "
                f"{len(resultado.descartados)} descartado(s)"
            )

    system_root = cfg.agent.paths.system_root
    supervisores = [
        SourceSupervisor(fonte, data_root, catalog_conn=catalog_conn, system_root=system_root)
        for fonte in cfg.channels.sources
    ]
    for sup in supervisores:
        print(f"iniciando {sup.source.slug} ({sup.source.protocol})")

    tier_por_fonte = {fonte.slug: fonte.tier for fonte in cfg.channels.sources}
    slugs_tv = [fonte.slug for fonte in cfg.channels.sources if fonte.kind == "tv"]
    tarefas = [sup.run_forever(stop_event) for sup in supervisores]
    tarefas.append(
        executar_loop_gc(
            catalog_conn,
            data_root,
            tier_por_fonte,
            stop_event,
            watermark_high=cfg.agent.retention.watermark_high_data,
            watermark_low=cfg.agent.retention.watermark_low_data,
        )
    )
    if slugs_tv:
        tarefas.append(executar_loop_thumbs(system_root, slugs_tv, stop_event))

    # HTTP local (sub-etapa 1.7) — servidores síncronos em threads próprias, isolados do
    # event loop da captura: um request travado não estagna a vigilância ("a captura
    # sempre ganha"). Abrem a própria conexão só-leitura no catálogo (SQLite não é
    # thread-safe entre conexões compartilhadas).
    ctx = ContextoServidor(
        config=cfg,
        caminho_catalogo=cfg.agent.paths.system_root / NOME_ARQUIVO_CATALOGO,
    )
    servidores: list = []
    try:
        servidores = iniciar_servidores(ctx)
        hoje = date.today().isoformat()
        enderecos = [s.server_address for s in servidores]
        # prefere o IP da tailnet (o que dá pra abrir de outra máquina) pra logar as URLs
        publico = next((a for a in enderecos if not a[0].startswith("127.")), enderecos[0])
        base = f"http://{publico[0]}:{publico[1]}"
        for srv in servidores:
            print(f"HTTP local escutando em http://{srv.server_address[0]}:{srv.server_address[1]}/v1/")
        for fonte in cfg.channels.sources:
            url = url_playlist_assinada(ctx, fonte.slug, hoje, ttl_s=24 * 3600)
            player = url_player_assinada(ctx, fonte.slug, hoje, ttl_s=24 * 3600)
            print(f"  playlist ({fonte.slug}): {base}{url}")
            print(f"  player   ({fonte.slug}): {base}{player}", flush=True)

        await asyncio.gather(*tarefas)
    finally:
        encerrar_servidores(servidores)
        catalog_conn.close()
    print("parado.")
    return 0


def _carregar_config_ou_falhar(args: argparse.Namespace) -> Config | None:
    try:
        return Config.load(
            _resolver_caminho_config(args.config), _resolver_caminho_config(args.channels)
        )
    except ConfigError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return None


def _comando_status(args: argparse.Namespace) -> int:
    cfg = _carregar_config_ou_falhar(args)
    if cfg is None:
        return 1

    caminho_db = cfg.agent.paths.system_root / NOME_ARQUIVO_CATALOGO
    if not caminho_db.exists():
        print("Catálogo ainda não existe — rode `lupa-recorder run` (ou `recover`) primeiro.")
        return 0

    conn = conectar(caminho_db)
    try:
        for fonte in cfg.channels.sources:
            segmentos = listar_segmentos(conn, source_slug=fonte.slug)
            prontos = sum(1 for s in segmentos if s.state == SegmentState.ready)
            parciais = sum(1 for s in segmentos if s.state == SegmentState.partial)
            print(f"{fonte.slug} ({fonte.protocol}): {len(segmentos)} segmento(s) — {prontos} ready, {parciais} partial")
            for evento in listar_eventos(conn, source_slug=fonte.slug, limite=3):
                print(f"    [{evento.kind}] {evento.message}")
    finally:
        conn.close()
    return 0


def _comando_recover(args: argparse.Namespace) -> int:
    cfg = _carregar_config_ou_falhar(args)
    if cfg is None:
        return 1

    problemas = cfg.validate_environment()
    if problemas:
        for p in problemas:
            print(f"Erro: {p}", file=sys.stderr)
        return 1

    conn = conectar(cfg.agent.paths.system_root / NOME_ARQUIVO_CATALOGO)
    try:
        for fonte in cfg.channels.sources:
            resultado = recuperar_orfaos(conn, cfg.agent.paths.data_root, fonte.slug)
            novos, ja_catalogados = reconstruir_catalogo_da_fonte(conn, cfg.agent.paths.data_root, fonte.slug)
            print(
                f"{fonte.slug}: {len(resultado.recuperados)} recuperado(s), "
                f"{len(resultado.descartados)} descartado(s), catálogo — {novos} novo(s), "
                f"{ja_catalogados} já cadastrado(s)"
            )
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _montar_parser()
    args = parser.parse_args(argv)

    comandos = {
        "probe": _comando_probe,
        "doctor": _comando_doctor,
        "run": _comando_run,
        "status": _comando_status,
        "recover": _comando_recover,
        "bench": _comando_bench,
    }
    if args.comando in comandos:
        return comandos[args.comando](args)

    if args.comando in COMANDOS_DVB:
        print(
            f"`lupa-recorder {args.comando}` só faz sentido com placa DVB — adiado com GRV-01.",
            file=sys.stderr,
        )
        return 2
    print(f"`lupa-recorder {args.comando}` não implementado.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
