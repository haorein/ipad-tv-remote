#!/bin/sh
set -eu
if [ "$(id -u)" -eq 0 ]; then
    echo '请使用安装服务的图形桌面普通用户运行。' >&2
    exit 1
fi
data_dir="$HOME/.local/share/ipad-tv-remote"
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/ipad-tv-remote"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ipad-tv-remote.service"
if [ -e "$unit" ]; then
    systemctl --user disable --now ipad-tv-remote.service
    rm -f -- "$unit"
    systemctl --user daemon-reload
fi
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/ipad-tv-remote.desktop"
for entry in "$applications/ipad-tv-remote-connect.desktop" "$autostart"; do
    if [ -f "$entry" ] && [ ! -L "$entry" ] && grep -Fqx 'X-IpadTVRemote-Managed=true' "$entry"; then
        rm -f -- "$entry"
    fi
done
rm -f -- "$cache_dir/connection.html"
rm -f -- "$data_dir/server.py" "$data_dir/desktop.py" "$data_dir/connection.py" "$data_dir/session_start.py" \
    "$data_dir/static/index.html" "$data_dir/static/style.css" "$data_dir/static/app.js"
if [ -d "$data_dir/__pycache__" ]; then
    find "$data_dir/__pycache__" -maxdepth 1 -type f \( -name 'server.cpython-*.pyc' -o -name 'desktop.cpython-*.pyc' -o -name 'connection.cpython-*.pyc' -o -name 'session_start.cpython-*.pyc' \) -delete
fi
for directory in "$data_dir/static" "$data_dir/__pycache__" "$data_dir" "$cache_dir"; do
    if [ -d "$directory" ]; then rmdir -- "$directory" 2>/dev/null || :; fi
done
echo '已卸载服务和程序，保留用户配置及口令。'
