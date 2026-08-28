"""Configuração do agente: agent.toml (identidade/caminhos/segredos da máquina) e
channels.yaml (as fontes que essa máquina grava).

Validação de estrutura (tipos, obrigatoriedade, regras entre campos) acontece aqui, na carga.
Validação de ambiente (disco existe, tem espaço) é um passo explícito separado
(Config.validate_environment) — não roda sozinha ao importar/instanciar, pra não exigir
filesystem real em todo teste unitário.
"""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

SEGUNDOS_POR_HORA = 3600
NOME_ARQUIVO_AGENT = "agent.toml"
NOME_ARQUIVO_CHANNELS = "channels.yaml"


class ConfigError(Exception):
    """Erro de configuração com mensagem já pronta pra mostrar ao operador."""


# ── agent.toml ────────────────────────────────────────────────────────────────


class AgentInfo(BaseModel):
    name: str = Field(min_length=1, description="Identidade desta máquina, ex.: recorder-maringa-01")


class PathsConfig(BaseModel):
    # HD — vídeo/áudio bruto e miniaturas. Escrita sequencial pesada, sem exigência de latência.
    data_root: Path
    # SSD — catálogo SQLite e scratch. Latência importa, volume escrito é pequeno.
    system_root: Path

    def disco_do_acervo_disponivel_gb(self) -> float:
        uso = shutil.disk_usage(self.data_root)
        return uso.free / (1024**3)


class RetentionWatermarks(BaseModel):
    """Watermarks do GC por pressão (§6.4 do plano) — independentes por volume."""

    watermark_high_system: float = Field(0.85, gt=0, lt=1)
    watermark_low_system: float = Field(0.70, gt=0, lt=1)
    watermark_high_data: float = Field(0.85, gt=0, lt=1)
    watermark_low_data: float = Field(0.70, gt=0, lt=1)

    @model_validator(mode="after")
    def low_menor_que_high(self) -> RetentionWatermarks:
        if self.watermark_low_system >= self.watermark_high_system:
            raise ValueError("watermark_low_system precisa ser menor que watermark_high_system")
        if self.watermark_low_data >= self.watermark_high_data:
            raise ValueError("watermark_low_data precisa ser menor que watermark_high_data")
        return self


class SecurityConfig(BaseModel):
    hmac_secret: str = Field(min_length=16, description="Segredo do auth HMAC da HTTP local (sub-etapa 1.7)")


class AgentConfig(BaseSettings):
    """Carrega de um agent.toml. Também aceita override por variável de ambiente
    (prefixo LUPA_RECORDER_, ex.: LUPA_RECORDER_AGENT__NAME=foo) — útil em teste/systemd,
    sem precisar de um agent.toml de verdade.
    """

    model_config = SettingsConfigDict(
        toml_file=NOME_ARQUIVO_AGENT,
        env_prefix="LUPA_RECORDER_",
        env_nested_delimiter="__",
    )

    agent: AgentInfo
    paths: PathsConfig
    retention: RetentionWatermarks = Field(default_factory=RetentionWatermarks)
    security: SecurityConfig

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env pode sobrescrever o que o toml disse; toml é a base.
        return (env_settings, TomlConfigSettingsSource(settings_cls))


def load_agent_config(path: Path | str = NOME_ARQUIVO_AGENT) -> AgentConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"{path} não existe. Copie agent.toml.example pra {path} e ajuste os valores "
            "pra esta máquina (nunca commite o arquivo real — tem segredo dentro)."
        )

    class _AgentConfig(AgentConfig):
        model_config = SettingsConfigDict(
            toml_file=path,
            env_prefix="LUPA_RECORDER_",
            env_nested_delimiter="__",
        )

    try:
        return _AgentConfig()
    except Exception as exc:  # ValidationError do pydantic já tem mensagem boa; só rotula a origem
        raise ConfigError(f"{path} inválido: {exc}") from exc


# ── channels.yaml ────────────────────────────────────────────────────────────


class SourceKind(StrEnum):
    tv = "tv"
    radio = "radio"


class Protocol(StrEnum):
    http = "http"
    hls = "hls"
    rtsp = "rtsp"
    youtube = "youtube"
    dvb = "dvb"


class UrlResolver(StrEnum):
    static = "static"
    http_refresh = "http_refresh"
    yt_dlp = "yt_dlp"


class Tier(StrEnum):
    critical = "critical"
    standard = "standard"
    background = "background"


# Perfis de transcode (qsv-*, vaapi-*, oneseg-*) dependem do caminho DVB — adiado, GRV-01.
# "copy" é o único suportado nesta fase (M4 do plano: -c copy é a regra, exceção é DVB).
PERFIS_SUPORTADOS_NESTA_FASE = frozenset({"copy"})

# Retenção alvo por tier, em dias — §6.4 do plano, mesmos números que o GC (sub-etapa 1.5) usa.
RETENCAO_DIAS_POR_TIER = {
    Tier.critical: 7,
    Tier.standard: 5,
    Tier.background: 2,
}


