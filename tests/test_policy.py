from lupa_recorder.capture.policy import BackoffPolicy, FlappingTracker


class TestBackoffPolicy:
    def test_primeira_tentativa_sem_atraso(self):
        assert BackoffPolicy().atraso_para_tentativa(0) == 0.0

    def test_cresce_exponencial(self):
        p = BackoffPolicy(base_s=1.0, max_s=60.0)
        assert p.atraso_para_tentativa(1) == 1.0
        assert p.atraso_para_tentativa(2) == 2.0
        assert p.atraso_para_tentativa(3) == 4.0
        assert p.atraso_para_tentativa(4) == 8.0

    def test_respeita_teto(self):
        p = BackoffPolicy(base_s=1.0, max_s=60.0)
        assert p.atraso_para_tentativa(10) == 60.0
        assert p.atraso_para_tentativa(100) == 60.0


class TestFlappingTracker:
    def test_poucos_restarts_nao_e_flapping(self):
        tracker = FlappingTracker(janela_s=3600, limite=6)
        for i in range(3):
            tracker.registrar_restart(agora=i)
        assert not tracker.esta_flapping(agora=3)

    def test_mais_de_6_na_janela_e_flapping(self):
        tracker = FlappingTracker(janela_s=3600, limite=6)
        for i in range(7):
            tracker.registrar_restart(agora=i)
        assert tracker.esta_flapping(agora=7)

    def test_eventos_fora_da_janela_nao_contam(self):
        tracker = FlappingTracker(janela_s=3600, limite=6)
        for i in range(7):
            tracker.registrar_restart(agora=i)  # todos em t=0..6
        # 2 horas depois, a janela de 1h não pega mais nenhum desses
        assert not tracker.esta_flapping(agora=7200)

    def test_exatamente_no_limite_nao_e_flapping(self):
        tracker = FlappingTracker(janela_s=3600, limite=6)
        for i in range(6):
            tracker.registrar_restart(agora=i)
        assert not tracker.esta_flapping(agora=6)
