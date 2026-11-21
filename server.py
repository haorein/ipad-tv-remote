#!/usr/bin/env python3
"""LAN-only remote, using the Python standard library."""
import argparse
from collections import OrderedDict
import getpass
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading
import time
from urllib.parse import quote, urlsplit

from desktop import Desktop, KEYS

ROOT = Path(__file__).resolve().parent
PRIVATE = [ipaddress.ip_network(net) for net in
           ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128")]
SESSION_TTL = 8 * 60 * 60
DRAG_TIMEOUT = 1.5
MAX_BODY = 32768
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"),
          "/style.css": ("style.css", "text/css")}
SECRETS_ROUNDS = 300000
# Fill in verified routes when loading a config saved before per-site search.
DEFAULT_SEARCH_URLS = {
    ("https", "www.youtube.com", 443): "https://www.youtube.com/results?search_query={query}",
    ("https", "kubb.tv", 443): "https://kubb.tv/search/{query}",
    ("https", "www.kubb.tv", 443): "https://www.kubb.tv/search/{query}",
    ("https", "olevod.com", 443): "https://olevod.com/search?q={query}",
    ("https", "www.olevod.com", 443): "https://www.olevod.com/search?q={query}",
}


def require(condition):
    if not condition:
        raise ValueError("Invalid request")


def strict_object(pairs):
    result = dict(pairs)
    require(len(result) == len(pairs))
    return result


def decode_json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=strict_object)


def site_origin(url):
    require(0 < len(url) <= 2048 and url.isascii() and all(32 < ord(ch) < 127 for ch in url))
    require("\\" not in url)
    parsed = urlsplit(url)
    require(parsed.scheme in ("http", "https") and parsed.hostname and
            parsed.username is None and parsed.password is None and parsed.port != 0)
    require("{" not in parsed.netloc and "}" not in parsed.netloc)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def load_config(path):
    config = decode_json(path.read_bytes())
    require(type(config) is dict)
    require(set(config) == {"bind", "port", "backend", "networks", "hosts", "sites"})
    address = ipaddress.ip_address(config["bind"])
    require(address.version == 4 and (address.is_unspecified or any(address in net for net in PRIVATE)))
    require(type(config["port"]) is int and 1024 <= config["port"] <= 65535)
    require(config["backend"] in ("auto", "x11", "wayland"))
    require(type(config["networks"]) is list and 0 < len(config["networks"]) <= 32)
    require(all(type(net) is str for net in config["networks"]))
    config["networks"] = [ipaddress.ip_network(net) for net in config["networks"]]
    require(all(any(net.version == parent.version and net.subnet_of(parent) for parent in PRIVATE)
                for net in config["networks"]))
    require(type(config["hosts"]) is list and len(config["hosts"]) <= 32)
    require(all(type(host) is str and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", host)
                for host in config["hosts"]))
    require(type(config["sites"]) is list and 0 < len(config["sites"]) <= 32)
    ids = set()
    for site in config["sites"]:
        require(type(site) is dict and {"id", "name", "url"} <= set(site) <=
                {"id", "name", "url", "color", "search_url"})
        require(all(type(value) is str for value in site.values()))
        require(re.fullmatch(r"[a-z0-9-]{1,32}", site["id"]) and site["id"] not in ids)
        require(0 < len(site["name"]) <= 60 and site["name"].isprintable())
        require(re.fullmatch(r"#[0-9a-fA-F]{6}", site.get("color", "#333333")))
        origin = site_origin(site["url"])
        if "search_url" in site:
            template = site["search_url"]
            require(template.count("{query}") == 1 and template.count("{") == template.count("}") == 1)
            require(site_origin(template) == origin)
        elif origin in DEFAULT_SEARCH_URLS:
            site["search_url"] = DEFAULT_SEARCH_URLS[origin]
        ids.add(site["id"])
    return config


def allowed_source(ip, config):
    address = ipaddress.ip_address(ip)
    return any(address in network for network in config["networks"])


def allowed_host(host, config):
    name, separator, port = host.lower().partition(":")
    if separator:
        if port != str(config["port"]):
            return False
    elif config["port"] != 80:
        return False
    if name == "localhost" or name in config["hosts"]:
        return True
    try:
        address = ipaddress.IPv4Address(name)
    except ValueError:
        return False
    return any(address in net for net in PRIVATE)


