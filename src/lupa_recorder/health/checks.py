"""Checagens de pré-condição da máquina (sub-etapa 1.8, adaptação da lista de 16 itens
do plano §1.8 — os 6 itens de DVB ficam de fora até a 1.3 / GRV-01).

Cada checagem é uma função pura de orquestração sobre I/O injetável — dá pra testar a
lógica de aprovação/reprovação sem `ffmpeg`, `chrony` ou rede de verdade. `rodar_todas`
devolve a lista pro `doctor` imprimir, pro `run` decidir se sobe, e pro timer systemd
gravar como evento no catálogo.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from lupa_recorder.config import Config, Protocol

OFFSET_RELOGIO_MAX_S = 2.0  # plano §1.8 / §19 gap 2: acima disso, captura não deve começar
HOST_DNS_TESTE = "one.one.one.one"


class Status(StrEnum):
    ok = "ok"
    aviso = "aviso"
    falha = "falha"


@dataclass
class Checagem:
    nome: str
    status: Status
    detalhe: str

    @property
    def simbolo(self) -> str:
        return {Status.ok: "✅", Status.aviso: "⚠️ ", Status.falha: "❌"}[self.status]


# ── executores injetáveis (trocados por fakes no teste) ──────────────────────


def _rodar(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


Rodar = Callable[[list[str]], subprocess.CompletedProcess]


# ── checagens individuais ────────────────────────────────────────────────────


def checar_ferramentas(cfg: Config | None = None, *, which=shutil.which) -> list[Checagem]:
    out = []
    for ferramenta in ("ffmpeg", "ffprobe"):
        if which(ferramenta):
            out.append(Checagem(ferramenta, Status.ok, "no PATH"))
        else:
            out.append(Checagem(ferramenta, Status.falha, "não encontrado no PATH"))

    fontes_youtube = (
        sum(s.protocol == Protocol.youtube for s in cfg.channels.sources) if cfg is not None else 0
    )
    if which("yt-dlp"):
        out.append(Checagem("yt-dlp", Status.ok, "no PATH"))
    elif fontes_youtube:
        # com fonte protocol=youtube cadastrada, yt-dlp ausente não é inconveniência: aquela
        # captura fica em loop de erro pra sempre. Vira falha pra aparecer no `doctor`.
        out.append(
            Checagem(
                "yt-dlp",
                Status.falha,
                f"ausente e {fontes_youtube} fonte(s) protocol=youtube cadastrada(s) — rode o bootstrap.sh",
            )
        )
    else:
        out.append(Checagem("yt-dlp", Status.aviso, "ausente — só bloqueia fontes protocol=youtube"))
    return out


def _parsear_offset_chrony(saida: str) -> float | None:
    """`System time     : 0.000000123 seconds slow of NTP time` → 1.23e-7."""
    for linha in saida.splitlines():
        if linha.strip().startswith("System time"):
            partes = linha.split(":", 1)[1].split()
            try:
                return abs(float(partes[0]))
            except (IndexError, ValueError):
                return None
    return None


def checar_relogio(*, which=shutil.which, rodar: Rodar = _rodar) -> Checagem:
    """`chrony` é obrigatório (plano §1.8) e o offset tem que ser < 2s (gap 2) — um
    gravador com relógio errado nomeia os segmentos na hora errada e a barra do dia fica
    mentindo. `aviso` (não `falha`) quando o `chrony` está ausente: quem decide barrar a
    captura é o `run`, e barrar por falta de pacote numa máquina de teste é pior que
    gravar com um relógio provavelmente ok."""
    if not which("chronyc"):
        return Checagem("relógio", Status.aviso, "chronyc ausente — instale chrony (bootstrap.sh faz)")
    try:
        proc = rodar(["chronyc", "tracking"])
    except (OSError, subprocess.SubprocessError) as exc:
        return Checagem("relógio", Status.aviso, f"chronyc falhou: {exc}")
    if proc.returncode != 0:
        return Checagem("relógio", Status.aviso, "chronyc tracking retornou erro (chrony parado?)")
    offset = _parsear_offset_chrony(proc.stdout)
    if offset is None:
        return Checagem("relógio", Status.aviso, "não consegui ler o offset da saída do chronyc")
    if offset > OFFSET_RELOGIO_MAX_S:
        return Checagem("relógio", Status.falha, f"offset {offset:.3f}s > {OFFSET_RELOGIO_MAX_S:.0f}s")
    return Checagem("relógio", Status.ok, f"sincronizado (offset {offset * 1000:.0f} ms)")


def checar_config(cfg: Config | None, erro_carga: str | None) -> list[Checagem]:
    if cfg is None:
        return [Checagem("config", Status.falha, erro_carga or "agent.toml/channels.yaml inválidos")]
    n = len(cfg.channels.sources)
    out = [Checagem("config", Status.ok, f"agent.toml + channels.yaml válidos ({n} fonte(s))")]
    problemas = cfg.validate_environment()
    if problemas:
        out.extend(Checagem("volumes", Status.falha, p) for p in problemas)
    else:
        out.append(Checagem("volumes", Status.ok, "data_root e system_root existem e têm espaço"))
    return out


def checar_escrita(cfg: Config | None) -> list[Checagem]:
    if cfg is None:
        return []
    out = []
    for nome, caminho in (
        ("data_root", cfg.agent.paths.data_root),
        ("system_root", cfg.agent.paths.system_root),
    ):
        teste = caminho / ".lupa-recorder-write-test"
        try:
            teste.write_bytes(b"x")
            teste.unlink()
            out.append(Checagem(f"escrita {nome}", Status.ok, "gravável"))
        except OSError as exc:
            out.append(Checagem(f"escrita {nome}", Status.falha, f"não gravável: {exc}"))
    return out


def checar_porta_http(cfg: Config | None) -> Checagem:
    if cfg is None:
        return Checagem("porta HTTP", Status.aviso, "config não carregou")
    porta = cfg.agent.http.port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", porta))
        return Checagem("porta HTTP", Status.ok, f"{porta}/tcp livre")
    except OSError:
        return Checagem("porta HTTP", Status.aviso, f"{porta}/tcp ocupada (o próprio `run` já rodando?)")
    finally:
        s.close()


def checar_dns(*, resolver=socket.getaddrinfo) -> Checagem:
    try:
        resolver(HOST_DNS_TESTE, 443)
        return Checagem("DNS", Status.ok, f"resolve {HOST_DNS_TESTE}")
    except OSError as exc:
        return Checagem("DNS", Status.aviso, f"não resolveu {HOST_DNS_TESTE}: {exc}")


def checar_tailscale(*, which=shutil.which, rodar: Rodar = _rodar) -> Checagem:
    if not which("tailscale"):
        return Checagem("tailscale", Status.aviso, "ausente — a Lupa não vai enxergar este gravador")
    try:
        proc = rodar(["tailscale", "status"])
    except (OSError, subprocess.SubprocessError) as exc:
        return Checagem("tailscale", Status.aviso, f"tailscale status falhou: {exc}")
    if proc.returncode != 0:
        return Checagem("tailscale", Status.aviso, "não autenticado (rode `tailscale up`)")
    return Checagem("tailscale", Status.ok, "conectado")


def checar_dvb() -> Checagem:
    return Checagem(
        "DVB", Status.aviso, "adaptadores/sinal/kernel homologado — checagem adiada com GRV-01"
    )


# ── orquestração ────────────────────────────────────────────────────────────


def rodar_todas(
    cfg: Config | None,
    *,
    erro_carga_config: str | None = None,
    incluir_rede: bool = True,
) -> list[Checagem]:
    out: list[Checagem] = []
    out.extend(checar_ferramentas(cfg))
    out.append(checar_relogio())
    out.extend(checar_config(cfg, erro_carga_config))
    out.extend(checar_escrita(cfg))
    out.append(checar_porta_http(cfg))
    if incluir_rede:
        out.append(checar_dns())
        out.append(checar_tailscale())
    out.append(checar_dvb())
    return out


def resumir(checagens: list[Checagem]) -> tuple[int, int, int]:
    """`(ok, avisos, falhas)`."""
    return (
        sum(c.status == Status.ok for c in checagens),
        sum(c.status == Status.aviso for c in checagens),
        sum(c.status == Status.falha for c in checagens),
    )


def linha_de_evento(checagens: list[Checagem]) -> str:
    """Resumo de uma linha pro evento no catálogo / `/v1/health`."""
    ok, avisos, falhas = resumir(checagens)
    if falhas:
        nomes = ", ".join(c.nome for c in checagens if c.status == Status.falha)
        return f"doctor: {falhas} falha(s) ({nomes}), {avisos} aviso(s), {ok} ok"
    return f"doctor: verde — {ok} ok, {avisos} aviso(s)"
