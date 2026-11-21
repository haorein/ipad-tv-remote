#!/bin/sh
set -eu
if [ "$(id -u)" -eq 0 ]; then
    echo '请使用安装服务的图形桌面普通用户运行。' >&2
    exit 1
fi
data_dir="$HOME/.local/share/ipad-tv-remote"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ipad-tv-remote.service"
if [ -e "$unit" ]; then
    systemctl --user disable --now ipad-tv-remote.service
    rm -f -- "$unit"
    systemctl --user daemon-reload
fi
rm -f -- "$data_dir/server.py" "$data_dir/desktop.py" \
    "$data_dir/static/index.html" "$data_dir/static/style.css" "$data_dir/static/app.js"
if [ -d "$data_dir/__pycache__" ]; then
    find "$data_dir/__pycache__" -maxdepth 1 -type f \( -name 'server.cpython-*.pyc' -o -name 'desktop.cpython-*.pyc' \) -delete
fi
for directory in "$data_dir/static" "$data_dir/__pycache__" "$data_dir"; do
    if [ -d "$directory" ]; then rmdir -- "$directory" 2>/dev/null || :; fi
done
echo '已卸载服务和程序，保留用户配置及口令。'
