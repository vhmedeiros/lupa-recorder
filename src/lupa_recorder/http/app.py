"""O servidor HTTP local — `ThreadingHTTPServer` da stdlib, roteamento por regex.

Escuta em `127.0.0.1` **e** no IP da tailnet, **nunca** em `0.0.0.0` (plano §10, §11.3):
mesmo que alguém abrisse porta no roteador por engano, não há serviço na interface
pública. O IP da tailnet é autodetectado (`tailscale ip -4`, com fallback varrendo
interfaces); `agent.toml` pode fixá-lo ou desligar o bind da tailnet (teste local).

Roda em threads próprias, fora do event loop da captura — um request travado não
estagna a vigilância. Cada request abre a própria conexão só-leitura no catálogo
(`conectar_leitura`), porque conexão SQLite não atravessa thread.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import logging
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lupa_recorder.catalog.db import conectar_leitura
from lupa_recorder.catalog.models import (
    SegmentState,
    listar_eventos,
    listar_segmentos_do_dia,
    resumo_segmentos,
)
from lupa_recorder.config import Config, SourceConfig
from lupa_recorder.http.auth import (
    TTL_PADRAO_S,
    query_escopo_assinada,
    verificar,
    verificar_escopo,
)
from lupa_recorder.http.playlist import EntradaSegmento, montar_playlist
from lupa_recorder.probe import ProbeError, ResultadoProbe, probe, resultado_para_json

logger = logging.getLogger(__name__)

# Faixa CGNAT que o Tailscale usa (100.64.0.0/10) — RFC 6598.
REDE_TAILNET = ipaddress.ip_network("100.64.0.0/10")
SIOCGIFADDR = 0x8915  # ioctl Linux: "me dá o IPv4 desta interface"

ROTA_PLAY = re.compile(r"^/v1/play/([a-z0-9-]+)/(\d{4}-\d{2}-\d{2})\.m3u8$")
ROTA_SEG = re.compile(r"^/v1/seg/([a-z0-9-]+)/(\d{4}-\d{2}-\d{2})/(\d{6})\.ts$")
ROTA_THUMBS = re.compile(r"^/v1/thumbs/([a-z0-9-]+)/(.+)$")
_RE_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
_COMPONENTE_SEGURO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_TIPO_POR_SUFIXO = {
    ".ts": "video/mp2t",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".vtt": "text/vtt; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


# ── contexto compartilhado ────────────────────────────────────────────────────


@dataclass
class ContextoServidor:
    config: Config
    caminho_catalogo: Path
    iniciado_em: float = field(default_factory=time.time)
    # injetável pra teste (o probe real chama ffmpeg; nos testes é um fake).
    probe_fn: Callable[..., ResultadoProbe] = probe

    @property
    def secret(self) -> str:
        return self.config.agent.security.hmac_secret

    @property
    def slugs(self) -> frozenset[str]:
        return frozenset(s.slug for s in self.config.channels.sources)

    def fonte(self, slug: str) -> SourceConfig | None:
        return next((s for s in self.config.channels.sources if s.slug == slug), None)

    def abrir_catalogo(self):
        try:
            return conectar_leitura(self.caminho_catalogo)
        except (FileNotFoundError, OSError):
            return None  # ninguém gravou ainda — endpoints devolvem vazio, não erro


def url_playlist_assinada(
    ctx: ContextoServidor, slug: str, data_iso: str, *, ttl_s: int = TTL_PADRAO_S
) -> str:
    """`/v1/play/{slug}/{data}.m3u8?e=&s=` — usado pelo `run` pra logar uma URL pronta
    pra colar no VLC (o critério de aceite da sub-etapa 1.7). O token é de **escopo**
    `(fonte, dia)` — o mesmo `?e=&s=` cobre a playlist, os segmentos e as miniaturas
    daquele dia, e a playlist ecoa esse token em cada linha (URLs estáveis entre
    recargas da playlist `EVENT`)."""
    query = query_escopo_assinada(ctx.secret, slug, data_iso, ttl_s=ttl_s)
    return f"/v1/play/{slug}/{data_iso}.m3u8?{query}"


def _data_dos_thumbs(resto: str) -> str | None:
    """A data (= escopo) de um path de miniatura: primeiro componente, ou o stem de
    `{data}.vtt`."""
    primeiro = resto.split("/", 1)[0]
    if primeiro.endswith(".vtt"):
        primeiro = primeiro[:-4]
    return primeiro if _RE_DATA.fullmatch(primeiro) else None


# ── detecção do IP da tailnet ─────────────────────────────────────────────────


def _e_ip_tailnet(valor: str) -> bool:
    try:
        return ipaddress.ip_address(valor) in REDE_TAILNET
    except ValueError:
        return False


def _ip_da_interface(nome: str) -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        req = struct.pack("256s", nome.encode()[:15])
        resp = fcntl.ioctl(s.fileno(), SIOCGIFADDR, req)
        return socket.inet_ntoa(resp[20:24])
    except OSError:
        return None
    finally:
        s.close()


def detectar_ip_tailnet(cfg: Config) -> str | None:
    if cfg.agent.http.tailnet_ip:
        return cfg.agent.http.tailnet_ip
    try:
        saida = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5, check=False
        )
        for linha in saida.stdout.splitlines():
            if _e_ip_tailnet(linha.strip()):
                return linha.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    # fallback: varre interfaces (o gravador é sempre Linux — plano §12).
    for _, nome in socket.if_nameindex():
        ip = _ip_da_interface(nome)
        if ip and _e_ip_tailnet(ip):
            return ip
    return None


# ── segurança de caminho ─────────────────────────────────────────────────────


def resolver_dentro(base: Path, *partes: str) -> Path | None:
    """Junta `partes` sob `base` e confirma que o resultado **não escapou** de `base`
    (defesa em profundidade — as rotas já validam cada componente por regex)."""
    base_abs = base.resolve()
    alvo = (base_abs / Path(*partes)).resolve()
    if alvo == base_abs or base_abs in alvo.parents:
        return alvo
    return None


def _parsear_range(header: str | None, tamanho: int) -> tuple[int, int] | str | None:
    """`None` = sem Range (200 inteiro). `str` = Range inválido (416). `(ini, fim)`
    inclusive = 206. Multi-range cai em `None` (servir inteiro é permitido pela spec)."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :]
    if "," in spec:
        return None
    ini_txt, _, fim_txt = spec.partition("-")
    try:
        if ini_txt == "":
            n = int(fim_txt)
            if n <= 0:
                return "invalido"
            inicio, fim = max(0, tamanho - n), tamanho - 1
        else:
            inicio = int(ini_txt)
            fim = int(fim_txt) if fim_txt else tamanho - 1
    except ValueError:
        return "invalido"
    if inicio > fim or inicio >= tamanho:
        return "invalido"
    return (inicio, min(fim, tamanho - 1))


