import contextlib
from configparser import ConfigParser
from io import StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import session_start
import setup_wizard
from server import load_config


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PASSWORD = "test-only setup passphrase"
REAL_RUN = subprocess.run
SERVICE_CHANGES = {"import-environment", "daemon-reload", "reset-failed", "restart", "start", "enable"}


class SetupTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        home = Path(workspace.name)
        self.config_home = home / "custom config"
        self.config_dir = self.config_home / "ipad-tv-remote"
        fake_bin = home / "bin"
        fake_bin.mkdir()
        for tool in ("xdotool", "xclip", "gio", "ip", "systemctl"):
            path = fake_bin / tool
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o700)
        environment = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_RUNTIME_DIR": str(home),
            "XDG_SESSION_TYPE": "x11",
            "DISPLAY": ":0",
            "PATH": str(fake_bin) + os.pathsep + os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        patcher = patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config_file = self.config_dir / "config.json"
        self.auth_file = self.config_dir / "auth.json"
        self.autostart_file = self.config_home / "autostart" / "ipad-tv-remote.desktop"
        self.graphical_target_active = True
        self.events = []
        self.output = StringIO()

    def run_command(self, command, **_kwargs):
        command = tuple(map(str, command))
        self.events.append(command)
        if command == ("systemctl", "--user", "is-active", "--quiet", "graphical-session.target"):
            return subprocess.CompletedProcess(command, int(not self.graphical_target_active), "", "")
        if command[:3] == ("systemctl", "--user", "is-failed"):
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[0] == "systemctl" or command == ("xdotool", "getmouselocation"):
            return subprocess.CompletedProcess(command, 0, "", "")
        install_files = command == ("sh", str(ROOT / "install.sh"), "--install")
        install_autostart = command == ("/usr/bin/python3", str(ROOT / "install_launcher.py"), "--autostart")
        if install_files or install_autostart:
            input_text = (FIXTURE_PASSWORD + "\n") * 2 if install_files and not self.auth_file.exists() else ""
            result = REAL_RUN(command, input=input_text, capture_output=True, text=True,
                              start_new_session=True, timeout=15)
            self.assertNotIn(FIXTURE_PASSWORD, result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result
        if Path(command[-1]).name == "connection.py":
            return subprocess.CompletedProcess(command, 0, "", "")
        self.fail("Unexpected setup command: {!r}".format(command))

    def run_setup(self, answers, *, missing=(), ready_error=None):
        answers = iter(answers)

        def reply(_prompt):
            answer = next(answers)
            self.events.append(("consent", answer))
            return answer

        def readiness(config):
            self.events.append(("readiness",))
            self.ready_port = config["port"]
            if ready_error is not None:
                raise ready_error

        with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output), \
                patch("builtins.input", side_effect=reply), \
                patch.object(setup_wizard.platform, "system", return_value="Linux"), \
                patch.object(setup_wizard.os, "getuid", return_value=1000), \
                patch.object(setup_wizard.os, "geteuid", return_value=1000), \
                patch.object(setup_wizard, "distro_family", return_value="debian"), \
                patch.object(setup_wizard, "missing_packages", return_value=list(missing)), \
                patch.object(setup_wizard, "wait_ready", side_effect=readiness), \
                patch.object(setup_wizard.subprocess, "run", side_effect=self.run_command):
            return setup_wizard.setup()

    def service_actions(self):
        return [event[2] for event in self.events if event[:2] == ("systemctl", "--user")]

    def test_decline_package_install_leaves_files_and_service_alone(self):
        with self.assertRaises(RuntimeError):
            self.run_setup([""], missing=("xdotool", "xclip"))
        self.assertIn("sudo apt", self.output.getvalue())
        self.assertFalse(self.config_dir.exists())
        self.assertTrue(SERVICE_CHANGES.isdisjoint(self.service_actions()))

    @unittest.skipUnless(os.geteuid() and Path("/usr/bin/python3").is_file(),
                         "file installation requires a non-root user and /usr/bin/python3")
    def test_decline_service_start_skips_systemd_changes_and_readiness(self):
        self.graphical_target_active = False
        with self.assertRaises(RuntimeError):
            self.run_setup(["n"])
        self.assertTrue(self.config_file.is_file())
        self.assertTrue(self.auth_file.is_file())
        self.assertTrue(SERVICE_CHANGES.isdisjoint(self.service_actions()))
        self.assertNotIn(("readiness",), self.events)
        self.assertFalse(self.autostart_file.exists())

    @unittest.skipUnless(os.geteuid() and Path("/usr/bin/python3").is_file(),
                         "file installation requires a non-root user and /usr/bin/python3")
    def test_readiness_failure_does_not_enable_service(self):
        self.graphical_target_active = False
        failure = RuntimeError("setup test health check failed")
        with self.assertRaisesRegex(RuntimeError, str(failure)):
            self.run_setup(["y"], ready_error=failure)
        self.assertIn("restart", self.service_actions())
        self.assertIn(("readiness",), self.events)
        self.assertNotIn("enable", self.service_actions())
        self.assertFalse(self.autostart_file.exists())

    @unittest.skipUnless(os.geteuid() and Path("/usr/bin/python3").is_file(),
                         "file installation requires a non-root user and /usr/bin/python3")
    def test_setup_preserves_config_and_auth_on_reinstall(self):
        self.run_setup(["y"])
        config = json.loads(self.config_file.read_text())
        config["port"] = 9876
        config["sites"][0]["name"] = "Customized YouTube"
        self.config_file.write_text(json.dumps(config, indent=2))
        original_config = self.config_file.read_bytes()
        original_auth = self.auth_file.read_bytes()
        self.events.clear()

        self.run_setup(["y"])

        self.assertEqual(self.config_file.read_bytes(), original_config)
        self.assertTrue(self.auth_file.read_bytes() == original_auth, "setup rewrote the stored credential")
        self.assertEqual(stat.S_IMODE(self.auth_file.stat().st_mode), 0o600)
        self.assertEqual(self.ready_port, 9876)
        self.assertFalse(self.autostart_file.exists())
        consent = self.events.index(("consent", "y"))
        ready = self.events.index(("readiness",))
        restart = next(index for index, command in enumerate(self.events)
                       if command[:3] == ("systemctl", "--user", "restart"))
        enable = next(index for index, command in enumerate(self.events)
                      if command[:3] == ("systemctl", "--user", "enable"))
        self.assertLess(consent, restart)
        self.assertLess(restart, ready)
        self.assertLess(ready, enable)

    @unittest.skipUnless(os.geteuid() and Path("/usr/bin/python3").is_file(),
                         "file installation requires a non-root user and /usr/bin/python3")
    def test_inactive_graphical_target_installs_login_only_fallback_after_enabling(self):
        self.graphical_target_active = False

        self.run_setup(["y"])

        self.assertTrue(self.autostart_file.is_file())
        entry = ConfigParser(interpolation=None)
        entry.read_string(self.autostart_file.read_text())
        desktop_entry = entry["Desktop Entry"]
        self.assertEqual(desktop_entry["Exec"], "/usr/bin/python3 ./session_start.py")
        working_dir = Path(desktop_entry["Path"])
        self.assertEqual(working_dir, Path.home() / ".local/share/ipad-tv-remote")
        self.assertTrue((working_dir / "session_start.py").is_file())
        consent = self.events.index(("consent", "y"))
        ready = self.events.index(("readiness",))
        enable = self.events.index(("systemctl", "--user", "enable", "ipad-tv-remote.service"))
        fallback = self.events.index(("/usr/bin/python3", str(ROOT / "install_launcher.py"), "--autostart"))
        self.assertLess(consent, ready)
        self.assertLess(ready, enable)
        self.assertLess(enable, fallback)

    @unittest.skipUnless(os.geteuid() and Path("/usr/bin/python3").is_file(),
                         "file installation requires a non-root user and /usr/bin/python3")
    def test_uninstall_preserves_preexisting_desktop_login_entry(self):
        self.graphical_target_active = False
        self.autostart_file.parent.mkdir(parents=True)
        custom = "[Desktop Entry]\nType=Application\nHidden=true\nName=My login entry\n"
        self.autostart_file.write_text(custom)
        self.run_setup(["y"])
        subprocess.run(["sh", str(ROOT / "uninstall.sh")], check=True, capture_output=True)
        self.assertEqual(self.autostart_file.read_text(), custom)
        menu = Path.home() / ".local/share/applications/ipad-tv-remote-connect.desktop"
        self.assertFalse(menu.exists())


