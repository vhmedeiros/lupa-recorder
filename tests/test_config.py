import pytest
from pydantic import ValidationError

from lupa_recorder.config import (
    ChannelsConfig,
    Config,
    ConfigError,
    SourceConfig,
    load_agent_config,
    load_channels_config,
)

AGENT_TOML_VALIDO = """
[agent]
name = "recorder-teste-01"

[paths]
data_root = "/mnt/acervo/lupa-recorder"
system_root = "/var/lib/lupa-recorder"

[security]
hmac_secret = "segredo-com-mais-de-16-caracteres"
"""

FONTE_HTTP_VALIDA = {
    "id": 1,
    "slug": "radio-teste",
    "kind": "radio",
    "protocol": "http",
    "url": "http://exemplo.com/stream",
}


def test_carrega_agent_toml_valido(tmp_path):
    caminho = tmp_path / "agent.toml"
    caminho.write_text(AGENT_TOML_VALIDO)

    cfg = load_agent_config(caminho)

    assert cfg.agent.name == "recorder-teste-01"
    assert str(cfg.paths.data_root) == "/mnt/acervo/lupa-recorder"
    assert cfg.retention.watermark_high_data == pytest.approx(0.85)


def test_agent_toml_inexistente_da_erro_claro(tmp_path):
    with pytest.raises(ConfigError, match="não existe"):
        load_agent_config(tmp_path / "nao-existe.toml")


def test_agent_toml_sem_hmac_secret_falha(tmp_path):
    caminho = tmp_path / "agent.toml"
    caminho.write_text("""
[agent]
name = "x"
[paths]
data_root = "/a"
system_root = "/b"
[security]
hmac_secret = "curto"
""")
    with pytest.raises(ConfigError):
        load_agent_config(caminho)


def test_watermark_low_maior_que_high_falha(tmp_path):
    caminho = tmp_path / "agent.toml"
    caminho.write_text("""
[agent]
name = "x"
[paths]
data_root = "/a"
system_root = "/b"
[retention]
watermark_high_data = 0.5
watermark_low_data = 0.9
[security]
hmac_secret = "segredo-com-mais-de-16-caracteres"
""")
    with pytest.raises(ConfigError, match="watermark_low_data"):
        load_agent_config(caminho)


def test_env_var_sobrescreve_toml(tmp_path, monkeypatch):
    caminho = tmp_path / "agent.toml"
    caminho.write_text(AGENT_TOML_VALIDO)
    monkeypatch.setenv("LUPA_RECORDER_AGENT__NAME", "recorder-via-env")

    cfg = load_agent_config(caminho)

    assert cfg.agent.name == "recorder-via-env"


class TestSourceConfig:
    def test_fonte_valida(self):
        fonte = SourceConfig(**FONTE_HTTP_VALIDA)
        assert fonte.segment_seconds == 240
        assert fonte.archive_profile == "copy"

    @pytest.mark.parametrize("segundos", [250, 700, 1000])
    def test_segment_seconds_precisa_dividir_3600(self, segundos):
        with pytest.raises(ValidationError, match="não divide"):
            SourceConfig(**{**FONTE_HTTP_VALIDA, "segment_seconds": segundos})

    def test_thumbnails_so_em_tv(self):
        with pytest.raises(ValidationError, match="thumbnails"):
            SourceConfig(**{**FONTE_HTTP_VALIDA, "kind": "radio", "thumbnails": True})

    def test_thumbnails_em_tv_ok(self):
        fonte = SourceConfig(
            **{**FONTE_HTTP_VALIDA, "kind": "tv", "protocol": "hls", "thumbnails": True}
        )
        assert fonte.thumbnails is True

    def test_protocol_dvb_rejeitado(self):
        with pytest.raises(ValidationError, match="GRV-01"):
            SourceConfig(**{**FONTE_HTTP_VALIDA, "protocol": "dvb", "url": None})

    def test_url_obrigatoria_fora_de_dvb(self):
        with pytest.raises(ValidationError, match="url é obrigatória"):
            SourceConfig(**{**FONTE_HTTP_VALIDA, "url": None})

    def test_archive_profile_nao_copy_rejeitado(self):
        with pytest.raises(ValidationError, match="não suportado"):
            SourceConfig(**{**FONTE_HTTP_VALIDA, "archive_profile": "qsv-576p"})

    def test_youtube_exige_resolver_yt_dlp(self):
        with pytest.raises(ValidationError, match="yt_dlp"):
            SourceConfig(
                **{
                    **FONTE_HTTP_VALIDA,
                    "kind": "tv",
                    "protocol": "youtube",
                    "url_resolver": "static",
                }
            )

    def test_slug_invalido_rejeitado(self):
        with pytest.raises(ValidationError, match="slug"):
            SourceConfig(**{**FONTE_HTTP_VALIDA, "slug": "Rádio Teste!"})


class TestChannelsConfig:
    def test_ids_duplicados_rejeitados(self):
        with pytest.raises(ValidationError, match="ids duplicados"):
            ChannelsConfig(
                sources=[
                    FONTE_HTTP_VALIDA,
                    {**FONTE_HTTP_VALIDA, "slug": "outra-fonte"},
                ]
            )

    def test_slugs_duplicados_rejeitados(self):
        with pytest.raises(ValidationError, match="slugs duplicados"):
            ChannelsConfig(
                sources=[
                    FONTE_HTTP_VALIDA,
                    {**FONTE_HTTP_VALIDA, "id": 2},
                ]
            )

    def test_lista_vazia_ok(self):
        assert ChannelsConfig(sources=[]).sources == []


def test_load_channels_config_arquivo_yaml(tmp_path):
    caminho = tmp_path / "channels.yaml"
    caminho.write_text("""
sources:
  - id: 1
    slug: radio-teste
    kind: radio
    protocol: http
    url: "http://exemplo.com/stream"
""")
    cfg = load_channels_config(caminho)
    assert len(cfg.sources) == 1
    assert cfg.sources[0].slug == "radio-teste"


def test_load_channels_config_inexistente_da_erro_claro(tmp_path):
    with pytest.raises(ConfigError, match="não existe"):
        load_channels_config(tmp_path / "nao-existe.yaml")


def test_config_validate_environment_acusa_data_root_ausente(tmp_path):
    agent_toml = tmp_path / "agent.toml"
    agent_toml.write_text(f"""
[agent]
name = "x"
[paths]
data_root = "{tmp_path / "nao-existe-data"}"
system_root = "{tmp_path}"
[security]
hmac_secret = "segredo-com-mais-de-16-caracteres"
""")
    channels_yaml = tmp_path / "channels.yaml"
    channels_yaml.write_text("sources: []")

    cfg = Config.load(agent_toml, channels_yaml)
    problemas = cfg.validate_environment()

    assert any("data_root" in p for p in problemas)


def test_config_validate_environment_ok(tmp_path):
    agent_toml = tmp_path / "agent.toml"
    agent_toml.write_text(f"""
[agent]
name = "x"
[paths]
data_root = "{tmp_path}"
system_root = "{tmp_path}"
[security]
hmac_secret = "segredo-com-mais-de-16-caracteres"
""")
    channels_yaml = tmp_path / "channels.yaml"
    channels_yaml.write_text("sources: []")

    cfg = Config.load(agent_toml, channels_yaml)

    assert cfg.validate_environment() == []
