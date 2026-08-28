# CLAUDE.md

Guia rápido para o Claude Code neste repositório.

## O que é este repo

`lupa-recorder` — o agente que roda nas máquinas gravadoras de TV/Rádio da plataforma **Lupa**
(repo-irmão em `/home/django/projetos/Lupa`, ou `github.com/vhmedeiros/Lupa`). Python 3.11+, sem
Django, sem Postgres — deliberadamente separado e leve, porque roda em dezenas de máquinas
espalhadas pelo Brasil, com ciclo de release próprio.

## Onde está o plano — leia antes de codar

**O planejamento completo desta feature mora no monorepo `Lupa`, não aqui:**

- [`Lupa/docs/gravacao-tv-radio/fase1-gravador-autonomo.md`](../Lupa/docs/gravacao-tv-radio/fase1-gravador-autonomo.md)
  — **o checklist de trabalho desta fase, sub-etapa por sub-etapa.** Comece por aqui sempre. Marque
  `[x]` conforme cada item for concluído — este arquivo é a fonte de verdade do progresso, não
  duplicar em outro lugar.
- [`Lupa/docs/gravacao-tv-radio/guia-para-leigos.md`](../Lupa/docs/gravacao-tv-radio/guia-para-leigos.md)
  — visão geral sem jargão de todo o projeto de gravação, todas as 6 fases.
- [`Lupa/docs/gravacao-tv-radio/roadmap.md`](../Lupa/docs/gravacao-tv-radio/roadmap.md) — status de
  todas as fases.
- [`Lupa/docs/gravacao-tv-radio/issues.md`](../Lupa/docs/gravacao-tv-radio/issues.md) — decisões e
  riscos em aberto (catálogo `GRV-NN`).
- [`Lupa/PLANO-GRAVACAO-TV-RADIO.md`](../Lupa/PLANO-GRAVACAO-TV-RADIO.md) — o plano técnico completo
  (arquitetura, contas de capacidade, comandos exatos, justificativa de cada decisão). Denso —
  consultar a seção específica que o `fase1-gravador-autonomo.md` referencia, não ler inteiro.

Se o caminho acima não existir (repo `Lupa` não clonado ao lado deste), peça ao usuário o caminho
correto ou o conteúdo relevante antes de assumir requisitos.

## `bench.md`

Números reais medidos em campo (hardware, comportamento de cada tipo de fonte, bugs de comando já
encontrados) — [`bench.md`](bench.md), neste repo. Consultar antes de implementar qualquer
`capture/strategies/*.py` — várias decisões (ex.: nunca `-reconnect*` em HLS, `-thread_queue_size
1024` no YouTube, restart a cada 3h) já vêm de achados de campo documentados lá, não são escolha
livre.

## Convenções deste repo

- `ruff.toml`: `target-version = "py311"` (não `py312`, ao contrário do repo Lupa) — desvio
  deliberado: Debian 12, a distro-alvo das máquinas de campo, vem com Python 3.11.
- `src/lupa_recorder/` é código, git-tracked. **Nunca** dado de gravação aqui dentro — isso vai em
  `data_root` (configurável em `agent.toml`, fora do repo — ver ajustes de 2026-08-28 no
  `fase1-gravador-autonomo.md`).
- Commits diretos na `main` por enquanto (repo novo, sem colaboradores ainda) — decisão de
  2026-08-28, revisar quando fizer sentido.
- `pytest` + `ruff check .` antes de qualquer commit.

## Atualize a documentação junto

Mesma regra do monorepo Lupa: se o código mudou e o `fase1-gravador-autonomo.md` não reflete isso,
a tarefa está incompleta. Esse arquivo vive no repo `Lupa`, não aqui — editar lá.
