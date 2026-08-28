from datetime import datetime, timedelta
from pathlib import Path

from lupa_recorder.catalog.db import conectar
from lupa_recorder.catalog.models import (
    Segment,
    SegmentState,
    definir_hold_until,
    inserir_segmento,
    listar_eventos,
    listar_segmentos,
)
from lupa_recorder.config import Tier
from lupa_recorder.retention.gc import (
    executar_ciclo,
    purgar_expirados_por_idade,
    purgar_por_pressao,
)

AGORA = datetime(2026, 8, 28, 12, 0, 0)


def _seg(base, slug, dias_atras, tamanho=1000, hold_until=None):
    return Segment(
        source_slug=slug,
        path=str(base / slug / f"{dias_atras}.ts"),
        started_at=(AGORA - timedelta(days=dias_atras)).isoformat(),
        bytes=tamanho,
        hold_until=hold_until,
    )


def _criar_arquivo_do_segmento(seg: Segment) -> None:
    caminho = Path(seg.path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"x" * seg.bytes)


def _conn(tmp_path):
    return conectar(tmp_path / "catalogo.sqlite3")


class TestPurgarExpiradosPorIdade:
    def test_apaga_segmento_mais_velho_que_o_tier(self, tmp_path):
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=6)  # standard = 5 dias
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)

        apagados = purgar_expirados_por_idade(conn, {"radio-x": Tier.standard}, AGORA)

        assert len(apagados) == 1
        assert not Path(seg.path).exists()
        assert listar_segmentos(conn, "radio-x")[0].state == SegmentState.purged

    def test_nao_apaga_dentro_do_prazo(self, tmp_path):
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=3)  # standard = 5 dias, ainda dentro
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)

        apagados = purgar_expirados_por_idade(conn, {"radio-x": Tier.standard}, AGORA)

        assert apagados == []
        assert Path(seg.path).exists()

    def test_respeita_tier_diferente_por_fonte(self, tmp_path):
        conn = _conn(tmp_path)
        critico = _seg(tmp_path, "tv-critica", dias_atras=6)  # critical = 7 dias, ainda dentro
        padrao = _seg(tmp_path, "radio-padrao", dias_atras=6)  # standard = 5 dias, expirado
        _criar_arquivo_do_segmento(critico)
        _criar_arquivo_do_segmento(padrao)
        inserir_segmento(conn, critico)
        inserir_segmento(conn, padrao)

        apagados = purgar_expirados_por_idade(
            conn, {"tv-critica": Tier.critical, "radio-padrao": Tier.standard}, AGORA
        )

        assert [s.source_slug for s in apagados] == ["radio-padrao"]
        assert Path(critico.path).exists()

    def test_hold_until_no_futuro_protege(self, tmp_path):
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=10, hold_until=(AGORA + timedelta(days=1)).isoformat())
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)

        apagados = purgar_expirados_por_idade(conn, {"radio-x": Tier.standard}, AGORA)

        assert apagados == []
        assert Path(seg.path).exists()

    def test_hold_until_no_passado_nao_protege_mais(self, tmp_path):
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=10, hold_until=(AGORA - timedelta(days=1)).isoformat())
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)

        apagados = purgar_expirados_por_idade(conn, {"radio-x": Tier.standard}, AGORA)

        assert len(apagados) == 1

    def test_gera_evento_gc_purge(self, tmp_path):
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=6)
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)

        purgar_expirados_por_idade(conn, {"radio-x": Tier.standard}, AGORA)

        eventos = listar_eventos(conn, source_slug="radio-x")
        assert any(e.kind == "gc_purge" for e in eventos)

    def test_arquivo_ja_ausente_ainda_marca_purged(self, tmp_path):
        # o registro no catálogo não pode depender do arquivo existir — já pode ter sumido
        # por outro motivo (disco limpo à mão, por exemplo).
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=6)
        inserir_segmento(conn, seg)  # nunca criou o arquivo de verdade

        apagados = purgar_expirados_por_idade(conn, {"radio-x": Tier.standard}, AGORA)

        assert len(apagados) == 1


