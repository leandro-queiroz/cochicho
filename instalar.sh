#!/usr/bin/env bash
# Instala o cochicho para o usuário atual: ambiente Python, serviço do systemd, atalho do GNOME.
# Pode rodar de novo: só refaz o que faltar e atualiza o serviço e o atalho.
# Atalho diferente: ATALHO='<Super>h' ./instalar.sh
set -euo pipefail

PASTA="$(cd "$(dirname "$0")" && pwd)"
VENV="$PASTA/.venv"
ATALHO="${ATALHO:-<Control><Shift>space}"

falta=()
for c in pw-record wl-copy uv gsettings; do command -v "$c" >/dev/null || falta+=("$c"); done
/usr/bin/python3 -c 'import gi' 2>/dev/null || falta+=("python3-gi")
if ((${#falta[@]})); then
    echo "Falta: ${falta[*]}  (Ubuntu: sudo apt install pipewire-bin wl-clipboard python3-gi; uv: https://docs.astral.sh/uv/)" >&2
    exit 1
fi

# Python do sistema com --system-site-packages: o servidor usa o PyGObject (gi) do Ubuntu para falar com o GNOME.
if [[ ! -x "$VENV/bin/python" ]]; then
    uv venv --python /usr/bin/python3 --system-site-packages "$VENV"
fi
uv pip install --python "$VENV/bin/python" --quiet -e "$PASTA"

mkdir -p ~/.config/cochicho ~/.config/systemd/user ~/.local/share/applications
[[ -f ~/.config/cochicho/config.toml ]] || cp "$PASTA/config.exemplo.toml" ~/.config/cochicho/config.toml

sed "s|@VENV@|$VENV|g" "$PASTA/instalacao/cochicho.service" > ~/.config/systemd/user/cochicho.service
sed "s|@VENV@|$VENV|g" "$PASTA/instalacao/cochicho.desktop" > ~/.local/share/applications/cochicho.desktop
systemctl --user daemon-reload
systemctl --user enable cochicho.service >/dev/null 2>&1
systemctl --user restart cochicho.service

# Atalho personalizado do GNOME: reaproveita o do cochicho se existir, senão cria um novo.
/usr/bin/python3 - "$VENV/bin/cochicho alternar" "$ATALHO" <<'PY'
import ast, subprocess, sys

comando, atalho = sys.argv[1], sys.argv[2]
LISTA = "org.gnome.settings-daemon.plugins.media-keys"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

def get(schema, chave):
    return subprocess.run(["gsettings", "get", schema, chave], capture_output=True, text=True, check=True).stdout.strip()

def setar(schema, chave, valor):
    subprocess.run(["gsettings", "set", schema, chave, valor], check=True)

bruto = get(LISTA, "custom-keybindings")
caminhos = [] if bruto.startswith("@as") else ast.literal_eval(bruto)
esquema = lambda c: f"{LISTA}.custom-keybinding:{c}"
alvo = None
for c in caminhos:
    nome = ast.literal_eval(get(esquema(c), "name"))
    cmd = ast.literal_eval(get(esquema(c), "command"))
    if nome == "Cochicho" or "cochicho alternar" in cmd:
        alvo = c
        break
if alvo is None:
    n = 0
    while f"{BASE}custom{n}/" in caminhos:
        n += 1
    alvo = f"{BASE}custom{n}/"
    setar(LISTA, "custom-keybindings", str(caminhos + [alvo]))
setar(esquema(alvo), "name", "'Cochicho'")
setar(esquema(alvo), "command", repr(comando))
setar(esquema(alvo), "binding", repr(atalho))
print(f"atalho {atalho} -> {comando} ({alvo})")
PY

echo "Pronto. Teste a colagem (pede a permissão do GNOME na primeira vez): $VENV/bin/cochicho testar-colar"
