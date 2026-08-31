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

🟡 Fase 1 em andamento — **código de todas as sub-etapas de rede pronto** (0, 1.1, 1.2, 1.4, 1.5,
1.6, 1.7, 1.8). `bootstrap.sh` + `doctor` verde validados numa VM Debian 12 limpa (2026-08-31);
falta o teste de campo de 72h, o `bench` com fontes reais e a queda de energia. DVB (1.3) adiado
sem previsão (GRV-01). 246 testes. Ver
[`fase1-gravador-autonomo.md`](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/fase1-gravador-autonomo.md)
pro checklist completo.

- `lupa-recorder run` — grava as fontes do `channels.yaml` **e** sobe o HTTP local
  (`http://127.0.0.1:8383/v1/` + IP da tailnet: playlist sintética, segmentos com Range,
  miniaturas). Recusa iniciar se o relógio estiver > 2s fora (`--ignorar-relogio` de escape).
- `lupa-recorder doctor` — checa as pré-condições da máquina (`--json`, `--sem-rede`); grava o
  resultado no catálogo (o timer systemd roda de 15 em 15 min).
- `lupa-recorder bench` — mede a capacidade da máquina → `{system_root}/bench.json`.
- `packaging/` — `bootstrap.sh` (prepara o Debian), `install-release.sh` (layout versionado +
  symlink `current`), unidades systemd. Ver
  [`requisitos-hardware.md`](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/requisitos-hardware.md).

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
