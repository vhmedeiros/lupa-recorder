import asyncio
import signal
import time

from lupa_recorder.capture.policy import BackoffPolicy, FlappingTracker
from lupa_recorder.capture.segments import pasta_do_dia
from lupa_recorder.capture.supervisor import (
    YOUTUBE_RESTART_INTERVAL_S,
    EstadoSupervisor,
    MotivoParada,
    SourceSupervisor,
)
from lupa_recorder.config import SourceConfig
from lupa_recorder.resolve.base import ResolvedInput

FONTE_RADIO = {
    "id": 1,
    "slug": "radio-teste",
    "kind": "radio",
    "protocol": "http",
    "url": "http://exemplo.com/stream",
}

FONTE_YOUTUBE = {
    "id": 7,
    "slug": "tv-youtube-teste",
    "kind": "tv",
    "protocol": "youtube",
    "url": "https://youtube.com/watch?v=abc",
    "url_resolver": "yt_dlp",
}


class RelogioFalso:
    """Substitui `time.time()`/`asyncio.sleep()` no supervisor — o tempo só passa quando
    alguém manda passar, não precisa esperar de verdade nos testes.

    Começa perto de `time.time()` real de propósito: `ultimo_progresso_em` usa `mtime` de
    arquivo de verdade (real, não fake) — se o relógio falso começasse numa época arbitrária
    longe da real, a comparação `agora - ultimo_progresso` ficaria sempre absurdamente
    negativa assim que um arquivo real entrasse na conta, mascarando bug em vez de testar
    a interação de verdade.
    """

    def __init__(self, inicio: float | None = None) -> None:
        self.agora_valor = inicio if inicio is not None else time.time()
        self.chamadas_sleep: list[float] = []

    def agora(self) -> float:
        return self.agora_valor

    async def sleep(self, segundos: float) -> None:
        self.chamadas_sleep.append(segundos)
        self.agora_valor += segundos
        await asyncio.sleep(0)  # cede o event loop de verdade — sem isso o laço do
        # supervisor gira síncrono pra sempre e nunca deixa outra tarefa (ex.: o próprio
        # teste, esperando um `stop.set()`) rodar junto.


class ResolverFalso:
    def __init__(self, urls: list[str] | None = None) -> None:
        self.urls = urls or ["http://exemplo.com/stream"]
        self.chamadas = 0

    async def resolve(self, source: SourceConfig) -> ResolvedInput:
        self.chamadas += 1
        return ResolvedInput(urls=self.urls)


class ProcessoFalso:
    def __init__(self, ignora_sigterm: bool = False, morto_desde_o_inicio: bool = False) -> None:
        self.returncode: int | None = 1 if morto_desde_o_inicio else None
        self.sinais: list[int] = []
        self.ignora_sigterm = ignora_sigterm
        self._morreu = asyncio.Event()
        if morto_desde_o_inicio:
            self._morreu.set()

    def send_signal(self, sig: int) -> None:
        self.sinais.append(sig)
        if sig == signal.SIGTERM and self.ignora_sigterm:
            return
        self.returncode = -sig
        self._morreu.set()

    async def wait(self) -> int:
        await self._morreu.wait()
        return self.returncode  # type: ignore[return-value]


class LauncherFalso:
    def __init__(self, fabrica) -> None:
        self.fabrica = fabrica
        self.comandos: list[list[str]] = []

    async def iniciar(self, comando: list[str]) -> ProcessoFalso:
        self.comandos.append(comando)
        return self.fabrica()


def _supervisor(source_overrides=None, data_root=None, **kwargs):
    source = SourceConfig(**{**FONTE_RADIO, **(source_overrides or {})})
    return SourceSupervisor(
        source,
        data_root,
        resolver=kwargs.pop("resolver", ResolverFalso()),
        watchdog_timeout_s=kwargs.pop("watchdog_timeout_s", 10.0),
        poll_interval_s=kwargs.pop("poll_interval_s", 2.0),
        sigterm_grace_s=kwargs.pop("sigterm_grace_s", 0.05),  # real, mas curto — não fake
        **kwargs,
    )


class TestCicloProcessoMorreSozinho:
    async def test_detecta_processo_morto_e_promove_pasta(self, tmp_path):
        relogio = RelogioFalso()
        launcher = LauncherFalso(lambda: ProcessoFalso(morto_desde_o_inicio=True))
        sup = _supervisor(
            data_root=tmp_path, launcher=launcher, agora=relogio.agora, sleep=relogio.sleep
        )

        resultado = await sup._ciclo(asyncio.Event())

        assert resultado.motivo == MotivoParada.processo_morreu
        assert pasta_do_dia(tmp_path, "radio-teste", None).exists()  # garantir_pastas_do_dia rodou


