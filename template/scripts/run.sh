#!/bin/sh
# POSIX entry: no Python, activation, global installation or shell profile required.
set -eu
blocked() { echo "bootstrap blocked: $*" >&2; exit 2; }
[ ! -L "$0" ] || blocked 'launcher must not be a symlink'
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
lock="$root/scripts/bootstrap.lock"
value() { awk -F '\t' -v key="$1" '$1 == key { print $2 }' "$lock"; }
setup=false
[ "$#" -ne 1 ] || [ "${1:-}" != setup ] || setup=true
case "$(uname -s)" in Linux) os=linux ;; Darwin) os=macos ;; *) blocked 'unsupported OS; native Windows uses scripts/run.ps1' ;; esac
case "$(uname -m)" in x86_64|amd64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; *) blocked 'unsupported architecture' ;; esac
row=$(awk -F '\t' -v key="$os-$arch" '$1 == key {print $2 " " $3 " " $4}' "$lock")
[ -n "$row" ] || blocked 'unsupported OS/architecture'
# These three archive fields are fixed manifest identifiers, with no spaces.
archive=$(printf '%s\n' "$row" | awk '{print $1}')
expected=$(printf '%s\n' "$row" | awk '{print $2}')
entry=$(printf '%s\n' "$row" | awk '{print $3}')
runtime="$root/.local/runtime"
for path in "$root/.local" "$runtime" "$runtime/owner" "$runtime/uv" "$runtime/$archive" "$runtime/python" "$runtime/cache" "$runtime/bin" "$root/.venv"; do
    [ ! -L "$path" ] || blocked "path alias forbidden: $path"
done
if [ -d "$runtime" ]; then
    [ -f "$runtime/owner" ] || blocked 'foreign runtime directory; preserve it and use a fresh checkout'
    [ "$(cat "$runtime/owner")" = "$root" ] || blocked 'runtime was relocated; use a fresh checkout'
elif "$setup"; then
    [ ! -e "$root/.venv" ] || blocked 'foreign .venv; preserve it and use a fresh checkout'
    mkdir -p "$runtime"
    printf '%s\n' "$root" > "$runtime/owner"
else
    blocked 'environment is not ready; run scripts/run.sh setup'
fi
# Clean inherited redirect/config variables before even the first uv Python operation.
for name in $(env | awk -F= '{print $1}' | LC_ALL=C awk '/^(UV_|PYTHON|PIP_)[A-Za-z0-9_]*$/ || /^(VIRTUAL_ENV|CONDA_PREFIX|CONDA_DEFAULT_ENV|__PYVENV_LAUNCHER__)$/'); do
    unset "$name"
done
export UV_PYTHON_INSTALL_DIR="$runtime/python" UV_CACHE_DIR="$runtime/cache" UV_PYTHON_BIN_DIR="$runtime/bin"
export UV_NO_CONFIG=1 UV_NO_ENV_FILE=1 UV_HTTP_TIMEOUT=60 UV_HTTP_RETRIES=0 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
hash() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
    else blocked 'SHA256 utility missing (sha256sum or shasum)'; fi
}
# Check the parent's current job table before every signal. A completed job's
# numeric PID may have been reused, so kill -0 alone does not establish ownership.
owned_job() {
    LC_ALL=C jobs -l > "$temp/jobs.$1"
    owned_job_value=$(awk -v pid="$1" 'index($1,"[")==1 && /Running|Stopped/ {for(i=2;i<=NF;i++) if($i==pid){gsub(/[^0-9]/,"",$1); print "%" $1; exit}}' "$temp/jobs.$1")
}
signal_owned() {
    owned_job "$2"
    [ -z "$owned_job_value" ] || kill -"$1" "$2" 2>/dev/null || :
}
bounded() {
    "$@" & child=$!
    timed_out=false
    trap 'timed_out=true
          signal_owned TERM "$child"
          sleep 1
          signal_owned KILL "$child"' USR1
    (trap 'signal_owned TERM "$sleeper"; wait "$sleeper" 2>/dev/null || :; exit 0' TERM HUP INT
     sleep "$(value TIMEOUT_SECONDS)" & sleeper=$!
     wait "$sleeper" || exit 0
     kill -USR1 "$$" 2>/dev/null || :) & timer=$!
    code=0; wait "$child" || code=$?
    if "$timed_out"; then
        wait "$child" 2>/dev/null || :
    fi
    signal_owned TERM "$timer"
    wait "$timer" 2>/dev/null || :
    trap - USR1
    if "$timed_out"; then blocked "runtime command timed out; owned child was terminated and reaped"; fi
    [ "$code" -eq 0 ] || blocked "runtime command failed (exit $code)"
}

if [ ! -f "$runtime/$archive" ]; then
    "$setup" || blocked 'pinned uv archive missing; run explicit setup'
    command -v curl >/dev/null 2>&1 || blocked 'curl is required for the first setup'
    temp=$(mktemp -d "$runtime/.fetch-XXXXXXXX")
    trap 'rm -rf -- "$temp"' EXIT HUP INT TERM
    curl --disable --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --max-filesize "$(value MAX_ARCHIVE_BYTES)" \
        "https://github.com/astral-sh/uv/releases/download/$(value UV_VERSION)/$archive" \
        --output "$temp/archive" || blocked 'download failed/offline; explicit setup can be retried'
    [ "$(hash "$temp/archive")" = "$expected" ] || blocked 'uv archive SHA256 mismatch; downloaded bytes were not executed'
    mv "$temp/archive" "$runtime/$archive"
fi
[ "$(wc -c < "$runtime/$archive")" -le "$(value MAX_ARCHIVE_BYTES)" ] || blocked 'archive size limit exceeded'
[ "$(hash "$runtime/$archive")" = "$expected" ] || blocked 'cached uv archive SHA256 mismatch; preserve it and inspect before retry'
# Read only one exact entry to stdout. Never extract archive paths to the filesystem.
[ "$(tar -tzf "$runtime/$archive" | awk -v entry="$entry" '$0 == entry {n++} END {print n+0}')" = 1 ] || blocked 'uv archive entry is absent or duplicated'
temp=${temp:-$(mktemp -d "$runtime/.fetch-XXXXXXXX")}
trap 'rm -rf -- "$temp"' EXIT HUP INT TERM
tar -xOzf "$runtime/$archive" "$entry" > "$temp/uv" || blocked 'cannot read pinned uv executable'
if [ -f "$runtime/uv" ]; then
    [ "$(hash "$runtime/uv")" = "$(hash "$temp/uv")" ] || blocked 'uv executable differs from pinned archive'
else
    "$setup" || blocked 'uv executable missing; run explicit setup'
    chmod 700 "$temp/uv"
    mv "$temp/uv" "$runtime/uv"
fi
if "$setup"; then
    command -v git >/dev/null 2>&1 || blocked 'Git 2.43+ required; agent should use native OS installer within existing rights: https://git-scm.com/downloads'
    bounded "$runtime/uv" --no-config python install "$(value PYTHON_REQUEST)" --install-dir "$runtime/python" --no-bin --no-registry
fi
bounded "$runtime/uv" --no-config python find --offline --system --managed-python --no-project --no-python-downloads "$(value PYTHON_REQUEST)" > "$temp/python"
python=$(cat "$temp/python")
case "$python" in "$runtime/python/"*) ;; *) blocked 'managed Python points outside the project runtime' ;; esac
[ -x "$python" ] || blocked 'managed Python missing; run explicit setup'
rm -rf -- "$temp"
trap - EXIT HUP INT TERM
exec "$python" -I "$root/scripts/bootstrap.py" "$@"
