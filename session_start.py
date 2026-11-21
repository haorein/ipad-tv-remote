"""Import a graphical login's environment before starting the user service."""
import os
from pathlib import Path
import subprocess
import sys

from desktop import resolve_backend


UNIT = "ipad-tv-remote.service"


def import_environment():
    env = os.environ.copy()
    session = resolve_backend("auto")
    env["XDG_SESSION_TYPE"] = session
    env["XDG_CONFIG_HOME"] = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    if session == "x11" and not env.get("XAUTHORITY"):
        # Match Xlib's normal fallback even when a previous login left an authority path in systemd.
        env["XAUTHORITY"] = str(Path.home() / ".Xauthority")
    if session == "wayland" and not env.get("YDOTOOL_SOCKET"):
        env["YDOTOOL_SOCKET"] = str(Path(env.get("XDG_RUNTIME_DIR") or "/tmp") / ".ydotool_socket")
    names = [name for name in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE",
                               "XDG_RUNTIME_DIR", "YDOTOOL_SOCKET", "XDG_CONFIG_HOME")
             if env.get(name)]
    subprocess.run(["systemctl", "--user", "import-environment", *names], env=env, timeout=8, check=True)


def main():
    if os.geteuid() == 0 or os.getuid() == 0 or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return 1
    try:
        import_environment()
        # A service surviving logout must pick up this login's display and credentials.
        subprocess.run(["systemctl", "--user", "restart", UNIT], check=True, timeout=40)
    except (OSError, subprocess.SubprocessError) as error:
        print("无法在图形登录时启动电视遥控服务。{}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
