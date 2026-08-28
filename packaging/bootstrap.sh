#!/bin/bash
# bootstrap.sh — instalação mínima do lupa-recorder (sub-etapa 0 do plano).
#
# Idempotente: rodar de novo numa máquina já preparada não deve quebrar nada.
#
# Deliberadamente NÃO particiona/formata disco nenhum. Formatar o disco errado numa
# máquina de terceiros é destrutivo e irreversível — isso fica um passo manual, documentado
# no checklist de instalação (docs/gravacao-tv-radio/requisitos-hardware.md, no monorepo Lupa).
# Este script só confere se a montagem já existe e avisa se não existir.
#
# Uso:
#   sudo ACERVO_MOUNT=/mnt/acervo ./bootstrap.sh
set -euo pipefail

ACERVO_MOUNT="${ACERVO_MOUNT:-/mnt/acervo}"
LUPA_USER="${LUPA_USER:-lupa}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Precisa rodar como root (sudo)." >&2
  exit 1
fi

echo "== Pacotes =="
apt-get update
apt-get install -y ffmpeg curl chrony

echo "== chrony =="
systemctl enable --now chrony
chronyc tracking || true

echo "== Tailscale =="
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "tailscale já instalado, pulando."
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "⚠️  Tailscale instalado mas não autenticado nesta máquina."
  echo "    Rode manualmente: tailscale up"
  echo "    (auth automática via authkey fica pra quando isso virar rotina de fábrica — não agora)"
fi

echo "== Usuário $LUPA_USER =="
if ! id "$LUPA_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$LUPA_USER"
  echo "Usuário $LUPA_USER criado."
else
  echo "Usuário $LUPA_USER já existe, pulando."
fi

echo "== Partição de acervo =="
if mountpoint -q "$ACERVO_MOUNT"; then
  echo "✅ $ACERVO_MOUNT já está montado."
else
  cat >&2 <<EOF
⚠️  $ACERVO_MOUNT não está montado.

Este script NÃO particiona nem formata disco — é passo manual, feito uma vez por
máquina (ver docs/gravacao-tv-radio/requisitos-hardware.md no monorepo Lupa). Resumo:

  1. lsblk                                   # identificar o disco certo — confira duas vezes
  2. sudo mkfs.ext4 -L acervo /dev/sdX1
  3. sudo mkdir -p $ACERVO_MOUNT
  4. sudo mount -o noatime /dev/sdX1 $ACERVO_MOUNT
  5. adicionar em /etc/fstab pra sobreviver a reboot

Rode este script de novo depois de montar.
EOF
  exit 1
fi

chown "$LUPA_USER":"$LUPA_USER" "$ACERVO_MOUNT"

echo "== OK. Base do sistema pronta. =="
