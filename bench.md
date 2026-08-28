# `bench.md` — números reais medidos na Fase 0

> Migrado do monorepo Lupa em 2026-08-28 (sub-etapa 0 da Fase 1 —
> [`fase1-gravador-autonomo.md`](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/fase1-gravador-autonomo.md)).
> Esta é a fonte de verdade agora — a cópia antiga em `Lupa/docs/gravacao-tv-radio/bench.md` foi
> substituída por um redirecionamento pra cá.
>
> Cada linha cita de onde veio o dado, pra rastrear até o teste original em
> [`fase0-prova-de-campo.md`](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/fase0-prova-de-campo.md)
> e no
> [Apêndice E do plano](https://github.com/vhmedeiros/Lupa/blob/main/PLANO-GRAVACAO-TV-RADIO.md#apêndice-e--diário-de-bordo-da-fase-0-achados-de-campo-em-andamento).
>
> **Máquina de teste única até agora:** Debian 12 minimal, Intel i9-11900F, 16GB RAM, disco único
> `/dev/sda1`. Todo número abaixo é dessa máquina — não extrapolar pra outro hardware sem medir de
> novo (princípio #5 do plano: nada de capacidade prometida sem medição).

## Hardware da máquina de teste

| Item | Valor medido | Fonte |
|---|---|---|
| CPU | Intel i9-11900F — 8 núcleos / 16 threads | `lscpu`, 2026-08-26 |
| RAM | 16 GB | `free -h`, 2026-08-26 |
| Disco | `/dev/sda1`, 219 GB total (204 GB livres / 4,8 GB usados após um dia inteiro de testes) | `df -h`, 2026-08-26 |
| GPU / encode por hardware | NVIDIA GeForce G210 (chip GT218, ~2010) — **anterior ao NVENC**. CPU tem sufixo `F` — **sem iGPU Intel/QSV**. **Conclusão: zero caminho de transcode por hardware nesta máquina hoje.** | `lspci \| grep -i vga`, 2026-08-26 — ver [`issues.md` GRV-08](https://github.com/vhmedeiros/Lupa/blob/main/docs/gravacao-tv-radio/issues.md#grv-08--máquina-de-teste-i9-11900f-não-tem-nenhum-encode-por-hardware-disponível-revisado) |
| Partição dedicada ao acervo | ✅ **Criada (2026-08-26).** `/mnt/acervo`, ext4, `noatime`, 1,8TB — ver detalhe de disco abaixo. | `fase0-prova-de-campo.md` §A.1 |

### Topologia real de disco (achado, não esperado)

A máquina tem **3 discos**, não 1 como o A.1 registrou inicialmente:

| Disco | Modelo | Tipo | Leitura (`hdparm -tT`) | Papel |
|---|---|---|---|---|
| `sda` (223GB) | "XrayDisk 240GB" | **SSD** | 428 MB/s buffered | Sistema (`/`) — tudo rodou aqui até a criação do acervo |
| `sdb` (1,8TB) | Seagate `ST2000DM001` | HD mecânico 7200rpm | 176 MB/s buffered | Livre, não usado ainda (tinha boot do Windows + dados NTFS — apagado com autorização do usuário, disponível pra um segundo uso futuro) |
| `sdc` (1,8TB) | Seagate `ST2000DM001` | HD mecânico 7200rpm | — | **Formatado e montado como o acervo** (`/mnt/acervo`, ext4, `noatime`, label `acervo`) |

**Escrita concorrente real no disco do acervo (`fio`, 8 processos, `bs=1M`, `--direct=1`, bypassando cache):**

| Disco testado | BW sustentado | Latência média | `%util` |
|---|---|---|---|
| `sda` (SSD, teste inicial — disco errado, não é onde o acervo mora) | 72 MB/s | 111ms | 98% |
| **`sdc` (HD mecânico, disco real do acervo)** | **150 MB/s** | 55ms (p99 180ms) | 96% |

**Conclusão:** a conta do plano §6.2 (60-90 MB/s pra HD de 2010) foi **superada com folga** neste HD específico (2TB Seagate, mais novo que os "HD de 2010" hipotéticos do plano). Como 8 canais reais consomem no máximo dezenas de MB/s agregados (não centenas), **disco não é gargalo nesta máquina de teste** — confirma a Conta 2/3 do plano, com número real em vez de estimado.

**Nota importante:** essa é a máquina de *teste* da Fase 0, não uma das máquinas de 2010 da frota real de campo (que o plano assume ter HD mecânico velho e pouca RAM). O achado de CPU/rede desta máquina generaliza em espírito (captura é barata, disco tem folga); os números exatos de disco **não** — cada máquina da frota real precisa do próprio `lupa-recorder bench` antes de contar com números específicos (princípio #5 do plano).

## Relógio e rede

| Item | Valor medido | Fonte |
|---|---|---|
| Drift de relógio (`chronyc tracking`) | **~0,5 ms** de offset — bem abaixo da meta de 200 ms | `fase0-prova-de-campo.md` §A.1, 2026-08-26 |
| Tailscale → VM2 (`a1-heitordolago1`) | **25 ms, conexão direta** (sem relay DERP) | `tailscale ping`, 2026-08-26 |
| Tailscale → outros peers | `a1-marianalacerda2208` e `a1`: direct. `a1-rmcelestinos`: passou brevemente por DERP, convergiu pra direct. | `tailscale ping`, 2026-08-26 |

## Rádios testadas

| # | Fonte | Protocolo | Formato/bitrate | Token? | Resultado |
|---|---|---|---|---|---|
| 1 | Jovem Pan News SP (SurferNetwork) | HTTP progressivo | MP3 44.1kHz/128kbps | Sim, `zt=` JWT | Usada nos testes de reconexão — ver GRV-04 (resolvido, ver `issues.md` no monorepo Lupa) |
| 2 | Ouveai (`stream01.ouveai.com.br:1072`) | HTTP progressivo | HE-AAC 64kbps | Não | **13h36min contínuas** (13:02→02:38, 2026-08-26/27), então **morreu por falha de DNS local** (não da fonte — ver achado abaixo). Religada 2026-08-27 07:20 em pasta nova (`radio_ouveai`), relógio zerado rumo a um 24h limpo |
| 3 | Jovem Pan Nacional (SurferNetwork) | HTTP progressivo | — | Sim, `zt=` JWT | **Queda natural (sem pausa manual) em ~1h44min** de streaming contínuo (17:39:49→~19:24, 2026-08-26) — fecha a pergunta "quanto tempo até a primeira queda". Religada 2026-08-27 07:24 com URL fresca; teste de reconexão de 3h funcionou (ver tabela de tolerância abaixo) |
| 4 | Cultura AM (UOL) | **HLS** | — | Não | Achou o bug `-reconnect*`+HLS (corrigido); pausa curta não derruba mas pula pra borda ao vivo (buraco do tamanho da pausa). **Sobreviveu ileso à falha de DNS local** que matou #2/#5/#6 — processo nunca reiniciado, rodando desde 2026-08-26 17:52, **~13h26min contínuas** até a checagem das 07:18 de 2026-08-27, junto com a TV Cultura (mesmo grupo UOL) rumo ao 24h |
| 5 | Roraima AM 590 (`stm1.srvif.com`) | HTTP progressivo | — | Não | Segmento inicial confirmado. **Também morreu na mesma falha de DNS local** (~02:36-02:38, 2026-08-27) |
| 6 | Band News FM SP (StreamTheWorld/Triton) | HTTP progressivo | — | Sem token visível | Segmento inicial confirmado. **Também morreu na mesma falha de DNS local** (~02:36-02:38, 2026-08-27) |

### Tolerância do token SurferNetwork (`zt=`) — GRV-04, resolvido

| Tempo entre emissão do token e reconexão | Resultado |
|---|---|
| ~20-25 min | ✅ Funcionou |
| 1 hora (3600s, script automatizado) | ✅ Funcionou |
| 3 horas | ✅ Funcionou — pausado 07:27:21, retomado 10:27:21, segmento novo (`20260827-102800.ts`) com áudio válido |
| ~8 horas | ❌ Falhou (HTTP 500) |

**Literal do JWT decodificado sugeria `exp` = 60s de vida — bem mais curto que o comportamento real.**
**Decisão que saiu daqui (aplicada em `strategies/youtube.py`/`resolve/http_refresh.py` desta fase):**
refresh periódico simples (30-45min), não o modo "por tentativa de conexão" — a tolerância medida
(≥3h) sobra folga enorme sobre qualquer intervalo de refresh razoável.

## TV testadas

| Fonte | Protocolo | Renditions | Bitrate real | Resultado |
|---|---|---|---|---|
| TV Cultura (substituto do encoder real) | HLS | Única (960x540, ~717kbps nominal) | **~963 kbps medido → ~10,4 GB/dia** | Válida, cabe tranquilo no disco. **Achado (2026-08-27):** depois de ~21h rodando, um reset de CDN fez o processo entrar num loop de reconexão morto (`HTTP error 404`/`Failed to reload playlist 0` repetindo) — processo continuou "vivo" no `ps` mas parou de produzir segmento novo. Mesmo padrão do achado Bloomberg, agora numa fonte-alvo real; matado (`kill`, escalando pra `kill -9`) e religado |
| Bloomberg (Google DAI, ad-supported) | HLS | 720p (amostra) | — | **Descartada.** Sessão DAI expira em ~5s após o primeiro segmento (HTTP 404). Processo trava em loop de reconexão morto — precisou `SIGKILL`, `SIGTERM` foi ignorado |
| Encoder próprio | — | — | — | ⏳ **Ainda sem acesso** — bloqueado até chegar |

## YouTube (canal SBT)

| Item | Valor medido |
|---|---|
| Formato combinado disponível? | **Não** — só `video only` + `audio only` separados |
| Formato escolhido | Vídeo 480p (id 231, ~1,5 Mbps) + áudio "high" (id 234) |
| `-thread_queue_size` necessário | **1024** em cada `-i` (sem isso: `PES packet size mismatch`/pacote corrompido) |
| Estabilidade confirmada | ~1h contínua (18:46–19:44), 13+ segmentos, sem corrupção |
| Precisão do corte de segmento | Oscila entre 3min54s e 4min04s — ruído estável, **não deriva** com o tempo |
| Tempo até a URL resolvida expirar | **~6 horas** (primeiro segmento 18:44:04, último 00:44:04, 2026-08-26/27 — depois disso, `HTTP error 403 Forbidden` em todas as URLs do `googlevideo.com`, sessão morta) — bem acima da estimativa de ~4h do plano §7.4. **Decisão aplicada em `strategies/youtube.py`: restart a cada 3h** (metade da janela medida, margem de segurança) |

## Bugs de comando encontrados (relevantes para `capture/strategies/*.py` desta fase)

| Bug | Onde apareceu | Correção |
|---|---|---|
| `-reconnect*` com fonte HLS trava relendo o master playlist pra sempre | Cultura AM, Bloomberg (2x — vale ficar atento, é fácil repetir) | Nunca usar `-reconnect*` em URLs `.m3u8`. Só em HTTP progressivo (Ouveai, Roraima, Band News) |
| Fila de thread padrão (8) estoura ao mixar 2 `-i` HLS independentes | YouTube (vídeo + áudio separados) | `-thread_queue_size 1024` em cada `-i` |
| Ffmpeg preso em loop de reconexão ignora `SIGTERM` | Bloomberg (404 da sessão DAI); **confirmado de novo em TV Cultura (2026-08-27)**, depois de ~21h — dessa vez numa fonte-alvo real, não só na descartada | Supervisor precisa usar `SIGKILL` (ou `SIGTERM` com timeout curto escalando pra `SIGKILL`) — confirmado necessário, não só prudente |
| Pausa curta em fonte HLS não derruba processo, mas pula pra borda ao vivo | Cultura AM (`kill -STOP`/`-CONT`) | Aceito como comportamento esperado — gera buraco do tamanho da pausa, não crash |
| Falha de DNS mata o processo mesmo com `-reconnect*` ativo — o flag só cobre reconexão HTTP, não resolução de hostname | 3 rádios (Ouveai, Roraima, Band News) morreram juntas (~02:36-02:38, 2026-08-27) numa falha de DNS local transitória (`getent hosts` confirmou resolução normal minutos depois — a fonte nunca caiu, foi o resolver da própria máquina de teste) | Confirma que o supervisor não pode depender do `-reconnect*` interno do ffmpeg — precisa matar e reiniciar o processo do zero por "sem segmento novo em N s", independente do tipo de falha (HTTP, DNS, o que for) |

## Filosofia de aceite (decidida em 2026-08-27, vale pra toda a Fase 1 em diante)

**"Nunca vamos conseguir gravar algo 24h sem quebrar, sempre vai ter uma perda ou outra de arquivos.
Precisamos aprender a trabalhar com isso."** — decisão do usuário depois de ver o `radio_cultura` se
recuperar sozinho de um reset de CDN e o `tv-cultura` travar num loop reconhecível. O critério de
aceite não é "nunca falha", é **"detecta a falha e se recupera em tempo razoável, com o gap
entendido"**. Isso guia o design do supervisor (backoff + watchdog de "sem byte novo") e como avaliar
se cada sub-etapa desta fase está pronta.

## Pendências de medição

- [ ] `ffprobe`/acesso ao encoder próprio — bitrate real, renditions disponíveis, protocolo exato (m3u8 ou RTSP).
- [ ] Fator de tempo real do Silero VAD nessa CPU (não medido — precisa de uma fonte transcritível, Fase 3).
