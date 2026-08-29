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

🟡 Fase 1 em andamento. Concluídas: esqueleto (0), config/CLI/`probe` (1.1), supervisor +
estratégias de rede (1.2), catálogo SQLite + `recover` (1.4), GC por pressão + dois volumes (1.5),
miniaturas (1.6), HTTP local (1.7). Falta a 1.8 (empacotamento/bootstrap/`doctor`/`bench`) e o
teste de campo de 72h. DVB (1.3) adiado (GRV-01). 206 testes. Ver
[`fase1-gravador-autonomo.md`](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/fase1-gravador-autonomo.md)
pro checklist completo.

O `lupa-recorder run` grava as fontes do `channels.yaml` **e** sobe o servidor HTTP local
(`http://127.0.0.1:8383/v1/` + IP da tailnet) — playlist sintética, segmentos com Range,
miniaturas, `probe`. No boot ele loga uma URL de playlist já assinada por fonte, pronta pra abrir
no VLC.

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
