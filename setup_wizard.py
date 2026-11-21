"""Interactive setup from a Linux graphical desktop terminal."""
import http.client
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

from connection import discover_addresses
from desktop import VOLUME, resolve_backend
from server import allowed_source, load_config
from session_start import UNIT, import_environment


SOURCE = Path(__file__).resolve().parent
MANAGERS = {"debian": ["apt-get", "install"], "fedora": ["dnf", "install"],
            "arch": ["pacman", "-S", "--needed"]}
PACKAGE_NAMES = {
    "debian": {"gio": "libglib2.0-bin", "ip": "iproute2", "pactl": "pulseaudio-utils"},
    "fedora": {"gio": "glib2", "ip": "iproute", "pactl": "pulseaudio-utils"},
    "arch": {"gio": "glib2", "ip": "iproute2", "pactl": "libpulse"},
}
LOG_HINT = "请用 journalctl --user -u ipad-tv-remote.service -n 30 --no-pager 查看服务日志。"


def distro_family():
    try:
        lines = Path("/etc/os-release").read_text().splitlines()
    except OSError:
        return None
    fields = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    for name in ("ID", "ID_LIKE"):
        for token in shlex.split(fields.get(name, "")):
            for family in token.split():
                if family in ("debian", "ubuntu"):
                    return "debian"
                if family in ("fedora", "arch"):
                    return family
    return None


def missing_packages(backend, family):
    groups = [("gio",), ("ip",), ("qrencode",)]
    if backend == "x11":
        groups.extend([("xdotool",), ("xclip", "xsel")])
    else:
        groups.extend([("ydotool",), ("wl-copy",)])
    if not any(shutil.which(name) for name in VOLUME):
        groups.append(("pactl",))
    names = {"wl-copy": "wl-clipboard"}
    names.update(PACKAGE_NAMES.get(family, {}))
    return [names.get(group[0], group[0])
            for group in groups if not any(shutil.which(tool) for tool in group)]


def confirm(prompt):
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes", "是")
    except EOFError:
        return False


def install_dependencies(backend):
    family = distro_family()
    packages = missing_packages(backend, family)
    if not packages:
        return
    if not family:
        raise RuntimeError("请用发行版的包管理器安装所缺工具，再运行向导：" + "、".join(packages))
    args = ["sudo"] + MANAGERS[family] + packages
    print("为桌面控制和扫码连接补齐所缺的软件包，将运行 " + " ".join(args), flush=True)
    if not confirm("安装这些系统软件包？"):
        raise RuntimeError("已取消。可以自行安装所列软件包后重新运行向导。")
    if not shutil.which("sudo") or not shutil.which(args[1]):
        raise RuntimeError("请管理员运行所列安装命令，再重新运行向导。")
    # The package manager interacts with the terminal for privileges and package decisions.
    subprocess.run(args, check=True)
    remaining = missing_packages(backend, family)
    if remaining:
        raise RuntimeError("安装后仍缺少依赖：" + "、".join(remaining))


def systemctl(*args, check=True, quiet=False):
    return subprocess.run(["systemctl", "--user", *args], check=check, timeout=40,
                          stdout=subprocess.DEVNULL if quiet else None)


