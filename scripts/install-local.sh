#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="$project_root/.venv/bin/python"
bin_dir="${CAPIVARA2_BIN_DIR:-$HOME/.local/bin}"
launcher="$bin_dir/capivara2"

if [[ -e "$launcher" || -L "$launcher" ]]; then
    if [[ ! -L "$launcher" || "$(readlink -- "$launcher")" != "$project_root/.venv/bin/capivara2" ]]; then
        printf 'Já existe outro comando em %s. Defina CAPIVARA2_BIN_DIR para instalar em outra pasta.\n' "$launcher" >&2
        exit 1
    fi
fi

if [[ ! -x "$venv_python" ]]; then
    python3 -m venv --without-pip "$project_root/.venv"
fi

if "$venv_python" -c 'import pip' >/dev/null 2>&1; then
    "$venv_python" -m pip install --disable-pip-version-check --editable "$project_root"
else
    # Permite instalar também quando o Python do sistema não inclui ensurepip.
    pip_archive="$(mktemp "${TMPDIR:-/tmp}/capivara-pip.XXXXXX.pyz")"
    trap 'rm -f -- "$pip_archive"' EXIT
    "$venv_python" - "$pip_archive" <<'PY'
import sys
import urllib.request

urllib.request.urlretrieve("https://bootstrap.pypa.io/pip/pip.pyz", sys.argv[1])
PY
    "$venv_python" "$pip_archive" install --disable-pip-version-check --editable "$project_root"
fi

mkdir -p -- "$bin_dir"
ln -sfn -- "$project_root/.venv/bin/capivara2" "$launcher"
# Remove somente o atalho antigo criado por este projeto.
legacy_launcher="$bin_dir/harness"
if [[ -L "$legacy_launcher" && "$(readlink -- "$legacy_launcher")" == "$project_root/.venv/bin/harness" ]]; then
    rm -- "$legacy_launcher"
fi
"$launcher" --help >/dev/null
printf '\nInstalação local concluída: %s\nExecute: capivara2\n' "$launcher"
