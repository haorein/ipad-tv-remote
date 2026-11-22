"""Install the current user's desktop menu or login entry."""
import argparse
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def install_launcher(autostart=False):
    program_dir = Path.home() / ".local/share/ipad-tv-remote"
    if autostart:
        config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        dest = Path(config_home) / "autostart/ipad-tv-remote.desktop"
        action = "Name=启动客厅遥控板\nExec=/usr/bin/python3 ./session_start.py\n"
    else:
        data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
        dest = Path(data_home) / "applications/ipad-tv-remote-connect.desktop"
        action = ("Name=客厅遥控板 · 连接手机\nComment=显示手机或平板连接用的网址和二维码\n"
                  "Icon=phone\nCategories=Utility;\nExec=/usr/bin/python3 ./connection.py\n")
    dest.parent.mkdir(parents=True, exist_ok=True)
    marker = "X-IpadTVRemote-Managed=true"
    if dest.is_symlink():
        return

    # Path is a desktop-entry string. Exec runs in that directory with fixed arguments.
    working_dir = (str(program_dir).replace("\\", "\\\\").replace("\n", r"\n")
                   .replace("\r", r"\r").replace("\t", r"\t"))
    entry = ("[Desktop Entry]\nType=Application\nTerminal=false\n" + marker + "\n"
             + action + "Path=" + working_dir + "\n")
    if dest.exists():
        existing = dest.read_bytes()
        if marker.encode() not in existing.splitlines():
            return
        # Update a stock login entry from before the rename, preserving user edits.
        if autostart and existing != entry.replace("Name=启动客厅遥控板\n", "Name=启动电视遥控器\n", 1).encode():
            return
    file = NamedTemporaryFile(dir=str(dest.parent), mode="w", encoding="utf-8", delete=False)
    try:
        with file:
            file.write(entry)
        os.replace(file.name, dest)
    finally:
        if os.path.exists(file.name):
            os.unlink(file.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true")
    install_launcher(parser.parse_args().autostart)