class TestCicloWatchdog:
    async def test_mata_processo_travado_sem_segmento_novo(self, tmp_path):
        relogio = RelogioFalso()
        launcher = LauncherFalso(lambda: ProcessoFalso())  # nunca morre sozinho
        sup = _supervisor(
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=relogio.sleep,
            watchdog_timeout_s=10.0,
            poll_interval_s=2.0,
        )

        resultado = await sup._ciclo(asyncio.Event())

        assert resultado.motivo == MotivoParada.travado
        assert launcher.comandos  # confirma que chegou a montar/lançar o comando

    async def test_escala_pra_sigkill_quando_sigterm_e_ignorado(self, tmp_path):
        relogio = RelogioFalso()
        processos_criados = []

        def fabrica():
            p = ProcessoFalso(ignora_sigterm=True)
            processos_criados.append(p)
            return p

        launcher = LauncherFalso(fabrica)
        sup = _supervisor(
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=relogio.sleep,
            watchdog_timeout_s=10.0,
            poll_interval_s=2.0,
            sigterm_grace_s=0.02,
        )

        resultado = await sup._ciclo(asyncio.Event())

        assert resultado.motivo == MotivoParada.travado
        assert processos_criados[0].sinais == [signal.SIGTERM, signal.SIGKILL]

    async def test_segmento_novo_reseta_o_relogio_do_watchdog(self, tmp_path, monkeypatch):
        # progresso contínuo (um segmento novo a cada poll, acompanhando o relógio) não
        # pode disparar o watchdog, mesmo bem depois do tempo total decorrido já ter
        # passado watchdog_timeout_s muitas vezes. `ultimo_progresso_em` (mtime de
        # arquivo real) é isolado aqui de propósito — um relógio falso acelerado sempre
        # dispara mais rápido que o clock real do filesystem consegue acompanhar, então
        # testar a interação real exigiria tempo de parede de verdade. O que importa
        # (a lógica de comparação `agora - ultimo_progresso`) é testado isolado.
        relogio = RelogioFalso()
        chamadas = {"n": 0}
        stop = asyncio.Event()

        import lupa_recorder.capture.supervisor as mod

        monkeypatch.setattr(mod, "ultimo_progresso_em", lambda *a, **k: relogio.agora_valor)

        async def sleep_com_progresso_sincronizado(segundos):
            chamadas["n"] += 1
            relogio.agora_valor += segundos
            if chamadas["n"] == 10:  # 10 * poll_interval_s(2) = 20s, bem > watchdog_timeout_s(6)
                stop.set()
            await asyncio.sleep(0)

        launcher = LauncherFalso(lambda: ProcessoFalso())
        sup = _supervisor(
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=sleep_com_progresso_sincronizado,
            watchdog_timeout_s=6.0,
            poll_interval_s=2.0,
        )

        resultado = await sup._ciclo(stop)

        assert resultado.motivo == MotivoParada.parado_externamente
        assert chamadas["n"] == 10


class TestCicloParadaExterna:
    async def test_stop_event_ja_setado_mata_e_para(self, tmp_path):
        relogio = RelogioFalso()
        processo = ProcessoFalso()
        launcher = LauncherFalso(lambda: processo)
        sup = _supervisor(data_root=tmp_path, launcher=launcher, agora=relogio.agora, sleep=relogio.sleep)

        stop = asyncio.Event()
        stop.set()
        resultado = await sup._ciclo(stop)

        assert resultado.motivo == MotivoParada.parado_externamente
        assert signal.SIGTERM in processo.sinais