def same_origin(host, origin, referer):
    expected = "http://" + host.lower()
    if origin is not None:
        return origin.lower() == expected
    # Older Safari may only send Referer on same-origin XHR.
    try:
        ref = urlsplit(referer or "")
        return ref.scheme + "://" + ref.netloc.lower() == expected
    except ValueError:
        return False


def derive(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, SECRETS_ROUNDS)


def create_auth(path):
    password = getpass.getpass("遥控口令（至少 8 字符）: ")
    require(8 <= len(password) and len(password.encode("utf-8")) <= 256)
    require(password == getpass.getpass("再次输入口令: "))
    salt = secrets.token_bytes(16)
    with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump({"salt": salt.hex(), "hash": derive(password, salt).hex(),
                   "signing": secrets.token_hex(32)}, file)


class Auth:
    def __init__(self, path):
        with os.fdopen(os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as file:
            info = os.fstat(file.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                    stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 1024)
            keys = decode_json(file.read(1025))
        self.salt, self.key, self.password_hash = [bytes.fromhex(keys[name])
                                                  for name in ("salt", "signing", "hash")]
        require((len(self.salt), len(self.key), len(self.password_hash)) == (16, 32, 32))
        self.sessions = OrderedDict()
        self.attempts = OrderedDict()
        self.lock = threading.Lock()

    def sign(self, payload):
        return hmac.new(self.key, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def login(self, ip, password):
        with self.lock:
            now = time.monotonic()
            attempts = [stamp for stamp in self.attempts.get(ip, ()) if now - stamp < 60]
            if len(attempts) >= 5:
                return 429, None
            if not hmac.compare_digest(derive(password, self.salt), self.password_hash):
                self.attempts[ip] = attempts + [now]
                self.attempts.move_to_end(ip)
                if len(self.attempts) > 1024:
                    self.attempts.popitem(last=False)
                return 401, None
            self.attempts.pop(ip, None)
            nonce = secrets.token_urlsafe(24)
            expiry = int(time.time()) + SESSION_TTL
            payload = nonce + "." + str(expiry)
            self.sessions[nonce] = expiry
            if len(self.sessions) > 128:
                self.sessions.popitem(last=False)
            return 200, payload + "." + self.sign(payload)

    def session(self, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32}\.[0-9]{1,12}\.[0-9a-f]{64}", token):
            return None
        nonce, expiry, signature = token.split(".")
        if not hmac.compare_digest(signature, self.sign(nonce + "." + expiry)):
            return None
        with self.lock:
            if self.sessions.get(nonce) == int(expiry) and int(expiry) > time.time():
                return nonce
        return None

    def logout(self, session):
        with self.lock:
            self.sessions.pop(session, None)


def fields(data, names):
    require(type(data) is dict and data.keys() == set(names))
    return [data[name] for name in names]


def number(value, minimum, maximum):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def choice(value, allowed):
    require(type(value) is str and value in allowed)
    return value


def user_text(value, maximum):
    require(type(value) is str and "\x00" not in value and 0 < len(value.encode("utf-8")) <= maximum)
    return value


def search_address(template, query):
    if query in (".", ".."):
        # Browsers navigate away from a search path for standalone dot segments.
        for segment in urlsplit(template).path.split("/"):
            if "{query}" in segment:
                segment = segment.replace("{query}", query).lower().replace("%2e", ".")
                require(segment not in (".", ".."))
    return template.replace("{query}", quote(query, safe=""))


def action_for(path, data, sites):
    if path == "/api/pointer":
        require(type(data) is dict)
        action = choice(data.get("action"), ("move", "scroll", "click", "down", "up", "hold"))
        if action == "move":
            _, dx, dy = fields(data, ("action", "dx", "dy"))
            return ("pointer", action, (number(dx, -120, 120), number(dy, -120, 120)))
        if action == "scroll":
            _, steps = fields(data, ("action", "steps"))
            require(steps != 0)
            return ("pointer", action, (number(steps, -8, 8),))
        fields(data, ("action",))
        return ("pointer", action, ())
    if path == "/api/text":
        value, enter = fields(data, ("text", "enter"))
        require(type(enter) is bool)
        return ("paste", user_text(value, 4096), enter)
    if path == "/api/key":
        return ("key", KEYS[choice(fields(data, ("id",))[0], KEYS)])
    if path == "/api/volume":
        return ("volume", choice(fields(data, ("id",))[0], ("up", "down", "mute")))
    if path == "/api/media":
        choice(fields(data, ("id",))[0], ("play-pause",))
        return ("media",)
    if path == "/api/open-site":
        site_id = choice(fields(data, ("id",))[0], sites)
        return ("open-site", site_id, sites[site_id]["url"])
    if path == "/api/search":
        require(type(data) is dict)
        paste = "mode" in data
        if paste:
            site_id, query, mode = fields(data, ("id", "query", "mode"))
            require(mode == "paste")
        else:
            site_id, query = fields(data, ("id", "query"))
        site = sites[choice(site_id, sites)]
        query = user_text(query, 1024)
        require(not re.search(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", query))
        query = query.strip()
        require(query)
        if paste:
            require("search_url" not in site)
            return ("search", site_id, "paste", query, True)
        require("search_url" in site)
        return ("search", site_id, "open", search_address(site["search_url"], query))
    return None


class SiteChanged(Exception):
    pass


class Input:
    def __init__(self, desktop):
        self.desktop = desktop
        self.lock = threading.Lock()
        self.owner = None
        self.active_site = None
        self.deadline = 0
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.watch, daemon=True)
        self.worker.start()

    def release(self, session):
        if self.owner == session and session is not None:
            self.desktop.pointer("up", ())
            self.owner = None

    def watch(self):
        while not self.stop.wait(.2):
            with self.lock:
                if self.owner and time.monotonic() >= self.deadline:
                    try:
                        self.release(self.owner)
                    except (OSError, subprocess.SubprocessError):
                        self.deadline = time.monotonic() + 1

    def execute(self, session, command):
        method, *args = command
        with self.lock:
            if method == "search":
                site_id, method, *args = args
                if self.active_site != site_id:
                    raise SiteChanged()
            if method == "open-site":
                site_id, url = args
                self.desktop.open(url)
                self.active_site = site_id
                return
            if method != "pointer":
                getattr(self.desktop, method)(*args)
                return
            action, values = args
            if action == "up":
                self.release(session)
                return
            if action == "hold":
                if self.owner == session:
                    self.deadline = time.monotonic() + DRAG_TIMEOUT
                return
            if self.owner and (self.owner != session or action in ("click", "scroll")):
                raise ValueError("Drag in progress")
            if action == "down":
                fresh = self.owner is None
                self.owner = session
                self.deadline = time.monotonic() + DRAG_TIMEOUT
                if not fresh:
                    return
            self.desktop.pointer(action, values)
            if self.owner == session:
                self.deadline = time.monotonic() + DRAG_TIMEOUT

    def close(self):
        self.stop.set()
        self.worker.join()
        with self.lock:
            self.release(self.owner)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(4)
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, data, cookie=None, content_type="application/json"):
        body = (json.dumps(data).encode("utf-8") if content_type == "application/json" else data)
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return self.server.auth.session(cookie["remote"].value) if "remote" in cookie else None
        except (ValueError, AttributeError):
            return None

    def check_access(self, post=False):
        hosts = self.headers.get_all("Host", [])
        if (not allowed_source(self.client_address[0], self.server.config) or len(hosts) != 1
                or not allowed_host(hosts[0], self.server.config)):
            self.reply(403, {"error": "来源或主机地址未获允许"})
            return False
        if post and (self.headers.get_all("X-Remote-Request") != ["1"] or
                     len(self.headers.get_all("Origin", [])) > 1 or
                     len(self.headers.get_all("Referer", [])) > 1 or
                     not same_origin(hosts[0], self.headers.get("Origin"), self.headers.get("Referer"))):
            self.reply(403, {"error": "请求未通过同源检查"})
            return False
        return True

    def do_GET(self):
        if not self.check_access():
            return
        if self.path in STATIC:
            filename, mime = STATIC[self.path]
            self.reply(200, (ROOT / "static" / filename).read_bytes(), content_type=mime)
        elif self.path in ("/api/config", "/api/health"):
            if not self.session():
                self.reply(401, {"error": "请登录"})
                return
            data = {"backend": self.server.inputs.desktop.backend}
            if self.path == "/api/config":
                data["sites"] = []
                for site in self.server.config["sites"]:
                    item = {key: site[key] for key in ("id", "name", "color") if key in site}
                    item["searchable"] = "search_url" in site
                    data["sites"].append(item)
                with self.server.inputs.lock:
                    data["active_site"] = self.server.inputs.active_site
            self.reply(200, data)
        else:
            self.reply(404, {"error": "路径不存在"})

    def do_POST(self):
        if not self.check_access(post=True):
            return
        if self.headers.get_all("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
            self.reply(415, {"error": "需要 JSON 请求"})
            return
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,10}", sizes[0]):
            self.reply(400, {"error": "请求长度无效"})
            return
        size = int(sizes[0])
        if size > MAX_BODY:
            self.reply(413, {"error": "请求过大"})
            return
        try:
            payload = self.rfile.read(size)
            require(len(payload) == size)
            data = decode_json(payload)
            if self.path == "/api/login":
                password = user_text(fields(data, ("password",))[0], 256)
                status, token = self.server.auth.login(self.client_address[0], password)
                if token:
                    self.reply(200, {}, "remote=" + token + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" +
                               str(SESSION_TTL))
                else:
                    self.reply(status, {"error": "口令错误" if status == 401 else "尝试过多，请稍后再试"})
                return
            session = self.session()
            if not session:
                self.reply(401, {"error": "请登录"})
                return
            if self.path == "/api/logout":
                fields(data, ())
                self.server.auth.logout(session)
                with self.server.inputs.lock:
                    try:
                        self.server.inputs.release(session)
                    except (OSError, subprocess.SubprocessError):
                        self.server.inputs.deadline = time.monotonic()
                self.reply(200, {}, "remote=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                return
            command = action_for(self.path, data, self.server.sites)
            if command is None:
                self.reply(404, {"error": "动作不存在"})
                return
            self.server.inputs.execute(session, command)
        except SiteChanged:
            self.reply(409, {"error": "网站已切换，请确认当前搜索站点"})
            return
        except (ValueError, RecursionError):
            self.reply(400, {"error": "请求无效或另一操作正在拖动"})
            return
        except (OSError, subprocess.SubprocessError):
            self.reply(503, {"error": "桌面操作失败，请检查本机工具和图形会话"})
            return
        self.reply(200, {})


class Server(ThreadingHTTPServer):
    # Let in-flight desktop actions finish before releasing the drag on shutdown.
    daemon_threads = False
    slots = threading.BoundedSemaphore(24)

    def __init__(self, config, auth, inputs):
        self.config, self.auth, self.inputs = config, auth, inputs
        self.sites = {site["id"]: site for site in config["sites"]}
        super().__init__((config["bind"], config["port"]), Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        # Avoid tracebacks containing request or subprocess data.
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path,
                        default=Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) /
                        "ipad-tv-remote")
    parser.add_argument("--init-password", action="store_true")
    args = parser.parse_args()
    if os.getuid() == 0 or os.geteuid() == 0:
        parser.exit(1, "Run as the graphical desktop user.\n")
    secret = args.config_dir / "auth.json"
    if args.init_password:
        try:
            args.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            create_auth(secret)
        except FileExistsError:
            parser.exit(1, "auth.json already exists.\n")
        except (ValueError, OSError, EOFError):
            parser.exit(1, "Could not set password. Check length, confirmation and directory access.\n")
        return
    try:
        config = load_config(args.config_dir / "config.json")
        auth = Auth(secret)
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        parser.exit(1, "Check config.json and a user-owned auth.json with mode 0600.\n")
    try:
        desktop = Desktop(config["backend"])
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    inputs = Input(desktop)
    try:
        with Server(config, auth, inputs) as server:
            import signal
            signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
            print("Input backend: {}. Listening on {}:{}.".format(
                desktop.backend, config["bind"], config["port"]), flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError:
        parser.exit(1, "Could not listen on configured address and port.\n")
    finally:
        inputs.close()


if __name__ == "__main__":
    main()