def check_wayland():
    hint = ("请按 README 的 Wayland 章节配置 ydotool 1.x 和 ydotoold 专用 socket，"
            "在此终端设置实际的 YDOTOOL_SOCKET 后重试，或登录 X11 桌面。")
    # A persistent unit override takes precedence over the imported manager environment.
    override = subprocess.run(["systemctl", "--user", "show", "--property=Environment", "--value", UNIT],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
    unit_socket = None
    if override.returncode == 0:
        for assignment in shlex.split(override.stdout):
            if assignment.startswith("YDOTOOL_SOCKET="):
                unit_socket = assignment.partition("=")[2]
    default = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / ".ydotool_socket"
    sock = Path(unit_socket or os.environ.get("YDOTOOL_SOCKET") or default)
    try:
        mode = sock.stat().st_mode
    except OSError as error:
        raise RuntimeError(hint) from error
    if not sock.is_absolute() or not stat.S_ISSOCK(mode) or mode & stat.S_IWOTH or not os.access(sock, os.W_OK):
        raise RuntimeError("ydotool socket 需要限制为桌面用户或专用组可写。" + hint)
    os.environ["YDOTOOL_SOCKET"] = str(sock)
    result = subprocess.run(["ydotool", "mousemove", "--help"], timeout=5,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode or b"--wheel" not in result.stdout:
        raise RuntimeError(hint)
    if sock != default and not unit_socket:
        print("若 YDOTOOL_SOCKET 仅在当前终端设置，请按 README 的 Wayland 章节将它写入用户服务覆盖文件，以便下次登录。")


def wait_ready(config):
    host = config["bind"]
    if host == "0.0.0.0":
        host = "127.0.0.1"
        if not allowed_source(host, config):
            for endpoint in discover_addresses(config):
                candidate = urlsplit(endpoint.url).hostname
                if allowed_source(candidate, config):
                    host = candidate
                    break
    expected_page = (SOURCE / "static" / "index.html").read_bytes()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        # Binding the local end also prevents probing a stale address now owned by another device.
        conn = http.client.HTTPConnection(host, config["port"], timeout=.75, source_address=(host, 0))
        try:
            conn.request("GET", "/api/health")
            response = conn.getresponse()
            message = json.loads(response.read(256))
            if (not allowed_source(host, config) and response.status == 403
                    and message == {"error": "来源或主机地址未获允许"}):
                print("服务已响应。本机不在允许网段内，请从获准的手机或平板确认登录。")
                return
            if response.status == 401 and message == {"error": "请登录"}:
                conn.close()
                conn.request("GET", "/")
                page = conn.getresponse()
                if page.status == 200 and page.read(len(expected_page) + 1) == expected_page:
                    return
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            conn.close()
        time.sleep(.25)
    raise RuntimeError("未能验证遥控页面和登录保护。请核对绑定地址及端口是否被占用。" + LOG_HINT)


def start_service(config):
    desktop_login = systemctl("is-active", "--quiet", "graphical-session.target", check=False).returncode != 0
    print("将向 systemd 用户管理器导入当前桌面环境，重新加载服务，启动或重启遥控服务，"
          "并在验证通过后启用图形登录时自启。已有连接需重新登录。", flush=True)
    if desktop_login:
        print("当前桌面未激活 graphical-session.target，向导也会添加当前用户的桌面登录自启项。")
    if not confirm("现在执行以上用户服务操作？"):
        raise RuntimeError("已保留安装文件。准备好启动时可再次运行 sh install.sh --setup。")
    import_environment()
    systemctl("daemon-reload")
    previously_running = systemctl("is-active", "--quiet", UNIT, check=False).returncode == 0
    if systemctl("is-failed", "--quiet", UNIT, check=False).returncode == 0:
        systemctl("reset-failed", UNIT)
    try:
        systemctl("restart", UNIT)
        wait_ready(config)
        if systemctl("is-active", "--quiet", UNIT, check=False).returncode:
            raise RuntimeError("遥控用户服务未进入运行状态。" + LOG_HINT)
    except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt):
        if not previously_running:
            systemctl("stop", UNIT, check=False)
        raise
    systemctl("enable", UNIT)
    if desktop_login:
        subprocess.run(["/usr/bin/python3", str(SOURCE / "install_launcher.py"), "--autostart"],
                       check=True, timeout=8)


def setup():
    if platform.system() != "Linux" or os.geteuid() == 0 or os.getuid() == 0:
        raise RuntimeError("请在连接电视的 Linux 电脑上，以普通用户从图形桌面终端运行向导。")
    app_config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ipad-tv-remote"
    existing = app_config / "config.json"
    try:
        config = load_config(existing if existing.exists() else SOURCE / "config.example.json")
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as error:
        raise RuntimeError("请检查 config.json 的内容和访问权限，再运行向导。") from error
    backend = resolve_backend(config["backend"])
    display = "WAYLAND_DISPLAY" if backend == "wayland" else "DISPLAY"
    if not os.environ.get(display):
        raise RuntimeError("缺少 {}。请从 Linux 电脑的图形桌面终端运行向导。".format(display))
    if not shutil.which("systemctl") or systemctl("show", "--property=Version", quiet=True,
                                                  check=False).returncode:
        raise RuntimeError("无法连接 systemd 用户管理器。请登录 Linux 图形桌面并检查 systemctl --user status。")
    print("检测到 {} 桌面，正在检查遥控所需的工具。".format(backend), flush=True)
    install_dependencies(backend)
    if backend == "wayland":
        check_wayland()
    elif subprocess.run(["xdotool", "getmouselocation"], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=4).returncode:
        raise RuntimeError("无法访问 X11 桌面。请从图形桌面终端运行，并检查 DISPLAY 和 XAUTHORITY。")
    subprocess.run(["sh", str(SOURCE / "install.sh"), "--install"], check=True)
    start_service(config)
    connection = Path.home() / ".local/share/ipad-tv-remote/connection.py"
    return subprocess.run(["/usr/bin/python3", str(connection)], timeout=60).returncode


def main():
    try:
        return setup()
    except KeyboardInterrupt:
        print("\n已取消向导。", file=sys.stderr)
    except subprocess.CalledProcessError as error:
        print("命令执行失败：{}（退出码 {}）。".format(" ".join(error.cmd), error.returncode), file=sys.stderr)
        if error.cmd[0] == "systemctl":
            print(LOG_HINT, file=sys.stderr)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("配置未完成。{}".format(error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
