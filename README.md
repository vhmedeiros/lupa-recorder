# lupa-recorder

Gravador autônomo de TV/rádio da plataforma **Lupa**. Roda em máquinas espalhadas pelo Brasil,
grava canais de TV/rádio 24h/dia a partir de fontes de rede (HLS, HTTP progressivo, RTSP, YouTube —
DVB entra depois), gerencia os próprios discos, gera miniaturas e serve o acervo por HTTP local.
**Sem nenhuma dependência da Lupa (Django) em runtime** — grava dias a fio com a plataforma
offline; ela é só observadora/coordenadora.

Python 3.11+, sem Django, sem Postgres — repo deliberadamente separado da Lupa (roda em dezenas de
máquinas com ciclo de release próprio, precisa ser leve).

> ### 📚 Plano e roadmap completos vivem no monorepo da Lupa
>
> Este repo nasce a partir do planejamento em
> [`Lupa/docs/gravacao-tv-radio/`](https://github.com/vhmedeiros/Lupa/tree/main/docs/gravacao-tv-radio)
> — `PLANO-GRAVACAO-TV-RADIO.md` (o plano completo), `roadmap.md` (status por fase),
> `fase1-gravador-autonomo.md` (o checklist desta fase, sub-etapa por sub-etapa), `issues.md`
> (decisões e riscos em aberto) e `comandos.md` (cheatsheet de campo da Fase 0). Este README trata
> só de instalação e operação deste repo — não duplica o plano.

## Status

⬜ Sub-etapa 0 (esqueleto do repo) em andamento. Nada funcional ainda — ver
[`fase1-gravador-autonomo.md`](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/fase1-gravador-autonomo.md)
pro checklist completo.

## Setup de desenvolvimento

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## `bench.md`

Números reais medidos em campo (hardware, rede, comportamento de cada tipo de fonte) — migrado da
Fase 0. Ver [`bench.md`](bench.md).
