from __future__ import annotations

from lupa_recorder.capture.strategies.base import SourceStrategy
from lupa_recorder.resolve.base import ResolvedInput


class RtspStrategy(SourceStrategy):
    """RTSP — cotado no plano pro encoder próprio (§8.6), ainda sem fonte real testada na
    Fase 0 (o encoder não ficou disponível a tempo). `-rtsp_transport tcp` é o padrão
    recomendado (evita perda de pacote de UDP através de NAT/firewall — mesmo parâmetro já
    usado no `ffprobe` de validação em `comandos.md`). Reconexão via `-reconnect*` é segura
    aqui (RTSP não tem a armadilha de master playlist que HLS tem)."""

    def build_input(self, resolved: ResolvedInput) -> list[str]:
        return [
            "-rtsp_transport",
            "tcp",
            "-reconnect",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "30",
            "-i",
            resolved.urls[0],
        ]
