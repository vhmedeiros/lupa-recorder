"""Empacota as miniaturas de uma hora fechada num sprite único (grade 10×6) + as cues do
WebVTT do dia. Plano §11.4 — resolve o problema de "1.440 requisições HTTP pra desenhar um
rodapé": o dia inteiro custa 24 sprites + um VTT, não 1.440 imagens avulsas.

Sem ImageMagick — só Pillow. Pura manipulação de imagem, 100% testável sem `ffmpeg`.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

COLUNAS = 10
LINHAS = 6
CELULAS_POR_SPRITE = COLUNAS * LINHAS  # 60 — uma miniatura por minuto da hora


def montar_sprite_da_hora(
    miniaturas: list[Path], destino: Path, *, largura: int = 160, altura: int = 90
) -> Path:
    """`miniaturas` já vem ordenada por minuto (índice 0 = `:00`, ..., 59 = `:59`). Menos de
    60 é normal (hora incompleta, alguma extração falhou) — as células que sobram ficam em
    branco, não é erro."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    sprite = Image.new("RGB", (largura * COLUNAS, altura * LINHAS), color=(0, 0, 0))
    for indice, caminho in enumerate(miniaturas[:CELULAS_POR_SPRITE]):
        coluna, linha = indice % COLUNAS, indice // COLUNAS
        with Image.open(caminho) as miniatura:
            miniatura = miniatura.resize((largura, altura))
            sprite.paste(miniatura, (coluna * largura, linha * altura))
    sprite.save(destino, "JPEG", quality=85)
    return destino


def _formatar_timestamp(segundos: float) -> str:
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{int(horas):02d}:{int(minutos):02d}:{segs:06.3f}"


def gerar_cues_sprite(
    url_sprite: str, quantidade: int, *, offset_s: int = 0, largura: int = 160, altura: int = 90
) -> list[str]:
    """Uma cue de 1 min por miniatura, todas apontando pro mesmo sprite via `#xywh=`.
    `offset_s` desloca pro horário certo dentro do VTT do **dia** — sem isso, toda hora
    geraria cues começando em `00:00`, todas se sobrepondo."""
    cues = []
    for indice in range(quantidade):
        coluna, linha = indice % COLUNAS, indice // COLUNAS
        inicio = _formatar_timestamp(offset_s + indice * 60)
        fim = _formatar_timestamp(offset_s + (indice + 1) * 60)
        x, y = coluna * largura, linha * altura
        cues.append(f"{inicio} --> {fim}\n{url_sprite}#xywh={x},{y},{largura},{altura}")
    return cues


def gerar_cues_avulsas(urls_por_minuto: list[str], *, offset_s: int = 0) -> list[str]:
    """Hora corrente, sprite ainda não fechou — o VTT aponta pras miniaturas avulsas até lá
    (plano §11.4: "são no máximo 60 requisições pequenas, só da hora em curso"). Mesmo
    `offset_s` do sprite, mesmo motivo."""
    cues = []
    for indice, url in enumerate(urls_por_minuto):
        inicio = _formatar_timestamp(offset_s + indice * 60)
        fim = _formatar_timestamp(offset_s + (indice + 1) * 60)
        cues.append(f"{inicio} --> {fim}\n{url}")
    return cues


def montar_webvtt(cues: list[str]) -> str:
    if not cues:
        return "WEBVTT\n"
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n"
