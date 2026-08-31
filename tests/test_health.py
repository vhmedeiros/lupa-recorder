"""Checagens de pré-condição (`doctor` / gate de relógio do `run`) — sub-etapa 1.8."""

from __future__ import annotations

import socket
import subprocess

import pytest

from lupa_recorder.health import checks
from lupa_recorder.health.checks import (
    Status,
    _health_responde,
    _parsear_offset_chrony,
    checar_config,
    checar_ferramentas,
    checar_porta_http,
    checar_relogio,
    linha_de_evento,
    resumir,
    rodar_todas,
)


def _porta_livre() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


def _cfg_minima(tmp_path, *, porta: int | None = None):
    from lupa_recorder.config import Config

    agent = tmp_path / "agent.toml"
    agent.write_text(f"""
[agent]
name = "x"
[paths]
data_root = "{tmp_path}"
system_root = "{tmp_path}"
[security]
hmac_secret = "segredo-de-teste-com-mais-de-16"
[http]
port = {porta or _porta_livre()}
""")
    (tmp_path / "channels.yaml").write_text("sources: []")
    return Config.load(agent, tmp_path / "channels.yaml")


class _FakeResp:
    def __init__(self, corpo: bytes):
        self._corpo = corpo

    def read(self) -> bytes:
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TestPortaHttp:
    def test_porta_livre_e_ok(self, tmp_path):
        c = checar_porta_http(_cfg_minima(tmp_path))
        assert c.status == Status.ok
        assert "livre" in c.detalhe

    def test_porta_ocupada_pelo_servico_e_ok(self, tmp_path, monkeypatch):
        ocupa = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ocupa.bind(("127.0.0.1", 0))
        ocupa.listen(1)
        cfg = _cfg_minima(tmp_path, porta=ocupa.getsockname()[1])
        try:
            monkeypatch.setattr(checks, "_health_responde", lambda _p: True)
            assert checar_porta_http(cfg).status == Status.ok

            monkeypatch.setattr(checks, "_health_responde", lambda _p: False)
            c = checar_porta_http(cfg)
            assert c.status == Status.aviso
            assert "outro processo" in c.detalhe
        finally:
            ocupa.close()

    def test_health_responde_reconhece_o_agente(self):
        assert _health_responde(
            9999, abrir=lambda *a, **k: _FakeResp(b'{"uptime_s": 3, "status": "ok"}')
        )
        assert not _health_responde(9999, abrir=lambda *a, **k: _FakeResp(b"<html>nginx</html>"))

TRACKING_OK = """Reference ID    : 0A0A0A0A (a.b.c)
Stratum         : 3
System time     : 0.000123456 seconds slow of NTP time
Last offset     : -0.000001 seconds
"""
TRACKING_RUIM = "System time     : 5.400000000 seconds fast of NTP time\n"


def _cp(returncode=0, stdout="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["chronyc"], returncode=returncode, stdout=stdout, stderr="")


class TestFerramentas:
    def test_ffmpeg_ausente_e_falha(self):
        cs = checar_ferramentas(which=lambda _: None)
        assert {c.nome: c.status for c in cs}["ffmpeg"] == Status.falha
        assert {c.nome: c.status for c in cs}["yt-dlp"] == Status.aviso  # yt-dlp é só aviso

    def test_tudo_presente(self):
        cs = checar_ferramentas(which=lambda nome: f"/usr/bin/{nome}")
        assert all(c.status == Status.ok for c in cs)

    def test_yt_dlp_ausente_com_fonte_youtube_e_falha(self):
        from lupa_recorder.config import ChannelsConfig, Config, SourceConfig

        fonte = SourceConfig(
            id=1,
            slug="tv-x",
            kind="tv",
            protocol="youtube",
            url="https://www.youtube.com/watch?v=abc",
            url_resolver="yt_dlp",
        )
        cfg = Config.model_construct(agent=None, channels=ChannelsConfig(sources=[fonte]))

        cs = checar_ferramentas(cfg, which=lambda _: None)
        assert {c.nome: c.status for c in cs}["yt-dlp"] == Status.falha


class TestParsearOffsetChrony:
    def test_slow(self):
        assert _parsear_offset_chrony(TRACKING_OK) == pytest.approx(0.000123456)

    def test_fast(self):
        assert _parsear_offset_chrony(TRACKING_RUIM) == pytest.approx(5.4)

    def test_sem_a_linha(self):
        assert _parsear_offset_chrony("Stratum : 3\n") is None


class TestChecarRelogio:
    def test_chrony_ausente_e_aviso(self):
        c = checar_relogio(which=lambda _: None)
        assert c.status == Status.aviso

    def test_sincronizado_e_ok(self):
        c = checar_relogio(which=lambda _: "/usr/bin/chronyc", rodar=lambda _: _cp(0, TRACKING_OK))
        assert c.status == Status.ok

    def test_offset_grande_e_falha(self):
        c = checar_relogio(which=lambda _: "/usr/bin/chronyc", rodar=lambda _: _cp(0, TRACKING_RUIM))
        assert c.status == Status.falha
        assert "5.4" in c.detalhe

    def test_chronyc_com_erro_e_aviso(self):
        c = checar_relogio(which=lambda _: "/usr/bin/chronyc", rodar=lambda _: _cp(1, ""))
        assert c.status == Status.aviso


class TestChecarConfig:
    def test_config_none_e_falha(self):
        cs = checar_config(None, "agent.toml inválido: ...")
        assert cs[0].status == Status.falha

    def test_config_ok_mas_volume_faltando(self, tmp_path):
        from lupa_recorder.config import Config

        agent = tmp_path / "agent.toml"
        agent.write_text(f"""
[agent]
name = "x"
[paths]
data_root = "{tmp_path / 'nao-existe'}"
system_root = "{tmp_path}"
[security]
hmac_secret = "segredo-de-teste-com-mais-de-16"
""")
        (tmp_path / "channels.yaml").write_text("sources: []")
        cfg = Config.load(agent, tmp_path / "channels.yaml")

        cs = checar_config(cfg, None)
        assert cs[0].status == Status.ok  # config em si válida
        assert any(c.status == Status.falha and c.nome == "volumes" for c in cs)


def test_rodar_todas_e_resumo(monkeypatch):
    monkeypatch.setattr(checks, "checar_relogio", lambda: checks.Checagem("relógio", Status.ok, "ok"))
    cs = rodar_todas(None, erro_carga_config="boom", incluir_rede=False)

    ok, avisos, falhas = resumir(cs)
    assert falhas >= 1  # config None
    assert ok + avisos + falhas == len(cs)
    assert "falha" in linha_de_evento(cs)


def test_linha_de_evento_verde():
    cs = [checks.Checagem("a", Status.ok, ""), checks.Checagem("b", Status.aviso, "")]
    assert linha_de_evento(cs).startswith("doctor: verde")
