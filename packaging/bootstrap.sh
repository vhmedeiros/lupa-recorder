#!/bin/bash
# bootstrap.sh — prepara uma máquina Debian 12 pra rodar o lupa-recorder.
#
# Idempotente: rodar de novo numa máquina já preparada não deve quebrar nada.
#
# Deliberadamente NÃO particiona/formata disco nenhum. Formatar o disco errado numa
# máquina de terceiros é destrutivo e irreversível — isso fica um passo manual, documentado
# em docs/gravacao-tv-radio/requisitos-hardware.md (monorepo Lupa). Este script só confere
# se a montagem já existe e avisa se não existir.
#
# NÃO cobre DVB (driver DKMS, v4l-utils, apt-mark hold do kernel) — adiado com GRV-01.
#
# Uso:
#   sudo ./packaging/bootstrap.sh
#   sudo ACERVO_MOUNT=/mnt/acervo DATA_SUBDIR=lupa-recorder ./packaging/bootstrap.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACERVO_MOUNT="${ACERVO_MOUNT:-/mnt/acervo}"
DATA_SUBDIR="${DATA_SUBDIR:-lupa-recorder}"
DATA_ROOT="$ACERVO_MOUNT/$DATA_SUBDIR"
SYSTEM_ROOT="/var/lib/lupa-recorder"
OPT="/opt/lupa-recorder"
LUPA_USER="${LUPA_USER:-lupa}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Precisa rodar como root (sudo)." >&2
  exit 1
fi

echo "== Pacotes =="
apt-get update
apt-get install -y ffmpeg curl chrony python3-venv

echo "== yt-dlp (fontes protocol=youtube) =="
# O apt do Debian 12 traz um yt-dlp velho demais pro YouTube atual — usa o zipapp oficial
# (arch-independente: serve x86_64 e ARM64, só precisa do python3 que já instalamos).
if ! command -v yt-dlp >/dev/null 2>&1; then
  curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
  chmod a+rx /usr/local/bin/yt-dlp
  echo "yt-dlp instalado em /usr/local/bin/yt-dlp"
else
  echo "yt-dlp já no PATH, pulando."
fi
yt-dlp --version 2>/dev/null || echo "⚠️  yt-dlp não executou — confira se python3 está no PATH."

echo "== chrony (relógio — obrigatório, plano §1.8 / gap 2) =="
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

echo "== Diretórios =="
# data_root no HD (acervo), system_root no SSD (sistema), layout de release em /opt.
mkdir -p "$DATA_ROOT" "$SYSTEM_ROOT" "$OPT/releases"
chown -R "$LUPA_USER":"$LUPA_USER" "$DATA_ROOT" "$SYSTEM_ROOT"
echo "  data_root   = $DATA_ROOT"
echo "  system_root = $SYSTEM_ROOT"

echo "== journald (limite de log) =="
install -d /etc/systemd/journald.conf.d
install -m 0644 "$SCRIPT_DIR/journald-lupa-recorder.conf" /etc/systemd/journald.conf.d/lupa-recorder.conf
systemctl restart systemd-journald

echo "== Unidades systemd =="
for unit in lupa-recorder.service lupa-recorder-doctor.service lupa-recorder-doctor.timer; do
  install -m 0644 "$SCRIPT_DIR/$unit" "/etc/systemd/system/$unit"
done
install -m 0755 "$SCRIPT_DIR/install-release.sh" /usr/local/sbin/lupa-recorder-install-release
systemctl daemon-reload
systemctl enable --now lupa-recorder-doctor.timer

echo
echo "== Base pronta. Próximos passos (manuais, uma vez por máquina): =="
cat <<EOF

  1. Config:
       sudo cp $SCRIPT_DIR/../agent.toml.example    $SYSTEM_ROOT/agent.toml
       sudo cp $SCRIPT_DIR/../channels.yaml.example  $SYSTEM_ROOT/channels.yaml
       sudo -e $SYSTEM_ROOT/agent.toml     # gere um hmac_secret de verdade; data_root = $DATA_ROOT
       sudo -e $SYSTEM_ROOT/channels.yaml  # cadastre as fontes desta máquina
       sudo chown $LUPA_USER:$LUPA_USER $SYSTEM_ROOT/agent.toml $SYSTEM_ROOT/channels.yaml
       sudo chmod 600 $SYSTEM_ROOT/agent.toml    # tem segredo dentro

  2. Instalar o release e apontar o symlink 'current' (aceita wheel, sdist ou o dir do repo):
       sudo lupa-recorder-install-release 0.1.0 /caminho/pro/repo/lupa-recorder

  3. Conferir e ligar:
       sudo -u $LUPA_USER /opt/lupa-recorder/current/bin/lupa-recorder doctor
       sudo systemctl enable --now lupa-recorder
       journalctl -u lupa-recorder -f

  4. BIOS (manual, não scriptável): "Restore on AC Power Loss" = Power On
     (o PC volta sozinho depois de queda de energia). Ver requisitos-hardware.md.
EOF