class TestPurgarPorPressao:
    def test_sacrifica_background_antes_de_standard(self, tmp_path):
        conn = _conn(tmp_path)
        bg = _seg(tmp_path, "radio-bg", dias_atras=1, tamanho=500)
        std = _seg(tmp_path, "radio-std", dias_atras=1, tamanho=500)
        _criar_arquivo_do_segmento(bg)
        _criar_arquivo_do_segmento(std)
        inserir_segmento(conn, bg)
        inserir_segmento(conn, std)

        apagados = purgar_por_pressao(
            conn, {"radio-bg": Tier.background, "radio-std": Tier.standard}, bytes_a_liberar=500, agora=AGORA
        )

        assert [s.source_slug for s in apagados] == ["radio-bg"]
        assert Path(std.path).exists()  # standard não foi tocado, background já bastou

    def test_mais_antigo_primeiro_dentro_do_tier(self, tmp_path):
        conn = _conn(tmp_path)
        antigo = _seg(tmp_path, "radio-x", dias_atras=3, tamanho=500)
        recente = _seg(tmp_path, "radio-x", dias_atras=1, tamanho=500)
        _criar_arquivo_do_segmento(antigo)
        _criar_arquivo_do_segmento(recente)
        inserir_segmento(conn, antigo)
        inserir_segmento(conn, recente)

        apagados = purgar_por_pressao(conn, {"radio-x": Tier.standard}, bytes_a_liberar=500, agora=AGORA)

        assert len(apagados) == 1
        assert apagados[0].started_at == antigo.started_at
        assert Path(recente.path).exists()

    def test_para_assim_que_libera_o_suficiente(self, tmp_path):
        conn = _conn(tmp_path)
        for i in range(5):
            seg = _seg(tmp_path, "radio-x", dias_atras=i + 1, tamanho=1000)
            _criar_arquivo_do_segmento(seg)
            inserir_segmento(conn, seg)

        apagados = purgar_por_pressao(conn, {"radio-x": Tier.standard}, bytes_a_liberar=2500, agora=AGORA)

        assert len(apagados) == 3  # 3 × 1000 >= 2500, não precisa dos outros 2

    def test_hold_until_protege_mesmo_sob_pressao(self, tmp_path):
        conn = _conn(tmp_path)
        protegido = _seg(tmp_path, "radio-x", dias_atras=5, tamanho=1000, hold_until=(AGORA + timedelta(days=1)).isoformat())
        livre = _seg(tmp_path, "radio-x", dias_atras=1, tamanho=1000)
        _criar_arquivo_do_segmento(protegido)
        _criar_arquivo_do_segmento(livre)
        inserir_segmento(conn, protegido)
        inserir_segmento(conn, livre)

        apagados = purgar_por_pressao(conn, {"radio-x": Tier.standard}, bytes_a_liberar=1000, agora=AGORA)

        assert [s.started_at for s in apagados] == [livre.started_at]
        assert Path(protegido.path).exists()

    def test_nunca_toca_em_segmento_marcado_via_definir_hold_until(self, tmp_path):
        conn = _conn(tmp_path)
        seg = _seg(tmp_path, "radio-x", dias_atras=5, tamanho=1000)
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)
        definir_hold_until(conn, "radio-x", seg.started_at, (AGORA + timedelta(hours=72)).isoformat())

        apagados = purgar_por_pressao(conn, {"radio-x": Tier.standard}, bytes_a_liberar=1000, agora=AGORA)

        assert apagados == []


class TestExecutarCiclo:
    def test_sem_pressao_so_roda_idade(self, tmp_path):
        conn = _conn(tmp_path)
        data_root = tmp_path / "data"
        expirado = _seg(tmp_path, "radio-x", dias_atras=6)
        expirado = Segment(**{**expirado.__dict__, "path": str(data_root / "radio-x" / "velho.ts")})
        _criar_arquivo_do_segmento(expirado)
        inserir_segmento(conn, expirado)

        resultado = executar_ciclo(
            conn, data_root, {"radio-x": Tier.standard}, agora=AGORA, medir_uso_pct=lambda p: 0.10
        )

        assert len(resultado.apagados_por_idade) == 1
        assert resultado.apagados_por_pressao == []
        assert resultado.bytes_liberados == expirado.bytes

    def test_pressao_alta_dispara_sacrificio_extra(self, tmp_path):
        conn = _conn(tmp_path)
        data_root = tmp_path / "data"
        seg = _seg(tmp_path, "radio-x", dias_atras=1, tamanho=1000)  # dentro do prazo, só pressão pega
        seg = Segment(**{**seg.__dict__, "path": str(data_root / "radio-x" / "recente.ts")})
        _criar_arquivo_do_segmento(seg)
        inserir_segmento(conn, seg)

        resultado = executar_ciclo(
            conn,
            data_root,
            {"radio-x": Tier.standard},
            agora=AGORA,
            watermark_high=0.85,
            watermark_low=0.70,
            medir_uso_pct=lambda p: 0.90,  # acima do watermark_high
            medir_total_bytes=lambda p: 10_000,
        )

        assert resultado.apagados_por_idade == []
        assert len(resultado.apagados_por_pressao) == 1

    def test_pressao_baixa_nao_sacrifica_nada_alem_da_idade(self, tmp_path):
        conn = _conn(tmp_path)
        data_root = tmp_path / "data"

        resultado = executar_ciclo(
            conn, data_root, {"radio-x": Tier.standard}, agora=AGORA, medir_uso_pct=lambda p: 0.50
        )

        assert resultado.apagados_por_pressao == []


class TestExecutarLoop:
    async def test_roda_periodicamente_ate_parar(self, tmp_path):
        import asyncio

        from lupa_recorder.retention.gc import executar_loop

        conn = _conn(tmp_path)
        data_root = tmp_path / "data"
        stop = asyncio.Event()
        chamadas = {"n": 0}

        async def sleep_que_para_apos_3(segundos):
            chamadas["n"] += 1
            if chamadas["n"] >= 3:
                stop.set()
            await asyncio.sleep(0)

        await executar_loop(
            conn,
            data_root,
            {"radio-x": Tier.standard},
            stop,
            poll_interval_s=1.0,
            sleep=sleep_que_para_apos_3,
            medir_uso_pct=lambda p: 0.10,
        )

        assert chamadas["n"] == 3

    async def test_erro_no_ciclo_nao_derruba_o_loop(self, tmp_path):
        import asyncio

        from lupa_recorder.retention.gc import executar_loop

        conn = _conn(tmp_path)
        stop = asyncio.Event()
        chamadas = {"n": 0}

        async def sleep_que_para_apos_2(segundos):
            chamadas["n"] += 1
            if chamadas["n"] >= 2:
                stop.set()
            await asyncio.sleep(0)

        def uso_que_explode(p):
            raise RuntimeError("disco inacessível, simulado")

        # não pode levantar — essa é a asserção principal
        await executar_loop(
            conn,
            tmp_path / "data",
            {"radio-x": Tier.standard},
            stop,
            poll_interval_s=1.0,
            sleep=sleep_que_para_apos_2,
            medir_uso_pct=uso_que_explode,
        )

        assert chamadas["n"] == 2
