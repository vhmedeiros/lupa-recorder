"""Checagens de pré-condição da máquina — o que o `doctor` roda, o que o gate de
relógio do `run` reusa, e o que o timer systemd registra no catálogo (sub-etapa 1.8).

Chama-se `health/checks.py` (não `health/probe.py` como o tree do plano) — `probe.py` no
topo já é o probe de URL de fonte (§8.7); dois `probe` seriam confusos.
"""
