"""`lupa-recorder bench` — sub-etapa 1.8. Lógica pura com captura injetada."""

from __future__ import annotations

import json

from lupa_recorder.bench import (
    MedidaDeFonte,
    _estimar_capture_budget,
    _medir_uma_fonte,
    caminho_do_arquivo,
    escrever,
    rodar_bench,
)
from lupa_recorder.probe import ResultadoCaptura


class TestEstimarCaptureBudget:
    def test_sem_fonte_medida_e_none(self):
        assert _estimar_capture_budget(1.0, 0.5, 0, 8) is None

    def test_captura_de_graca_bate_no_teto(self):
        # carga quase não mexeu com 2 fontes → o gargalo é disco/rede, não CPU
        assert _estimar_capture_budget(0.5, 0.48, 2, 8) == 99

    def test_carga_sensivel_gera_estimativa_finita(self):
        # +2.0 de carga com 2 fontes = 1.0/fonte; 8 núcleos × 0.75 / 1.0 = 6
        assert _estimar_capture_budget(2.5, 0.5, 2, 8) == 6


def test_rodar_bench_soma_bitrate_e_marca_o_que_nao_mediu(ambiente, tmp_path):
    cfg, _data_root, _system_root = ambiente  # fontes: radio-x, tv-y

    def medir(source, _dir, _seg):
        if source.slug == "tv-y":
            return MedidaDeFonte("tv-y", "tv", erro="timeout")
        return MedidaDeFonte(source.slug, "radio", bitrate_bps=128_000, gb_por_dia=1.35)

    r = rodar_bench(cfg, tmp_path, segundos=10, medir_fonte=medir, carga=lambda: 0.5)

    assert r.maquina == "recorder-teste"
    assert r.archive_bytes_per_day == round(128_000 / 8 * 86400)  # só radio-x mediu
    assert r.transcode_budget is None and r.dvb_adapters == 0 and r.vad_hours_per_day is None
    assert any("tv-y (timeout)" in n for n in r.notas)
    assert r.capture_budget == 99  # carga não mexeu


def test_bench_filtra_por_slug(ambiente, tmp_path):
    cfg, _, _ = ambiente
    chamadas = []

    def medir(source, _dir, _seg):
        chamadas.append(source.slug)
        return MedidaDeFonte(source.slug, str(source.kind), bitrate_bps=100_000, gb_por_dia=1.0)

    rodar_bench(cfg, tmp_path, segundos=5, slugs=["radio-x"], medir_fonte=medir, carga=lambda: 0.4)
    assert chamadas == ["radio-x"]


def test_escrever_e_atomico_e_json_valido(ambiente, tmp_path):
    cfg, _, _ = ambiente
    r = rodar_bench(
        cfg,
        tmp_path,
        segundos=5,
        medir_fonte=lambda s, d, seg: MedidaDeFonte(s.slug, "radio", bitrate_bps=96_000, gb_por_dia=1.0),
        carga=lambda: 0.4,
    )

    alvo = escrever(r, tmp_path)

    assert alvo == caminho_do_arquivo(tmp_path)
    assert not (tmp_path / "bench.json.tmp").exists()  # tmp removido
    dados = json.loads(alvo.read_text())
    assert dados["capture_budget"] == 99
    assert {f["slug"] for f in dados["fontes"]} == {"radio-x", "tv-y"}
    assert "notas" in dados


def test_medir_uma_fonte_captura_de_verdade_e_erro(ambiente, tmp_path):
    cfg, _, _ = ambiente
    radio = next(f for f in cfg.channels.sources if f.slug == "radio-x")

    ok = _medir_uma_fonte(
        radio,
        tmp_path,
        10,
        capturar=lambda *a, **k: ResultadoCaptura(duracao_s=10.0, bytes_escritos=160_000),
    )
    assert ok.bitrate_bps == 128_000 and ok.erro is None

    def _explode(*a, **k):
        raise RuntimeError("sem rede")

    ruim = _medir_uma_fonte(radio, tmp_path, 10, capturar=_explode)
    assert ruim.erro == "sem rede" and ruim.bitrate_bps is None
