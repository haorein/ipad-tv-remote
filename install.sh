#!/bin/sh
set -eu

src=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
data_dir="$HOME/.local/share/ipad-tv-remote"
user_config="${XDG_CONFIG_HOME:-$HOME/.config}"
app_config="$user_config/ipad-tv-remote"
unit_dir="$user_config/systemd/user"

if [ "$(id -u)" -eq 0 ]; then
    echo '请使用图形桌面普通用户运行。' >&2
    exit 1
fi

require_python() {
    /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 7))' ||
        { echo '需要 /usr/bin/python3（3.7 或更新）。请先安装系统 Python（Debian/Ubuntu/Fedora 为 python3，Arch 为 python）。' >&2; return 1; }
}

bootstrap_python() (
    [ -x /usr/bin/python3 ] && return 0
    if [ "$(uname -s)" != Linux ] || [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        echo '请在 Linux 电脑的图形桌面终端运行向导。' >&2
        return 1
    fi
    if [ -r /etc/os-release ]; then . /etc/os-release; fi
    case " ${ID:-} ${ID_LIKE:-} " in
        *' debian '*|*' ubuntu '*) set -- apt-get install python3 ;;
        *' fedora '*) set -- dnf install python3 ;;
        *' arch '*) set -- pacman -S --needed python ;;
        *) echo '请先用发行版的包管理器安装 /usr/bin/python3，再运行向导。' >&2; return 1 ;;
    esac
    echo "向导需要系统 Python，将运行 sudo $*"
    printf '%s' '安装系统 Python？ [y/N] '
    read -r answer || return 1
    case "$answer" in y|Y|yes|YES|是) sudo "$@" ;; *) echo '已取消。'; return 1 ;; esac
)

check() {
    require_python
    /usr/bin/python3 - "$src" "$app_config" <<'PY'
from pathlib import Path
import shutil
import sys
sys.path.insert(0, sys.argv[1])
from desktop import Desktop, VOLUME
from server import load_config

config = Path(sys.argv[2]) / "config.json"
try:
    source = config if config.exists() else Path(sys.argv[1]) / "config.example.json"
    desktop = Desktop(load_config(source)["backend"])
    if not shutil.which("gio"):
        raise ValueError("Install gio for browser launch")
except (OSError, ValueError, TypeError, KeyError) as error:
    sys.exit(str(error))
audio = ", ".join(tool for tool in VOLUME if shutil.which(tool))
print("Input: {} / {}. Audio commands: {}. Media: {}.".format(
    desktop.input, desktop.clipboard, audio or "none",
    "playerctl + media key" if shutil.which("playerctl") else "media key"))
PY
}

case "${1:---check}" in
    --check) check ;;
    --setup)
        bootstrap_python
        require_python
        exec /usr/bin/python3 "$src/setup_wizard.py"
        ;;
    --install)
        check
        umask 077
        mkdir -p "$data_dir/static" "$app_config" "$unit_dir"
        install -m 644 "$src/server.py" "$src/desktop.py" "$src/connection.py" "$src/session_start.py" "$data_dir/"
        install -m 644 "$src/static/index.html" "$src/static/style.css" "$src/static/app.js" "$data_dir/static/"
        if [ ! -e "$app_config/config.json" ]; then
            install -m 600 "$src/config.example.json" "$app_config/config.json"
        fi
        install -m 644 "$src/systemd/ipad-tv-remote.service" "$unit_dir/"
        if [ ! -e "$app_config/auth.json" ]; then
            /usr/bin/python3 "$data_dir/server.py" --config-dir "$app_config" --init-password
        fi
        /usr/bin/python3 "$src/install_launcher.py"
        echo '安装文件已就绪。'
        ;;
    *) echo '用法：sh install.sh [--setup|--check|--install]' >&2; exit 2 ;;
esac