# ── handler ──────────────────────────────────────────────────────────────────


class _HandlerHttp(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lupa-recorder"

    @property
    def ctx(self) -> ContextoServidor:
        return self.server.ctx  # type: ignore[attr-defined]

    def log_message(self, formato: str, *args) -> None:
        logger.debug("%s - %s", self.address_string(), formato % args)

    def end_headers(self) -> None:
        # CORS liberado: o Estúdio (browser servido pela Lupa) e o hls.js buscam a
        # playlist/segmentos/miniaturas do agente por outra origem. Seguro aqui — o
        # agente só é alcançável pela tailnet e toda rota (menos /v1/health) exige o
        # token HMAC na URL; `*` não expõe nada que o token já não proteja.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")
        super().end_headers()

    # -- dispatch --

    def do_OPTIONS(self) -> None:  # noqa: N802 — preflight do browser (Range dispara CORS preflight)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (nome exigido pela stdlib)
        partes = urlsplit(self.path)
        params = {k: v[0] for k, v in parse_qs(partes.query).items()}
        try:
            self._despachar_get(partes.path, params, partes.query)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("erro tratando GET %s", self.path)
            self._talvez_erro_500()

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        partes = urlsplit(self.path)
        params = {k: v[0] for k, v in parse_qs(partes.query).items()}
        try:
            if not self._auth_path_ok(partes.path, params):
                return
            if partes.path == "/v1/probe":
                self._rota_probe()
            elif partes.path == "/v1/scan":
                self._erro(501, "scan DVB ainda não é suportado (GRV-01 — placa adiada por custo)")
            elif partes.path == "/v1/clip":
                self._erro(501, "corte de clipe chega na Fase 2")
            else:
                self._erro(404, "rota desconhecida")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("erro tratando POST %s", self.path)
            self._talvez_erro_500()

    def _despachar_get(self, caminho: str, params: dict[str, str], query_bruta: str) -> None:
        if caminho == "/v1/health":
            return self._rota_health()
        if caminho == "/v1/status":
            if self._auth_path_ok(caminho, params):
                self._rota_status()
            return
        if m := ROTA_PLAY.match(caminho):
            slug, data_iso = m.groups()
            if self._auth_escopo_ok(slug, data_iso, params):
                self._rota_play(slug, data_iso, query_bruta)
            return
        if m := ROTA_SEG.match(caminho):
            slug, data_iso, hhmmss = m.groups()
            if self._auth_escopo_ok(slug, data_iso, params):
                self._rota_seg(slug, data_iso, hhmmss)
            return
        if m := ROTA_THUMBS.match(caminho):
            slug, resto = m.groups()
            data_iso = _data_dos_thumbs(resto)
            if data_iso is None:
                return self._erro(400, "caminho de miniatura inválido")
            if self._auth_escopo_ok(slug, data_iso, params):
                self._rota_thumbs(slug, resto, query_bruta)
            return
        self._erro(404, "rota desconhecida")

    def _auth_path_ok(self, caminho: str, params: dict[str, str]) -> bool:
        if verificar(self.ctx.secret, caminho, params):
            return True
        self._erro(401, "token ausente, expirado ou inválido (?e=&s=)")
        return False

    def _auth_escopo_ok(self, fonte: str, dia: str, params: dict[str, str]) -> bool:
        if verificar_escopo(self.ctx.secret, fonte, dia, params):
            return True
        self._erro(401, "token de escopo ausente, expirado ou inválido (?e=&s=)")
        return False

    # -- rotas --

    def _rota_health(self) -> None:
        problemas = list(self.ctx.config.validate_environment())
        if not all(shutil.which(t) for t in ("ffmpeg", "ffprobe")):
            problemas.append("ffmpeg/ffprobe ausente do PATH")
        ok = not problemas
        corpo = {
            "status": "ok" if ok else "degraded",
            "problemas": problemas,
            "uptime_s": self._uptime(),
            "doctor": self._ultimo_doctor(),  # último resultado do timer systemd (1.8)
        }
        self._json(corpo, status=200 if ok else 503)

    def _ultimo_doctor(self) -> str | None:
        conn = self.ctx.abrir_catalogo()
        if conn is None:
            return None
        try:
            eventos = listar_eventos(conn, limite=50)
        finally:
            conn.close()
        return next((e.message for e in eventos if e.kind == "doctor"), None)

    def _rota_status(self) -> None:
        paths = self.ctx.config.agent.paths
        sistema = shutil.disk_usage(paths.system_root)
        acervo = shutil.disk_usage(paths.data_root)
        conn = self.ctx.abrir_catalogo()
        fontes = []
        try:
            for fonte in self.ctx.config.channels.sources:
                info: dict = {
                    "slug": fonte.slug,
                    "kind": str(fonte.kind),
                    "protocol": str(fonte.protocol),
                    "tier": str(fonte.tier),
                }
                if conn is not None:
                    total, ultimo = resumo_segmentos(conn, fonte.slug)
                    info["segmentos"] = total
                    info["ultimo_segmento"] = ultimo
                    info["eventos_recentes"] = [
                        {"kind": e.kind, "message": e.message}
                        for e in listar_eventos(conn, source_slug=fonte.slug, limite=3)
                    ]
                fontes.append(info)
        finally:
            if conn is not None:
                conn.close()
        self._json(
            {
                "uptime_s": self._uptime(),
                "volumes": {
                    "system": {"total": sistema.total, "usado": sistema.used, "livre": sistema.free},
                    "acervo": {"total": acervo.total, "usado": acervo.used, "livre": acervo.free},
                },
                "fontes": fontes,
            }
        )

    def _rota_play(self, slug: str, data_iso: str, query_bruta: str) -> None:
        fonte = self.ctx.fonte(slug)
        if fonte is None:
            return self._erro(404, f"fonte {slug!r} não cadastrada")

        conn = self.ctx.abrir_catalogo()
        segmentos = []
        try:
            if conn is not None:
                segmentos = listar_segmentos_do_dia(conn, slug, data_iso)
        finally:
            if conn is not None:
                conn.close()

        dia_corrente = data_iso == date.today().isoformat()
        entradas: list[EntradaSegmento] = []
        for seg in segmentos:
            if seg.state == SegmentState.purged:
                continue
            hhmmss = seg.started_at.split("T")[1].replace(":", "")
            # ecoa o mesmo token de escopo do request — URL idêntica em toda recarga da
            # playlist EVENT (o token cobre `(fonte, dia)`, igual pra todo segmento do dia)
            url_seg = f"/v1/seg/{slug}/{data_iso}/{hhmmss}.ts?{query_bruta}"
            entradas.append(
                EntradaSegmento(
                    started_at=datetime.fromisoformat(seg.started_at).astimezone(),
                    url=url_seg,
                    duration_ms=seg.duration_ms,
                    partial=seg.state == SegmentState.partial,
                )
            )

        m3u8 = montar_playlist(
            entradas,
            dia_corrente=dia_corrente,
            segment_seconds=fonte.segment_seconds,
            agora=datetime.now().astimezone() if dia_corrente else None,
        )
        self._corpo(m3u8.encode(), "application/vnd.apple.mpegurl")

    def _rota_seg(self, slug: str, data_iso: str, hhmmss: str) -> None:
        if slug not in self.ctx.slugs:
            return self._erro(404, f"fonte {slug!r} não cadastrada")
        alvo = resolver_dentro(self.ctx.config.agent.paths.data_root, slug, data_iso, f"{hhmmss}.ts")
        if alvo is None or not alvo.is_file():
            return self._erro(404, "segmento não encontrado")
        self._servir_arquivo(alvo, "video/mp2t")

    def _rota_thumbs(self, slug: str, resto: str, query_bruta: str) -> None:
        if slug not in self.ctx.slugs:
            return self._erro(404, f"fonte {slug!r} não cadastrada")
        partes = resto.split("/")
        if any(not _COMPONENTE_SEGURO.match(p) for p in partes):
            return self._erro(400, "caminho de miniatura inválido")
        base = self.ctx.config.agent.paths.system_root / "thumbs" / slug
        # a URL canônica da §11.3 é `/v1/thumbs/{slug}/{data}.vtt`, mas o `thumbs/manager.py`
        # grava o VTT DENTRO da pasta do dia (`{data}/{data}.vtt`) — traduz aqui. Sprites e
        # avulsas já vêm com a pasta do dia na URL (o VTT as emite assim), caem no caso geral.
        if len(partes) == 1 and partes[0].endswith(".vtt"):
            partes = [partes[0][: -len(".vtt")], partes[0]]
        alvo = resolver_dentro(base, *partes)
        if alvo is None or not alvo.is_file():
            return self._erro(404, "miniatura não encontrada")
        if alvo.suffix.lower() == ".vtt":
            return self._servir_vtt(alvo, query_bruta)
        self._servir_arquivo(alvo, _TIPO_POR_SUFIXO.get(alvo.suffix.lower(), "application/octet-stream"))

    def _servir_vtt(self, caminho: Path, query_bruta: str) -> None:
        """O VTT que o `thumbs/manager.py` grava tem URLs de sprite/miniatura sem token —
        o servidor ecoa o mesmo token de escopo do request em cada uma (o token cobre
        `(fonte, dia)`, então vale pras imagens do mesmo dia). Sem isso o player recebe
        o VTT mas toma 401 em toda imagem da filmstrip (bug de campo 2026-08-29). VTT é
        pequeno (≤~40KB) — sem Range."""
        try:
            texto = caminho.read_text()
        except OSError:
            return self._erro(404, "não encontrado")
        linhas = []
        for linha in texto.splitlines():
            if linha.startswith("/v1/thumb"):
                base, _, frag = linha.partition("#")
                linha = f"{base}?{query_bruta}" + (f"#{frag}" if frag else "")
            linhas.append(linha)
        self._corpo(("\n".join(linhas) + "\n").encode(), "text/vtt; charset=utf-8")

    def _rota_probe(self) -> None:
        try:
            dados = json.loads(self._ler_corpo() or b"{}")
        except ValueError:
            return self._erro(400, "corpo não é JSON válido")
        url = dados.get("url")
        if not url:
            return self._erro(400, "campo 'url' é obrigatório")
        try:
            disco = self.ctx.config.agent.paths.disco_do_acervo_disponivel_gb()
        except OSError:
            disco = None
        with tempfile.TemporaryDirectory(prefix="lupa-recorder-probe-") as tmp:
            try:
                resultado = self.ctx.probe_fn(
                    url,
                    disco_livre_gb=disco,
                    testar_captura_real=not bool(dados.get("sem_captura", False)),
                    segundos_teste=int(dados.get("segundos", 20)),
                    diretorio_scratch=Path(tmp),
                )
            except ProbeError as exc:
                return self._erro(400, str(exc))
        self._json(resultado_para_json(resultado))

    # -- utilidades de resposta --

    def _uptime(self) -> float:
        return round(time.time() - self.ctx.iniciado_em, 1)

    def _ler_corpo(self) -> bytes:
        tamanho = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(tamanho) if tamanho > 0 else b""

    def _json(self, obj: dict, *, status: int = 200) -> None:
        self._corpo(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def _erro(self, status: int, mensagem: str) -> None:
        self._corpo(json.dumps({"error": mensagem}).encode(), "application/json; charset=utf-8", status)

    def _corpo(self, dados: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(dados)

    def _talvez_erro_500(self) -> None:
        try:
            self._erro(500, "erro interno")
        except Exception:
            pass

    def _servir_arquivo(self, caminho: Path, content_type: str) -> None:
        try:
            tamanho = caminho.stat().st_size
        except OSError:
            return self._erro(404, "não encontrado")

        faixa = _parsear_range(self.headers.get("Range"), tamanho)
        if faixa == "invalido":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{tamanho}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if faixa is None:
            inicio, fim, status = 0, tamanho - 1, 200
        else:
            inicio, fim = faixa  # type: ignore[misc]
            status = 206

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - inicio + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {inicio}-{fim}/{tamanho}")
        self.end_headers()

        if self.command == "HEAD":
            return
        restante = fim - inicio + 1
        try:
            with caminho.open("rb") as fh:
                fh.seek(inicio)
                while restante > 0:
                    bloco = fh.read(min(65536, restante))
                    if not bloco:
                        break
                    self.wfile.write(bloco)
                    restante -= len(bloco)
        except (BrokenPipeError, ConnectionResetError):
            pass  # cliente pulou/fechou — normal com vídeo


# ── servidor ─────────────────────────────────────────────────────────────────


class ServidorHttp(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    ctx: ContextoServidor

    def __init__(self, endereco: tuple[str, int], ctx: ContextoServidor) -> None:
        super().__init__(endereco, _HandlerHttp)
        self.ctx = ctx


def criar_servidor(ctx: ContextoServidor, host: str, port: int) -> ServidorHttp:
    return ServidorHttp((host, port), ctx)


def iniciar_servidores(ctx: ContextoServidor) -> list[ServidorHttp]:
    """Um `ServidorHttp` por endereço de bind — sempre loopback, mais o IP da tailnet
    quando detectado. **Nunca 0.0.0.0.** Cada um roda `serve_forever` numa thread
    daemon; o chamador guarda a lista pra dar `shutdown()` no encerramento."""
    porta = ctx.config.agent.http.port
    binds = [("127.0.0.1", porta)]
    if ctx.config.agent.http.bind_tailnet:
        ip = detectar_ip_tailnet(ctx.config)
        if ip:
            binds.append((ip, porta))
        else:
            logger.warning(
                "HTTP local: IP da tailnet não detectado — escutando só no loopback. "
                "Fixe [http].tailnet_ip no agent.toml se `tailscale ip -4` não estiver disponível."
            )

    servidores: list[ServidorHttp] = []
    for host, port in binds:
        srv = criar_servidor(ctx, host, port)
        threading.Thread(target=srv.serve_forever, name=f"http-{host}", daemon=True).start()
        servidores.append(srv)
    return servidores


def encerrar_servidores(servidores: list[ServidorHttp]) -> None:
    """Para o `serve_forever` e fecha o socket de escuta de cada um."""
    for srv in servidores:
        srv.shutdown()
        srv.server_close()