class DesktopLoginTest(unittest.TestCase):
    def test_login_imports_session_environment_then_restarts_user_service(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        session_variables = {
            "x11": {"DISPLAY": ":4", "XAUTHORITY": home.name + "/.Xauthority"},
            "wayland": {"WAYLAND_DISPLAY": "wayland-2", "YDOTOOL_SOCKET": home.name + "/.ydotool_socket"},
        }
        for session, expected in session_variables.items():
            display = "DISPLAY" if session == "x11" else "WAYLAND_DISPLAY"
            env = {"HOME": home.name, "XDG_RUNTIME_DIR": home.name,
                   display: expected[display], "SSH_AUTH_SOCK": "/unrelated"}
            with self.subTest(session=session), patch.dict(os.environ, env, clear=True), \
                    patch.object(session_start.os, "getuid", return_value=1000), \
                    patch.object(session_start.os, "geteuid", return_value=1000), \
                    patch.object(session_start.subprocess, "run") as run:
                self.assertEqual(session_start.main(), 0)
                self.assertEqual(run.call_count, 2)
                import_args, import_options = run.call_args_list[0]
                self.assertEqual(import_args[0][:3], ["systemctl", "--user", "import-environment"])
                imported = {key: import_options["env"][key] for key in import_args[0][3:]}
                self.assertEqual(imported, dict(expected, XDG_SESSION_TYPE=session, XDG_RUNTIME_DIR=home.name,
                                                XDG_CONFIG_HOME=home.name + "/.config"))
                self.assertEqual(run.call_args_list[1][0][0],
                                 ["systemctl", "--user", "restart", "ipad-tv-remote.service"])


class ReadinessTest(unittest.TestCase):
    def test_probe_requires_login_protection_and_expected_page(self):
        config = load_config(ROOT / "config.example.json")
        expected_page = (ROOT / "static" / "index.html").read_bytes()
        cases = [
            ("wrong page", 401, 200, b"Different local service", False),
            ("unprotected API", 200, 200, expected_page, False),
            ("unavailable page", 401, 503, expected_page, False),
            ("ready", 401, 200, expected_page, True),
        ]
        for name, health_code, page_code, page_bytes, ready in cases:
            health = Mock(status=health_code)
            health.read.return_value = json.dumps({"error": "请登录"}).encode()
            page = Mock(status=page_code)
            page.read.return_value = page_bytes
            connection = Mock()
            connection.getresponse.side_effect = [health, page]
            with self.subTest(case=name), \
                    patch.object(setup_wizard.http.client, "HTTPConnection", return_value=connection), \
                    patch.object(setup_wizard, "time") as clock:
                clock.monotonic.side_effect = [0, 0, float("inf")]
                if ready:
                    setup_wizard.wait_ready(config)
                    self.assertEqual(connection.request.call_args_list,
                                     [call("GET", "/api/health"), call("GET", "/")])
                else:
                    with self.assertRaises(RuntimeError):
                        setup_wizard.wait_ready(config)


class DependencyTest(unittest.TestCase):
    def test_debian_installs_only_missing_packages_after_confirmation(self):
        available = {"ip", "qrencode", "wpctl", "xsel", "sudo", "apt-get"}
        approved = []
        output = StringIO()
        expected = ["sudo", "apt-get", "install", "libglib2.0-bin", "xdotool"]

        def consent(_prompt):
            self.assertIn(" ".join(expected), output.getvalue())
            approved.append(True)
            return "y"

        def install(command, *, check):
            self.assertTrue(approved)
            self.assertTrue(check)
            self.assertEqual(command, expected)
            available.update(("gio", "xdotool"))
            return subprocess.CompletedProcess(command, 0)

        with contextlib.redirect_stdout(output), \
                patch.object(setup_wizard, "distro_family", return_value="debian"), \
                patch.object(setup_wizard.shutil, "which", side_effect=lambda tool: tool if tool in available else None), \
                patch("builtins.input", side_effect=consent), \
                patch.object(setup_wizard.subprocess, "run", side_effect=install) as run:
            setup_wizard.install_dependencies("x11")
            run.assert_called_once()
