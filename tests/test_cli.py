"""CLI — códigos de saída dos comandos da 1.8 (`doctor`, gate de relógio do `run`)."""

from __future__ import annotations

import pytest

from lupa_recorder import cli
from lupa_recorder.health.checks import Checagem, Status


@pytest.fixture
def paths(ambiente, tmp_path):
    _cfg, _dr, _sr = ambiente
    return str(tmp_path / "agent.toml"), str(tmp_path / "channels.yaml")


def test_doctor_verde_sai_0(monkeypatch, paths, capsys):
    agent, channels = paths
    monkeypatch.setattr(cli, "rodar_todas", lambda *a, **k: [Checagem("x", Status.ok, "ok")])

    assert cli.main(["doctor", "--config", agent, "--channels", channels, "--sem-rede"]) == 0
    assert "✅ x" in capsys.readouterr().out


def test_doctor_com_falha_sai_1(monkeypatch, paths):
    agent, channels = paths
    monkeypatch.setattr(
        cli, "rodar_todas", lambda *a, **k: [Checagem("relógio", Status.falha, "offset 9s")]
    )
    assert cli.main(["doctor", "--config", agent, "--channels", channels]) == 1


def test_doctor_grava_evento_no_catalogo(monkeypatch, ambiente, paths):
    cfg, _dr, system_root = ambiente
    agent, channels = paths
    monkeypatch.setattr(cli, "rodar_todas", lambda *a, **k: [Checagem("x", Status.ok, "ok")])

    cli.main(["doctor", "--config", agent, "--channels", channels, "--sem-rede"])

    from lupa_recorder.catalog.db import conectar
    from lupa_recorder.catalog.models import listar_eventos

    conn = conectar(system_root / "catalog.sqlite3")
    try:
        assert any(e.kind == "doctor" for e in listar_eventos(conn, limite=10))
    finally:
        conn.close()


def test_run_recusa_com_relogio_fora(monkeypatch, paths):
    agent, channels = paths
    monkeypatch.setattr(cli, "checar_relogio", lambda: Checagem("relógio", Status.falha, "offset 9s"))
    monkeypatch.setattr(
        cli, "asyncio", type("A", (), {"run": staticmethod(lambda c: pytest.fail("não devia rodar"))})
    )

    assert cli.main(["run", "--config", agent, "--channels", channels]) == 1


def test_run_ignorar_relogio_passa_do_gate(monkeypatch, paths):
    agent, channels = paths
    chegou = []
    monkeypatch.setattr(cli, "checar_relogio", lambda: Checagem("relógio", Status.falha, "offset 9s"))
    monkeypatch.setattr(cli, "_supervisionar_todas_as_fontes", lambda cfg: chegou.append(1) or 0)
    monkeypatch.setattr(cli, "asyncio", type("A", (), {"run": staticmethod(lambda c: c)}))

    assert cli.main(["run", "--config", agent, "--channels", channels, "--ignorar-relogio"]) == 0
    assert chegou == [1]
