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
from urllib.parse import parse_qs, unquote, urlsplit

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
        config["sites"].extend([
            {"id": "archive", "name": "Archive", "url": "https://archive.test/",
             "search_url": "https://archive.test/find/{query}"},
            {"id": "manual", "name": "Manual", "url": "https://manual.test/"},
        ])
        desktop = Mock(spec=Desktop)
        desktop.backend = "x11"
        inputs = Input(desktop)
        self.addCleanup(inputs.close)
        self.server = SimpleNamespace(config=config, auth=Auth(path), inputs=inputs,
                                      sites={site["id"]: site for site in config["sites"]})

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
            ("key", {"id": "fullscreen"}),
            ("volume", {"id": 101}),
            ("media", {"id": "exec"}),
            ("media", {"id": "video-fullscreen", "key": "F11"}),
            ("media", {"id": "video-fullscreen", "site": "youtube"}),
            ("open-site", {"url": "https://example.test/"}),
            ("open-site", {"id": "youtube", "url": "https://example.test/"}),
            ("search", {"query": "keyword"}), ("search", {"id": "youtube", "query": "   "}),
            ("search", {"id": "unknown", "query": "keyword"}),
            ("search", {"id": "youtube", "query": "中" * 342}),
            ("search", {"id": "youtube", "query": "line\nbreak"}),
            ("search", {"id": "manual", "query": "line\tbreak", "mode": "paste"}),
            ("search", {"id": "youtube", "query": "x", "url": "https://example.test/"}),
            ("search", {"id": "youtube", "query": "x", "mode": "paste"}),
            ("search", {"id": "manual", "query": "x", "mode": "auto"}),
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
        self.assertEqual(self.call("/api/search", {"id": "youtube", "query": query}, cookie=cookie)[0], 200)
        url = urlsplit(self.server.inputs.desktop.open.call_args[0][0])
        self.assertEqual((url.scheme, url.hostname, url.path),
                         ("https", "www.youtube.com", "/results"))
        self.assertEqual(parse_qs(url.query), {"search_query": [query]})

    def test_site_search_follows_successful_open_across_remotes(self):
        first, second = self.login(), self.login()
        def config():
            return json.loads(self.call("/api/config", cookie=first)[2])

        previous = {"id": "youtube", "query": "中文 + / & # ? %"}
        archive = dict(previous, id="archive")
        self.assertEqual(self.call("/api/search", archive)[0], 401)
        self.assertIsNone(config()["active_site"])
        self.assertEqual(self.call("/api/search", previous, cookie=first)[0], 409)
        sites = {site["id"]: site for site in config()["sites"]}
        self.assertEqual(sites["archive"], {"id": "archive", "name": "Archive", "searchable": True})
        self.assertEqual(sites["manual"], {"id": "manual", "name": "Manual", "searchable": False})

        self.assertEqual(self.call("/api/open-site", {"id": "youtube"}, cookie=first)[0], 200)
        desktop = self.server.inputs.desktop
        desktop.open.side_effect = OSError("Could not open site")
        self.assertEqual(self.call("/api/open-site", {"id": "archive"}, cookie=second)[0], 503)
        self.assertEqual(config()["active_site"], "youtube")
        desktop.open.side_effect = None
        self.assertEqual(self.call("/api/open-site", {"id": "archive"}, cookie=second)[0], 200)
        self.assertEqual(config()["active_site"], "archive")
        desktop.reset_mock()
        self.assertEqual(self.call("/api/search", previous, cookie=first)[0], 409)
        for dots in (".", ".."):
            self.assertEqual(self.call("/api/search", dict(archive, query=dots), cookie=first)[0], 400)
        desktop.open.assert_not_called()
        self.assertEqual(self.call("/api/search", archive, cookie=second)[0], 200)
        url = urlsplit(desktop.open.call_args[0][0])
        self.assertEqual((url.scheme, url.netloc, url.query, url.fragment), ("https", "archive.test", "", ""))
        _, prefix, encoded = url.path.split("/")
        self.assertEqual((prefix, unquote(encoded)), ("find", archive["query"]))

    def test_video_fullscreen_uses_last_opened_site_across_remotes(self):
        first, second = self.login(), self.login()
        desktop = self.server.inputs.desktop
        toggle = {"id": "video-fullscreen"}
        self.assertEqual(self.call("/api/media", toggle)[0], 401)
        self.server.sites["renamed"] = {"id": "renamed", "url": "https://youtube.com/"}
        for site in (None, "youtube", "kubb", "olevod", "renamed", "manual"):
            with self.subTest(site=site):
                if site:
                    self.assertEqual(self.call("/api/open-site", {"id": site}, cookie=first)[0], 200)
                desktop.reset_mock()
                self.assertEqual(self.call("/api/media", toggle, cookie=second)[0], 200)
                if site in ("youtube", "renamed"):
                    desktop.key.assert_called_once_with(("f", (33,)))
                    desktop.pointer.assert_not_called()
                else:
                    desktop.pointer.assert_called_once_with("double-click", ())
                    desktop.key.assert_not_called()
        desktop.reset_mock()
        self.assertEqual(self.call("/api/key", toggle, cookie=first)[0], 200)
        desktop.pointer.assert_called_once_with("double-click", ())

    def test_search_field_requires_explicit_paste_mode_and_matching_site(self):
        cookie = self.login()
        request = {"id": "manual", "query": "中文 🙂"}
        assisted = dict(request, mode="paste")
        self.assertEqual(self.call("/api/search", assisted, cookie=cookie)[0], 409)
        self.assertEqual(self.call("/api/open-site", {"id": "manual"}, cookie=cookie)[0], 200)
        desktop = self.server.inputs.desktop
        desktop.reset_mock()
        self.assertEqual(self.call("/api/search", request, cookie=cookie)[0], 400)
        self.assertEqual(self.call("/api/search", dict(assisted, query="\rtext"), cookie=cookie)[0], 400)
        self.assertFalse(desktop.method_calls)
        self.assertEqual(self.call("/api/search", assisted, cookie=cookie)[0], 200)
        desktop.paste.assert_called_once_with(request["query"], True)
        desktop.open.assert_not_called()

    def test_drag_release_and_watchdog(self):
        first, second = self.login(), self.login()
        desktop = self.server.inputs.desktop
        released = threading.Event()
        desktop.pointer.side_effect = lambda action, _: released.set() if action == "up" else None
        with patch("server.DRAG_TIMEOUT", .15):
            self.assertEqual(self.call("/api/pointer", {"action": "down"}, cookie=first)[0], 200)
            for remote in (first, second):
                self.assertEqual(self.call("/api/media", {"id": "video-fullscreen"}, cookie=remote)[0], 400)
            desktop.pointer.assert_called_once_with("down", ())
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
                desktop.pointer("double-click", ())
                clicks = (["xdotool", "click", "--repeat", "2", "--delay", "80", "1"] if desktop.x11 else
                          ["ydotool", "click", "--repeat", "2", "--next-delay", "40", "0xC0"])
                desktop.run.assert_called_once_with(clicks)
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

    def test_per_site_templates_and_legacy_defaults(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        config["sites"] = [
            {"id": "videos", "name": "Videos", "url": "https://www.youtube.com:443/"},
            {"id": "a", "name": "Archive", "url": "https://kubb.tv/"},
            {"id": "b", "name": "Library", "url": "https://olevod.com/"},
            {"id": "local", "name": "Local", "url": "https://other.test/"},
            {"id": "custom", "name": "Custom", "url": "https://video.test/",
             "search_url": "https://video.test/#/find?q={query}"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            original = json.dumps(config)
            path.write_text(original)
            sites = load_config(path)["sites"]
            self.assertEqual([s.get("search_url") for s in sites], [
                "https://www.youtube.com/results?search_query={query}",
                "https://kubb.tv/search/{query}", "https://olevod.com/search?q={query}",
                None, "https://video.test/#/find?q={query}",
            ])
            self.assertEqual(path.read_text(), original)
            config["sites"] = [{"id": "youtube", "name": "Local", "url": "https://other.test/"}]
            path.write_text(json.dumps(config))
            self.assertNotIn("search_url", load_config(path)["sites"][0])

    def test_reject_invalid_search_routes(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        site = {"id": "a", "url": "https://video.test/", "name": "Videos"}
        config["sites"] = [site]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            for template in ("https://other.test/?q={query}", "http://video.test/?q={query}",
                             "https://video.test:444/?q={query}", "https://{query}.video.test/",
                             "https://user@video.test/?q={query}", "file:///tmp/{query}", "//video.test/{query}",
                             "https://video.test/?q=", "https://video.test/?q={query}{query}",
                             "https://video.test/?q={query}{bad}", "https://video.test/\\x/{query}",
                             "https://video.test/?q={query}\n", None):
                with self.subTest(template=template):
                    site["search_url"] = template
                    path.write_text(json.dumps(config))
                    with self.assertRaises(ValueError):
                        load_config(path)


if __name__ == "__main__":
    unittest.main()