class SourceConfig(BaseModel):
    id: int = Field(gt=0)
    slug: str = Field(min_length=1, max_length=64)
    kind: SourceKind
    protocol: Protocol
    url: str | None = None
    url_resolver: UrlResolver = UrlResolver.static
    quality_profile: str | None = Field(
        default=None, description="Ex.: '480p' — só usado por url_resolver=yt_dlp hoje"
    )
    archive_profile: str = "copy"
    tier: Tier = Tier.standard
    transcribable: bool = False
    segment_seconds: int = Field(default=240, gt=0)
    thumbnails: bool = False

    @field_validator("slug")
    @classmethod
    def slug_valido(cls, v: str) -> str:
        if not v.replace("-", "").isalnum() or v != v.lower():
            raise ValueError(
                f"slug {v!r} inválido — só minúsculas, números e hífen "
                "(é o que vira nome de pasta no disco: {data_root}/{slug}/...)"
            )
        return v

    @model_validator(mode="after")
    def regras_entre_campos(self) -> SourceConfig:
        if SEGUNDOS_POR_HORA % self.segment_seconds != 0:
            raise ValueError(
                f"segment_seconds={self.segment_seconds} não divide {SEGUNDOS_POR_HORA} "
                "(3600) — o corte deriva ao longo do dia em vez de cair certo na virada de hora. "
                "Plano §7.5."
            )
        if self.thumbnails and self.kind != SourceKind.tv:
            raise ValueError(
                f"fonte {self.slug!r}: thumbnails=true só faz sentido em kind=tv "
                "(rádio não tem frame de vídeo pra tirar miniatura)."
            )
        if self.protocol == Protocol.dvb:
            raise ValueError(
                f"fonte {self.slug!r}: protocol=dvb ainda não é suportado nesta fase "
                "(GRV-01 — placa DVB adiada por custo). Cadastre fontes de rede por enquanto."
            )
        if self.protocol != Protocol.dvb and not self.url:
            raise ValueError(f"fonte {self.slug!r}: url é obrigatória pra protocol={self.protocol}")
        if self.archive_profile not in PERFIS_SUPORTADOS_NESTA_FASE:
            raise ValueError(
                f"fonte {self.slug!r}: archive_profile={self.archive_profile!r} não suportado "
                f"nesta fase — só {sorted(PERFIS_SUPORTADOS_NESTA_FASE)} (perfis de transcode "
                "dependem do caminho DVB, GRV-01)."
            )
        if self.protocol == Protocol.youtube and self.url_resolver != UrlResolver.yt_dlp:
            raise ValueError(
                f"fonte {self.slug!r}: protocol=youtube precisa de url_resolver=yt_dlp "
                "(achado de campo da Fase 0 — não tem formato combinado, precisa resolver via yt-dlp)."
            )
        return self


class ChannelsConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def sem_duplicata(self) -> ChannelsConfig:
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            repetidos = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"ids duplicados em channels.yaml: {repetidos}")
        slugs = [s.slug for s in self.sources]
        if len(slugs) != len(set(slugs)):
            repetidos = sorted({s for s in slugs if slugs.count(s) > 1})
            raise ValueError(f"slugs duplicados em channels.yaml: {repetidos}")
        return self


def load_channels_config(path: Path | str = NOME_ARQUIVO_CHANNELS) -> ChannelsConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"{path} não existe. Copie channels.yaml.example pra {path} e cadastre as fontes "
            "desta máquina."
        )
    bruto = yaml.safe_load(path.read_text()) or {}
    try:
        return ChannelsConfig.model_validate(bruto)
    except Exception as exc:
        raise ConfigError(f"{path} inválido: {exc}") from exc


# ── config completa ──────────────────────────────────────────────────────────


class Config(BaseModel):
    agent: AgentConfig
    channels: ChannelsConfig

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def load(
        cls,
        agent_path: Path | str = NOME_ARQUIVO_AGENT,
        channels_path: Path | str = NOME_ARQUIVO_CHANNELS,
    ) -> Config:
        return cls(agent=load_agent_config(agent_path), channels=load_channels_config(channels_path))

    def validate_environment(self) -> list[str]:
        """Checagens que precisam do filesystem real — chamado explicitamente (doctor/run),
        nunca no load(). Devolve lista de problemas; vazia == tudo certo.
        """
        problemas: list[str] = []
        for nome, caminho in (
            ("data_root", self.agent.paths.data_root),
            ("system_root", self.agent.paths.system_root),
        ):
            if not caminho.exists():
                problemas.append(f"{nome} ({caminho}) não existe.")
            elif not caminho.is_dir():
                problemas.append(f"{nome} ({caminho}) existe mas não é um diretório.")
            elif shutil.disk_usage(caminho).free < 1024**3:
                problemas.append(f"{nome} ({caminho}) tem menos de 1 GB livre.")
        return problemas