class TestReinicioPlanejadoYoutube:
    async def test_reinicia_apos_3h_pra_fonte_youtube(self, tmp_path):
        relogio = RelogioFalso()
        launcher = LauncherFalso(lambda: ProcessoFalso())
        sup = _supervisor(
            source_overrides=FONTE_YOUTUBE,
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=relogio.sleep,
            resolver=ResolverFalso(urls=["http://video", "http://audio"]),
            watchdog_timeout_s=999999,  # não deixa o watchdog disparar primeiro neste teste
            poll_interval_s=60.0,
        )

        resultado = await sup._ciclo(asyncio.Event())

        assert resultado.motivo == MotivoParada.reinicio_planejado

    async def test_fonte_nao_youtube_nunca_tem_reinicio_planejado(self, tmp_path):
        # sem url_resolver=yt_dlp, `restart_planejado_em` nem é calculado (fica None) —
        # não importa quanto tempo (fake) passe, nunca pode virar reinicio_planejado.
        # Determinístico via contagem de iteração, watchdog desligado de propósito
        # (timeout gigante) pra isolar só o comportamento sob teste.
        relogio = RelogioFalso()
        stop = asyncio.Event()
        chamadas = {"n": 0}

        async def sleep_que_para_depois_de_n(segundos):
            chamadas["n"] += 1
            relogio.agora_valor += segundos
            if chamadas["n"] == 5:
                stop.set()
            await asyncio.sleep(0)

        launcher = LauncherFalso(lambda: ProcessoFalso())
        sup = _supervisor(
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=sleep_que_para_depois_de_n,
            watchdog_timeout_s=999999,  # isolado — não é o watchdog sob teste aqui
            poll_interval_s=YOUTUBE_RESTART_INTERVAL_S,  # se fosse youtube, dispararia já na 1ª iteração
        )

        resultado = await sup._ciclo(stop)

        assert resultado.motivo == MotivoParada.parado_externamente
        assert chamadas["n"] == 5


class TestRunForever:
    async def test_backoff_e_flapping_apos_varios_processos_morrendo(self, tmp_path):
        relogio = RelogioFalso()
        contador = {"n": 0}
        stop = asyncio.Event()

        def fabrica():
            contador["n"] += 1
            if contador["n"] > 7:
                stop.set()
            return ProcessoFalso(morto_desde_o_inicio=True)

        launcher = LauncherFalso(fabrica)
        sup = _supervisor(
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=relogio.sleep,
            backoff=BackoffPolicy(base_s=1.0, max_s=60.0),
            flapping=FlappingTracker(janela_s=3600, limite=6),
        )

        await sup.run_forever(stop)

        assert sup.tentativas == 7
        assert sup.estado == EstadoSupervisor.parado  # parou limpo, não travou em flapping
        # mas os restarts 1..7 tiveram backoff crescente registrado
        assert relogio.chamadas_sleep[:4] == [1.0, 2.0, 4.0, 8.0]

    async def test_reinicio_planejado_nao_conta_pra_tentativas(self, tmp_path):
        relogio = RelogioFalso()
        stop = asyncio.Event()
        contador = {"n": 0}

        def fabrica():
            contador["n"] += 1
            if contador["n"] >= 2:
                stop.set()
            return ProcessoFalso()  # nunca morre sozinho — só via reinício planejado

        launcher = LauncherFalso(fabrica)
        sup = _supervisor(
            source_overrides=FONTE_YOUTUBE,
            data_root=tmp_path,
            launcher=launcher,
            agora=relogio.agora,
            sleep=relogio.sleep,
            resolver=ResolverFalso(urls=["http://video", "http://audio"]),
            watchdog_timeout_s=999999,
            poll_interval_s=60.0,
        )

        await sup.run_forever(stop)

        assert sup.tentativas == 0  # nenhum restart "de falha" — só planejado

    async def test_erro_inesperado_no_ciclo_nao_derruba_run_forever(self, tmp_path):
        # achado de campo real (2026-08-28): ffmpeg ausente no PATH subia como
        # FileNotFoundError direto do launcher e matava o asyncio.gather() de TODAS as
        # fontes, não só a com problema. Qualquer erro não previsto tem que virar mais
        # uma tentativa com backoff, nunca propagar.
        relogio = RelogioFalso()
        stop = asyncio.Event()
        contador = {"n": 0}

        class LauncherQuebrado:
            async def iniciar(self, comando):
                contador["n"] += 1
                if contador["n"] >= 3:
                    stop.set()
                raise FileNotFoundError("ffmpeg não encontrado")

        sup = _supervisor(
            data_root=tmp_path,
            launcher=LauncherQuebrado(),
            agora=relogio.agora,
            sleep=relogio.sleep,
        )

        await sup.run_forever(stop)  # não pode levantar — essa é a asserção principal

        # a 3ª chamada já seta `stop` antes de levantar — o laço vê stop=set logo em
        # seguida e sai sem contar mais uma tentativa (mesma regra de qualquer outra
        # parada externa), então só as duas primeiras contam.
        assert sup.tentativas == 2
        assert sup.estado == EstadoSupervisor.parado
