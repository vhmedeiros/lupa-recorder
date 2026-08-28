"""`lupa-recorder probe` — a primeira ferramenta que quem cadastra uma fonte nova usa.

Responde à pergunta real (plano §8.7): não "essa URL existe", e sim "essa URL funciona
daqui, com o disco livre desta máquina, e o que cadastrar em channels.yaml pra ela".

Este módulo separa lógica pura (parsing de master playlist HLS, escolha de rendition
recomendada, conta de projeção de disco — tudo testável sem rede) da parte que fala de
verdade com a URL e com `ffmpeg`/`ffprobe`/`yt-dlp` (rede, subprocesso — testada manualmente
contra fontes reais, não em teste automatizado, pra não deixar o `pytest` dependente de
internet/serviço de terceiro).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DIAS_RETENCAO_PADRAO = 5
GB = 1024**3

# Nomes de parâmetro de URL que costumam carregar token de curta duração (achado de campo,
# Fase 0 — SurferNetwork usa "zt", mas o padrão de "parece um JWT/hash longo" generaliza).
NOMES_DE_PARAMETRO_TOKEN = {"token", "zt", "auth", "signature", "sig", "key", "jwt", "expires", "exp"}


class ProbeError(Exception):
    """Erro de probe com mensagem já pronta pra mostrar ao operador."""


# ── tipos ─────────────────────────────────────────────────────────────────────


@dataclass
class Rendition:
    """Uma variante dentro de um master playlist HLS."""

    uri: str
    bandwidth_bps: int
    resolution: str | None = None  # "1920x1080" ou None (ex.: variante só-áudio)
    codecs: str | None = None

    @property
    def altura(self) -> int | None:
        if not self.resolution or "x" not in self.resolution:
            return None
        try:
            return int(self.resolution.split("x")[1])
        except ValueError:
            return None

    @property
    def gb_por_dia_nominal(self) -> float:
        return (self.bandwidth_bps / 8) * 86400 / GB


@dataclass
class ResultadoCaptura:
    duracao_s: float
    bytes_escritos: int
    descontinuidades: int = 0

    @property
    def bitrate_bps(self) -> float:
        if self.duracao_s <= 0:
            return 0.0
        return (self.bytes_escritos * 8) / self.duracao_s

    @property
    def gb_por_dia_real(self) -> float:
        return (self.bitrate_bps / 8) * 86400 / GB


@dataclass
class ResultadoProbe:
    url: str
    protocolo_detectado: str
    alcancavel: bool
    latencia_ms: float | None
    tem_token: bool
    renditions: list[Rendition] = field(default_factory=list)
    faixas_audio: int | None = None
    legendas: int | None = None
    rendition_recomendada: Rendition | None = None
    captura: ResultadoCaptura | None = None
    disco_livre_gb: float | None = None
    dias_retencao: int = DIAS_RETENCAO_PADRAO
    erro: str | None = None

    @property
    def gb_por_dia_projetado(self) -> float | None:
        if self.captura:
            return self.captura.gb_por_dia_real
        if self.rendition_recomendada and self.rendition_recomendada.bandwidth_bps > 0:
            return self.rendition_recomendada.gb_por_dia_nominal
        # bandwidth_bps == 0 é "desconhecido" (URL não é master playlist, sem teste de
        # captura rodado) — não é o mesmo que "0 GB/dia", não dá pra fingir que sabemos.
        return None

    @property
    def cabe_no_disco(self) -> bool | None:
        projecao = self.gb_por_dia_projetado
        if projecao is None or self.disco_livre_gb is None:
            return None
        return projecao * self.dias_retencao <= self.disco_livre_gb

    def cadastro_sugerido(self) -> dict:
        protocolo = self.protocolo_detectado
        sugestao = {
            "protocol": protocolo,
            "url_resolver": "yt_dlp" if protocolo == "youtube" else "static",
            "archive_profile": "copy",
            "segment_seconds": 240,
        }
        if self.rendition_recomendada and self.rendition_recomendada.resolution:
            sugestao["quality_profile"] = self.rendition_recomendada.resolution
        return sugestao


# ── lógica pura (testável sem rede) ─────────────────────────────────────────────

_RE_STREAM_INF = re.compile(r"#EXT-X-STREAM-INF:(?P<attrs>.+)")


def detectar_protocolo(url: str) -> str:
    """Heurística só pela URL — o que decide de fato é a resposta real (verificar_alcancavel)."""
    parsed = urlparse(url)
    if parsed.scheme == "rtsp":
        return "rtsp"
    if "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
        return "youtube"
    if parsed.path.endswith(".m3u8"):
        return "hls"
    if parsed.path.endswith(".pls"):
        return "pls"  # precisa desembrulhar antes de virar protocolo de verdade
    return "http"


def tem_parametro_de_token(url: str) -> bool:
    query = parse_qs(urlparse(url).query)
    nomes_em_minusculo = {k.lower() for k in query}
    if nomes_em_minusculo & NOMES_DE_PARAMETRO_TOKEN:
        return True
    # heurística extra: valor de query longo, "tipo hash/JWT" (letras+números+pontos, >20
    # chars) e com pelo menos um dígito — hostname/domínio (ex.: vhost=player.uol.com.br,
    # achado real de falso-positivo) não tem dígito nenhum, token/hash quase sempre tem.
    for valores in query.values():
        for v in valores:
            if len(v) > 20 and re.fullmatch(r"[A-Za-z0-9._-]+", v) and any(c.isdigit() for c in v):
                return True
    return False


def parsear_master_playlist(texto: str, url_base: str) -> list[Rendition]:
    """Extrai as variantes de um master playlist HLS (#EXT-X-STREAM-INF + URI seguinte).

    Mesma abordagem já validada em `comandos.md` (grep -A1 EXT-X-STREAM-INF) — só que
    parseando os atributos de verdade em vez de só mostrar a linha crua.
    """
    linhas = texto.splitlines()
    renditions: list[Rendition] = []
    for i, linha in enumerate(linhas):
        m = _RE_STREAM_INF.match(linha.strip())
        if not m:
            continue
        bandwidth = _extrair_atributo_numerico(m.group("attrs"), "BANDWIDTH")
        resolution = _extrair_atributo_texto(m.group("attrs"), "RESOLUTION")
        codecs = _extrair_atributo_texto(m.group("attrs"), "CODECS")
        uri = None
        for prox in linhas[i + 1 :]:
            prox = prox.strip()
            if prox and not prox.startswith("#"):
                uri = prox
                break
        if uri is None or bandwidth is None:
            continue
        uri_absoluta = uri if uri.startswith("http") else _juntar_url(url_base, uri)
        renditions.append(
            Rendition(uri=uri_absoluta, bandwidth_bps=bandwidth, resolution=resolution, codecs=codecs)
        )
    return renditions


def _extrair_atributo_numerico(attrs: str, nome: str) -> int | None:
    m = re.search(rf"{nome}=(\d+)", attrs)
    return int(m.group(1)) if m else None


def _extrair_atributo_texto(attrs: str, nome: str) -> str | None:
    m = re.search(rf'{nome}="?([^,"]+)"?', attrs)
    return m.group(1) if m else None


def _juntar_url(base: str, relativa: str) -> str:
    partes = urlparse(base)
    if relativa.startswith("/"):
        return f"{partes.scheme}://{partes.netloc}{relativa}"
    caminho_base = partes.path.rsplit("/", 1)[0]
    return f"{partes.scheme}://{partes.netloc}{caminho_base}/{relativa}"


def recomendar_rendition(renditions: list[Rendition]) -> Rendition | None:
    """A menor rendition que ainda é "legível" (altura >= 480) — mesma lógica de §6.3 do
    plano (576p prova a menção com marca d'água legível e custa 4x menos que 1080p).
    Sem nenhuma com altura conhecida >= 480, cai pra menor disponível.
    """
    if not renditions:
        return None
    com_altura = [r for r in renditions if r.altura is not None]
    candidatas = [r for r in com_altura if r.altura >= 480] or com_altura or renditions
    return min(candidatas, key=lambda r: r.bandwidth_bps)


def desembrulhar_pls(texto: str) -> str | None:
    """Extrai a primeira URL de um arquivo .pls (formato File1=..., File2=...)."""
    for linha in texto.splitlines():
        linha = linha.strip()
        if re.match(r"File\d+\s*=", linha, re.IGNORECASE):
            return linha.split("=", 1)[1].strip()
    return None


def calcular_projecao(gb_por_dia: float, dias: int, disco_livre_gb: float) -> tuple[float, bool]:
    projecao = gb_por_dia * dias
    return projecao, projecao <= disco_livre_gb


# ── I/O (rede, subprocesso) — não coberto por teste automatizado ────────────────


def _buscar_texto(url: str, timeout_s: float = 10) -> tuple[str, float]:
    inicio = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "lupa-recorder-probe/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — URL vem do operador, uso deliberado
        corpo = resp.read().decode("utf-8", errors="replace")
    latencia_ms = (time.monotonic() - inicio) * 1000
    return corpo, latencia_ms


def verificar_alcancavel(url: str, timeout_s: float = 10) -> tuple[bool, float | None]:
    inicio = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "lupa-recorder-probe/0.1"})
        with urllib.request.urlopen(req, timeout=timeout_s):  # noqa: S310
            pass
        return True, (time.monotonic() - inicio) * 1000
    except urllib.error.HTTPError:
        # HEAD pode não ser suportado pelo servidor mas a URL existir de verdade — cai pro GET.
        try:
            _, latencia = _buscar_texto(url, timeout_s)
            return True, latencia
        except Exception:
            return False, None
    except Exception:
        return False, None


def _ffprobe_disponivel() -> bool:
    return shutil.which("ffprobe") is not None


def contar_faixas_audio_e_legendas(url: str, timeout_s: float = 15) -> tuple[int | None, int | None]:
    if not _ffprobe_disponivel():
        return None, None
    try:
        saida = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", url],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
        dados = json.loads(saida.stdout)
        streams = dados.get("streams", [])
        audio = sum(1 for s in streams if s.get("codec_type") == "audio")
        legendas = sum(1 for s in streams if s.get("codec_type") == "subtitle")
        return audio, legendas
    except Exception:
        return None, None


def testar_captura(
    url: str,
    protocolo: str,
    destino: Path,
    segundos: int = 20,
    url_audio_youtube: str | None = None,
) -> ResultadoCaptura:
    """Captura curta de verdade, pra medir bitrate real (não só o nominal do master
    playlist). Segue as regras já aprendidas na Fase 0: nunca -reconnect* em HLS/YouTube.
    """
    if not shutil.which("ffmpeg"):
        raise ProbeError("ffmpeg não encontrado no PATH — rode o bootstrap.sh primeiro.")

    destino.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]

    if protocolo == "youtube":
        if not url_audio_youtube:
            raise ProbeError("captura de youtube precisa das duas URLs (vídeo + áudio) resolvidas.")
        cmd += [
            "-thread_queue_size",
            "1024",
            "-i",
            url,
            "-thread_queue_size",
            "1024",
            "-i",
            url_audio_youtube,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
        ]
    else:
        if protocolo == "http":
            cmd += [
                "-reconnect",
                "1",
                "-reconnect_at_eof",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "30",
            ]
        cmd += ["-i", url]

    cmd += ["-t", str(segundos), "-c", "copy", str(destino)]

    inicio = time.monotonic()
    subprocess.run(cmd, capture_output=True, timeout=segundos + 30, check=True)
    duracao_real = time.monotonic() - inicio

    if not destino.exists():
        raise ProbeError("captura de teste não gerou arquivo — ver stderr do ffmpeg.")

    return ResultadoCaptura(duracao_s=min(duracao_real, segundos), bytes_escritos=destino.stat().st_size)


def resolver_youtube(url: str, quality_profile: str = "480p") -> tuple[str, str]:
    """Resolve as duas URLs (vídeo + áudio) de uma live do YouTube via yt-dlp — achado de
    campo da Fase 0: lives normalmente não têm formato combinado.
    """
    if not shutil.which("yt-dlp"):
        raise ProbeError("yt-dlp não encontrado no PATH — rode o bootstrap.sh primeiro.")
    try:
        video = subprocess.run(
            ["yt-dlp", "-g", "-f", f"bestvideo[height<={quality_profile.rstrip('p')}]", url],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
        audio = subprocess.run(
            ["yt-dlp", "-g", "-f", "bestaudio", url],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ProbeError(f"yt-dlp falhou ao resolver {url}: {exc.stderr}") from exc
    if not video or not audio:
        raise ProbeError(f"yt-dlp não devolveu URL de vídeo/áudio pra {url} (live fora do ar?).")
    return video, audio


def probe(
    url: str,
    *,
    disco_livre_gb: float | None = None,
    dias_retencao: int = DIAS_RETENCAO_PADRAO,
    testar_captura_real: bool = True,
    segundos_teste: int = 20,
    diretorio_scratch: Path | None = None,
) -> ResultadoProbe:
    """Orquestra o probe completo. `testar_captura_real=False` pula a parte que precisa de
    `ffmpeg`/rede de verdade — útil em ambiente sem essas ferramentas instaladas.
    """
    protocolo = detectar_protocolo(url)
    resultado = ResultadoProbe(
        url=url,
        protocolo_detectado=protocolo,
        alcancavel=False,
        latencia_ms=None,
        tem_token=tem_parametro_de_token(url),
        disco_livre_gb=disco_livre_gb,
        dias_retencao=dias_retencao,
    )

    if protocolo == "pls":
        try:
            texto, latencia = _buscar_texto(url)
        except Exception as exc:
            resultado.erro = f"não consegui baixar o .pls: {exc}"
            return resultado
        url_real = desembrulhar_pls(texto)
        if not url_real:
            resultado.erro = ".pls não tinha nenhuma linha File1=..."
            return resultado
        # reprocessa como se fosse a URL de verdade, mas guarda a detecção original de token
        sub = probe(
            url_real,
            disco_livre_gb=disco_livre_gb,
            dias_retencao=dias_retencao,
            testar_captura_real=testar_captura_real,
            segundos_teste=segundos_teste,
            diretorio_scratch=diretorio_scratch,
        )
        sub.url = url  # mantém a URL original pro cadastro (é o que o operador colou)
        return sub

    if protocolo == "youtube":
        try:
            url_video, url_audio = resolver_youtube(url)
        except ProbeError as exc:
            resultado.erro = str(exc)
            return resultado
        resultado.alcancavel = True
        if testar_captura_real and diretorio_scratch:
            try:
                resultado.captura = testar_captura(
                    url_video,
                    "youtube",
                    diretorio_scratch / "probe-youtube.ts",
                    segundos=segundos_teste,
                    url_audio_youtube=url_audio,
                )
            except ProbeError as exc:
                resultado.erro = str(exc)
        return resultado

    alcancavel, latencia = verificar_alcancavel(url)
    resultado.alcancavel = alcancavel
    resultado.latencia_ms = latencia
    if not alcancavel:
        resultado.erro = "URL não respondeu (nem HEAD nem GET)."
        return resultado

    if protocolo == "hls":
        try:
            texto, _ = _buscar_texto(url)
        except Exception as exc:
            resultado.erro = f"não consegui baixar o playlist: {exc}"
            return resultado
        renditions = parsear_master_playlist(texto, url)
        if not renditions:
            # não é master playlist (sem #EXT-X-STREAM-INF) — a própria URL já é a única opção.
            # Caso real: TV Cultura só tem essa (bench.md). Bandwidth nominal fica None,
            # o teste de captura abaixo mede o bitrate real.
            renditions = [Rendition(uri=url, bandwidth_bps=0)]
        resultado.renditions = renditions
        resultado.rendition_recomendada = recomendar_rendition(renditions)
        alvo_para_faixas = resultado.rendition_recomendada.uri if resultado.rendition_recomendada else url
        resultado.faixas_audio, resultado.legendas = contar_faixas_audio_e_legendas(alvo_para_faixas)

    if testar_captura_real and diretorio_scratch:
        try:
            resultado.captura = testar_captura(
                url, protocolo, diretorio_scratch / "probe-teste.ts", segundos=segundos_teste
            )
        except ProbeError as exc:
            resultado.erro = str(exc)

    return resultado
