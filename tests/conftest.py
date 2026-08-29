"""Fixtures compartilhadas pelos testes da HTTP local (sub-etapa 1.7)."""

from __future__ import annotations

import threading
from datetime import date

import pytest

from lupa_recorder.catalog.db import conectar
from lupa_recorder.catalog.models import Segment, SegmentState, inserir_segmento
from lupa_recorder.config import Config
from lupa_recorder.http.app import ContextoServidor, criar_servidor
from lupa_recorder.probe import ResultadoProbe

_AGENT_TOML = """
[agent]
name = "recorder-teste"
[paths]
data_root = "{data_root}"
system_root = "{system_root}"
[security]
hmac_secret = "segredo-de-teste-com-mais-de-16-chars"
[http]
bind_tailnet = false
"""

_CHANNELS_YAML = """
sources:
  - id: 1
    slug: radio-x
    kind: radio
    protocol: http
    url: "http://exemplo/stream"
  - id: 2
    slug: tv-y
    kind: tv
    protocol: hls
    url: "http://exemplo/master.m3u8"
    thumbnails: true
"""


def criar_segmento(
    conn,
    data_root,
    slug: str,
    started_at: str,
    *,
    conteudo: bytes = b"MPEGTS-FAKE" * 100,
    state: SegmentState = SegmentState.ready,
    duration_ms: int | None = None,
):
    """Escreve o `.ts` no layout de disco real (`{slug}/{AAAA-MM-DD}/HHMMSS.ts`) e insere
    a linha no catálogo. `started_at` = `AAAA-MM-DDTHH:MM:SS`."""
    data_iso, hms = started_at.split("T")
    pasta = data_root / slug / data_iso
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"{hms.replace(':', '')}.ts"
    arquivo.write_bytes(conteudo)
    inserir_segmento(
        conn,
        Segment(
            source_slug=slug,
            path=str(arquivo),
            started_at=started_at,
            bytes=len(conteudo),
            state=state,
            duration_ms=duration_ms,
        ),
    )
    return arquivo


@pytest.fixture
def ambiente(tmp_path):
    data_root = tmp_path / "acervo"
    system_root = tmp_path / "system"
    data_root.mkdir()
    system_root.mkdir()
    (tmp_path / "agent.toml").write_text(
        _AGENT_TOML.format(data_root=data_root, system_root=system_root)
    )
    (tmp_path / "channels.yaml").write_text(_CHANNELS_YAML)
    cfg = Config.load(tmp_path / "agent.toml", tmp_path / "channels.yaml")
    return cfg, data_root, system_root


def _fake_probe(url, **_kwargs) -> ResultadoProbe:
    return ResultadoProbe(
        url=url,
        protocolo_detectado="hls",
        alcancavel=True,
        latencia_ms=1.0,
        tem_token=False,
    )


@pytest.fixture
def contexto(ambiente):
    cfg, data_root, system_root = ambiente
    caminho_catalogo = system_root / "catalog.sqlite3"
    conn = conectar(caminho_catalogo)

    hoje = date.today().isoformat()
    for minuto in (0, 4, 8):
        criar_segmento(conn, data_root, "radio-x", f"{hoje}T00:{minuto:02d}:00")

    thumbs_dir = system_root / "thumbs" / "tv-y"
    thumbs_dir.mkdir(parents=True)
    (thumbs_dir / f"{hoje}.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:01:00.000\nx.jpg\n")

    ctx = ContextoServidor(config=cfg, caminho_catalogo=caminho_catalogo, probe_fn=_fake_probe)
    yield ctx
    conn.close()


@pytest.fixture
def servidor(contexto):
    srv = criar_servidor(contexto, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)
