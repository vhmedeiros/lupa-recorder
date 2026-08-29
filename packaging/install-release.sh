#!/bin/bash
# install-release.sh — instala uma versão do lupa-recorder num layout versionado e
# troca o symlink `current` que o systemd aponta (sub-etapa 1.8).
#
#   /opt/lupa-recorder/releases/0.1.0/     <- venv com o pacote instalado
#   /opt/lupa-recorder/current -> releases/0.1.0
#
# Layout versionado desde já, mesmo sem auto-update — é o que evita retrofit doloroso
# na Fase 2.9. O auto-update da Fase 2.9 vai ser este script + um `pull` assinado.
#
# Uso:
#   sudo ./install-release.sh <versão> <caminho-do-pacote> [--restart]
#
#   <caminho-do-pacote>  wheel/sdist (dist/lupa_recorder-0.1.0-*.whl), diretório do
#                        repo (instala com `pip install .`), ou "." pra usar o cwd.
set -euo pipefail

OPT="/opt/lupa-recorder"

if [ "$(id -u)" -ne 0 ]; then
  echo "Precisa rodar como root (sudo)." >&2
  exit 1
fi
if [ "$#" -lt 2 ]; then
  echo "Uso: sudo $0 <versão> <caminho-do-pacote> [--restart]" >&2
  exit 2
fi

VERSAO="$1"
PACOTE="$2"
RESTART="${3:-}"
DESTINO="$OPT/releases/$VERSAO"

echo "== Instalando lupa-recorder $VERSAO em $DESTINO =="
mkdir -p "$OPT/releases"

if [ -d "$DESTINO" ]; then
  echo "  release $VERSAO já existe — recriando o venv."
  rm -rf "$DESTINO"
fi

python3 -m venv "$DESTINO"
"$DESTINO/bin/pip" install --upgrade pip >/dev/null
"$DESTINO/bin/pip" install "$PACOTE"

INSTALADA="$("$DESTINO/bin/lupa-recorder" --version | awk '{print $2}')"
echo "  versão instalada: $INSTALADA"
if [ "$INSTALADA" != "$VERSAO" ]; then
  echo "⚠️  a versão instalada ($INSTALADA) não bate com o argumento ($VERSAO)." >&2
  echo "    Symlink NÃO trocado. Confira o pacote." >&2
  exit 1
fi

# troca atômica do symlink (ln -sfn não deixa janela sem alvo)
ln -sfn "releases/$VERSAO" "$OPT/current"
echo "  $OPT/current -> releases/$VERSAO"

systemctl daemon-reload || true

if [ "$RESTART" = "--restart" ]; then
  if systemctl is-active --quiet lupa-recorder; then
    echo "== Reiniciando o serviço =="
    systemctl restart lupa-recorder
    sleep 2
    systemctl --no-pager --lines=0 status lupa-recorder || true
  else
    echo "  serviço não está ativo — nada a reiniciar (use: systemctl enable --now lupa-recorder)."
  fi
else
  echo "  serviço NÃO reiniciado (passe --restart pra reiniciar agora)."
fi

echo "== OK =="
