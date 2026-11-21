import http.client
import ipaddress
import json
from pathlib import Path
from io import BytesIO
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from desktop import Desktop, KEYS
from server import Auth, Handler, Input, derive, load_config

ROOT = Path(__file__).resolve().parents[1]
HOST = "192.168.1.8:8765"
PASSWORD = "test-only example passphrase"


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "auth.json"
        salt = b"salt for tests!!"
        path.write_text(json.dumps({"salt": salt.hex(), "hash": derive(PASSWORD, salt).hex(),
                                    "signing": "ab" * 32}))
        path.chmod(0o600)
        config = load_config(ROOT / "config.example.json")
        desktop = Mock(spec=Desktop)
        desktop.backend = "x11"
        inputs = Input(desktop)
        self.addCleanup(inputs.close)
        self.server = SimpleNamespace(config=config, auth=Auth(path), inputs=inputs,
                                      sites={"youtube": "https://www.youtube.com/"})

    def call(self, path, data=None, *, host=HOST, origin="http://" + HOST,
             ip="192.168.1.10", cookie=None, csrf=True, referer=None, body=None):
        headers = ["Host: " + host]
        if cookie:
            headers.append("Cookie: " + cookie)
        if origin is not None:
            headers.append("Origin: " + origin)
        if referer:
            headers.append("Referer: " + referer)
        if csrf:
            headers.append("X-Remote-Request: 1")
        post = data is not None or body is not None
        if post:
            body = json.dumps(data).encode() if body is None else body
            headers.extend(["Content-Type: application/json", "Content-Length: " + str(len(body))])
        method = "POST" if post else "GET"
        raw = ("{} {} HTTP/1.1\r\n{}\r\n\r\n".format(
            method, path, "\r\n".join(headers))).encode("ascii") + (body or b"")
        output = BytesIO()
        request = SimpleNamespace(settimeout=lambda _: None, makefile=lambda *_: BytesIO(raw),
                                  sendall=output.write)
        Handler(request, (ip, 40000), self.server)
        response = http.client.HTTPResponse(SimpleNamespace(
            makefile=lambda *_: BytesIO(output.getvalue())))
        response.begin()
        try:
            return response.status, response.getheader("Set-Cookie"), response.read()
        finally:
            response.close()

    def login(self):
        code, cookie, _ = self.call("/api/login", {"password": PASSWORD})
        self.assertEqual(code, 200)
        for part in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age="):
            self.assertIn(part, cookie)
        return cookie.split(";")[0]

    def test_auth_cookie_logout_and_expiry(self):
        self.assertEqual(self.call("/api/config")[0], 401)
        self.assertEqual(self.call("/api/login", {"password": "wrong"})[0], 401)
        cookie = self.login()
        self.assertEqual(self.call("/api/config", cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/key", {"id": "enter"}, cookie=cookie + "0")[0], 401)
        with patch("server.time.time", return_value=time.time() + 86400):
            self.assertEqual(self.call("/api/config", cookie=cookie)[0], 401)
        self.assertEqual(self.call("/api/logout", {}, cookie=cookie)[0], 200)
        self.assertEqual(self.call("/api/config", cookie=cookie)[0], 401)

    def test_network_host_and_origin(self):
        self.assertEqual(self.call("/")[0], 200)
        self.assertEqual(self.call("/", ip="8.8.8.8")[0], 403)
        self.assertEqual(self.call("/", host="untrusted.example:8765")[0], 403)
        self.server.config["networks"] = [ipaddress.ip_network("192.168.1.0/24")]
        self.assertEqual(self.call("/", ip="192.168.2.10")[0], 403)
        form = {"password": PASSWORD}
        self.assertEqual(self.call("/api/login", form, origin="http://example.test")[0], 403)
        self.assertEqual(self.call("/api/login", form, csrf=False)[0], 403)
        self.assertEqual(self.call("/api/login", form, origin=None)[0], 403)
        self.assertEqual(self.call("/api/login", form, origin=None, referer="http://" + HOST + "/")[0], 200)

    def test_login_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.call("/api/login", {"password": "wrong"})[0], 401)
        self.assertEqual(self.call("/api/login", {"password": PASSWORD})[0], 429)

    def test_payload_and_command_whitelists(self):
        cookie = self.login()
        invalid = [
            ("pointer", {"action": "move", "dx": True, "dy": 0}),
            ("pointer", {"action": "move", "dx": 121, "dy": 0}),
            ("pointer", {"action": "scroll", "steps": 0}),
            ("pointer", {"action": "down", "button": 2}),
            ("text", {"text": "中" * 1366, "enter": False}),
            ("text", {"text": "中", "enter": 1}),
            ("text", {"text": "a\x00b", "enter": False}),
            ("key", {"id": "Ctrl+Alt+Delete"}),
            ("volume", {"id": 101}),
            ("media", {"id": "exec"}),
            ("open-site", {"url": "https://example.test/"}),
            ("open-site", {"id": "youtube", "url": "https://example.test/"}),
            ("search", {"query": "   "}),
        ]
        for path, data in invalid:
            with self.subTest(path=path):
                self.assertEqual(self.call("/api/" + path, data, cookie=cookie)[0], 400)
        self.assertEqual(self.call("/api/key", cookie=cookie, body=b'{"id":"up","id":"down"}')[0], 400)
        self.assertEqual(self.call("/api/text", cookie=cookie, body=b" " * 32769)[0], 413)
        self.assertEqual(self.server.inputs.desktop.method_calls, [])

    def test_valid_actions(self):
        cookie = self.login()
        requests = [
            ("key", {"id": "escape"}, "key", (KEYS["escape"],)),
            ("text", {"text": "English 中文 🙂", "enter": True}, "paste", ("English 中文 🙂", True)),
            ("open-site", {"id": "youtube"}, "open", ("https://www.youtube.com/",)),
            ("pointer", {"action": "move", "dx": -25, "dy": 10}, "pointer", ("move", (-25, 10))),
            ("pointer", {"action": "click"}, "pointer", ("click", ())),
            ("pointer", {"action": "scroll", "steps": -2}, "pointer", ("scroll", (-2,))),
            ("volume", {"id": "up"}, "volume", ("up",)),
            ("media", {"id": "play-pause"}, "media", ()),
        ]
        for endpoint, payload, method, args in requests:
            self.assertEqual(self.call("/api/" + endpoint, payload, cookie=cookie)[0], 200)
            getattr(self.server.inputs.desktop, method).assert_called_with(*args)
        query = "中文 空格 & + ? # 🙂"
        self.assertEqual(self.call("/api/search", {"query": query}, cookie=cookie)[0], 200)
        url = urlsplit(self.server.inputs.desktop.open.call_args[0][0])
        self.assertEqual((url.scheme, url.hostname, url.path),
                         ("https", "www.youtube.com", "/results"))
        self.assertEqual(parse_qs(url.query), {"search_query": [query]})

    def test_drag_release_and_watchdog(self):
        first, second = self.login(), self.login()
        desktop = self.server.inputs.desktop
        released = threading.Event()
        desktop.pointer.side_effect = lambda action, _: released.set() if action == "up" else None
        with patch("server.DRAG_TIMEOUT", .15):
            self.assertEqual(self.call("/api/pointer", {"action": "down"}, cookie=first)[0], 200)
            self.assertEqual(self.call("/api/pointer", {"action": "up"}, cookie=second)[0], 200)
            self.assertFalse(released.is_set())
            self.assertEqual(self.call("/api/pointer", {"action": "up"}, cookie=first)[0], 200)
            self.assertTrue(released.is_set())
            released.clear()
            self.assertEqual(self.call("/api/pointer", {"action": "down"}, cookie=first)[0], 200)
            self.assertTrue(released.wait(2))


class CommandTest(unittest.TestCase):
    @patch("desktop.shutil.which", side_effect=lambda tool: tool)
    def test_fixed_subprocess_arguments(self, _):
        for backend, env in (("x11", {"DISPLAY": ":0"}), ("wayland", {"WAYLAND_DISPLAY": "wayland-0"})):
            with self.subTest(backend=backend), patch.dict("os.environ", env):
                desktop = Desktop(backend)
                desktop.run = Mock()
                desktop.pointer("move", (-4, 9))
                move = (["xdotool", "mousemove_relative", "--", "-4", "9"] if desktop.x11
                        else ["ydotool", "mousemove", "-x", "-4", "-y", "9"])
                desktop.run.assert_called_once_with(move)
                desktop.run.reset_mock()
                desktop.key(KEYS["address"])
                keys = (["xdotool", "key", "--clearmodifiers", "ctrl+l"] if desktop.x11
                        else ["ydotool", "key", "29:1", "38:1", "38:0", "29:0"])
                desktop.run.assert_called_once_with(keys)
                desktop.run.reset_mock()
                with patch("desktop.time.sleep"):
                    desktop.paste("中文", True)
                commands = desktop.run.call_args_list
                self.assertEqual(commands[0][0][1], "中文".encode("utf-8"))
                self.assertEqual(len(commands), 3)

    def test_reject_bad_config(self):
        original = json.loads((ROOT / "config.example.json").read_text())
        for url in ("file:///etc/passwd", "https://user@youtube.com/", "https://youtube.com:0/",
                    "https://example.com/\n", "https://example.com/\\"):
            original["sites"][0]["url"] = url
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "config.json"
                path.write_text(json.dumps(original))
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
