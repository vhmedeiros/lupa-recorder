"""Empacota as miniaturas de uma hora fechada num sprite único (grade 10×6) + as cues do
WebVTT do dia. Plano §11.4 — resolve o problema de "1.440 requisições HTTP pra desenhar um
rodapé": o dia inteiro custa 24 sprites + um VTT, não 1.440 imagens avulsas.

**A célula N do sprite é sempre o minuto N da hora** (não a N-ésima miniatura que existe) —
o VTT mapeia `xywh` por minuto, então uma hora que começou às 22 min tem as células 0-21
em branco e o frame das 22h04 na célula 22, não na 0. Foi o bug que a validação de campo
(2026-08-29) pegou: com empacotamento sequencial, o frame das 13h47 aparecia rotulado 13h00.

Sem ImageMagick — só Pillow. Pura manipulação de imagem, 100% testável sem `ffmpeg`.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

COLUNAS = 10
LINHAS = 6
CELULAS_POR_SPRITE = COLUNAS * LINHAS  # 60 — uma miniatura por minuto da hora


def montar_sprite_da_hora(
    miniaturas_por_minuto: dict[int, Path], destino: Path, *, largura: int = 160, altura: int = 90
) -> Path:
    """`miniaturas_por_minuto`: `{minuto_da_hora (0-59): Path}`. Minutos ausentes ficam em
    branco (hora incompleta, extração falhada) — não é erro. Minutos fora de 0-59 são
    ignorados."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    sprite = Image.new("RGB", (largura * COLUNAS, altura * LINHAS), color=(0, 0, 0))
    for minuto, caminho in sorted(miniaturas_por_minuto.items()):
        if not 0 <= minuto < CELULAS_POR_SPRITE:
            continue
        coluna, linha = minuto % COLUNAS, minuto // COLUNAS
        with Image.open(caminho) as miniatura:
            sprite.paste(miniatura.resize((largura, altura)), (coluna * largura, linha * altura))
    sprite.save(destino, "JPEG", quality=85)
    return destino


def _formatar_timestamp(segundos: float) -> str:
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{int(horas):02d}:{int(minutos):02d}:{segs:06.3f}"


def gerar_cues_sprite(
    url_sprite: str, quantidade: int, *, offset_s: int = 0, largura: int = 160, altura: int = 90
) -> list[str]:
    """Uma cue de 1 min por célula, todas apontando pro mesmo sprite via `#xywh=`.
    `offset_s` desloca pro horário certo dentro do VTT do **dia** (senão toda hora geraria
    cues começando em `00:00`, todas se sobrepondo). Célula N = minuto N — casa com o
    empacotamento por minuto de `montar_sprite_da_hora`."""
    cues = []
    for indice in range(quantidade):
        coluna, linha = indice % COLUNAS, indice // COLUNAS
        inicio = _formatar_timestamp(offset_s + indice * 60)
        fim = _formatar_timestamp(offset_s + (indice + 1) * 60)
        x, y = coluna * largura, linha * altura
        cues.append(f"{inicio} --> {fim}\n{url_sprite}#xywh={x},{y},{largura},{altura}")
    return cues


def gerar_cues_avulsas(urls_por_minuto: dict[int, str], *, offset_s: int = 0) -> list[str]:
    """Hora corrente, sprite ainda não fechou — o VTT aponta pras miniaturas avulsas até lá
    (plano §11.4). `{minuto_da_hora: url}` — cue só pros minutos que têm frame, no horário
    real do minuto (não sequencial: um frame das 13h47 vai pra cue 13:47, não 13:00)."""
    cues = []
    for minuto, url in sorted(urls_por_minuto.items()):
        inicio = _formatar_timestamp(offset_s + minuto * 60)
        fim = _formatar_timestamp(offset_s + (minuto + 1) * 60)
        cues.append(f"{inicio} --> {fim}\n{url}")
    return cues


def montar_webvtt(cues: list[str]) -> str:
    if not cues:
        return "WEBVTT\n"
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n"
