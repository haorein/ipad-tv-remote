"""Fixed desktop actions for X11 and ydotool 1.x."""
import os
import shutil
import subprocess
import time

KEYS = {
    "enter": ("Return", (28,)), "escape": ("Escape", (1,)),
    "tab": ("Tab", (15,)), "backspace": ("BackSpace", (14,)),
    "delete": ("Delete", (111,)), "space": ("space", (57,)),
    "up": ("Up", (103,)), "down": ("Down", (108,)),
    "left": ("Left", (105,)), "right": ("Right", (106,)),
    "home": ("Home", (102,)), "end": ("End", (107,)),
    "page-up": ("Page_Up", (104,)), "page-down": ("Page_Down", (109,)),
    "address": ("ctrl+l", (29, 38)), "back": ("alt+Left", (56, 105)),
    "forward": ("alt+Right", (56, 106)), "new-tab": ("ctrl+t", (29, 20)),
    "close-tab": ("ctrl+w", (29, 17)), "refresh": ("ctrl+r", (29, 19)),
}
VOLUME = {
    "wpctl": {"up": ["set-volume", "-l", "1", "@DEFAULT_AUDIO_SINK@", "5%+"],
              "down": ["set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"],
              "mute": ["set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]},
    "pactl": {"up": ["set-sink-volume", "@DEFAULT_SINK@", "+5%"],
              "down": ["set-sink-volume", "@DEFAULT_SINK@", "-5%"],
              "mute": ["set-sink-mute", "@DEFAULT_SINK@", "toggle"]},
    "amixer": {"up": ["sset", "Master", "5%+"], "down": ["sset", "Master", "5%-"],
               "mute": ["sset", "Master", "toggle"]},
}


def resolve_backend(backend):
    if backend != "auto":
        return backend
    session = os.environ.get("XDG_SESSION_TYPE")
    if session == "wayland" or (session != "x11" and os.environ.get("WAYLAND_DISPLAY")):
        return "wayland"
    return "x11"


class Desktop:
    def __init__(self, backend):
        self.backend = resolve_backend(backend)
        self.x11 = self.backend == "x11"
        display = "DISPLAY" if self.x11 else "WAYLAND_DISPLAY"
        if not os.environ.get(display):
            raise ValueError("Missing graphical environment: " + display)
        self.clipboard = next((tool for tool in (("xclip", "xsel") if self.x11 else ("wl-copy",))
                               if shutil.which(tool)), None)
        self.input = "xdotool" if self.x11 else "ydotool"
        if not self.clipboard or not shutil.which(self.input):
            raise ValueError("Install " + self.input + " and " + ("xclip or xsel" if self.x11 else "wl-copy"))

    @staticmethod
    def run(args, data=None):
        subprocess.run(args, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=3, check=True)

    def key(self, mapping):
        name, codes = mapping
        if self.x11:
            self.run([self.input, "key", "--clearmodifiers", name])
        else:
            self.run([self.input, "key"] + [str(code) + ":1" for code in codes]
                     + [str(code) + ":0" for code in reversed(codes)])

    def pointer(self, action, values):
        if action == "move":
            dx, dy = map(str, values)
            args = ["mousemove_relative", "--", dx, dy] if self.x11 else ["mousemove", "-x", dx, "-y", dy]
        elif action == "scroll":
            steps = values[0]
            args = (["click", "--repeat", str(abs(steps)), "4" if steps > 0 else "5"]
                    if self.x11 else ["mousemove", "-w", "--", "0", str(steps)])
        elif action == "double-click":
            args = (["click", "--repeat", "2", "--delay", "80", "1"] if self.x11 else
                    ["click", "--repeat", "2", "--next-delay", "40", "0xC0"])
        elif self.x11:
            args = [{"click": "click", "down": "mousedown", "up": "mouseup"}[action], "1"]
        else:
            args = ["click", {"click": "0xC0", "down": "0x40", "up": "0x80"}[action]]
        self.run([self.input] + args)

    def paste(self, value, enter):
        args = {"xclip": ["xclip", "-selection", "clipboard", "-in"],
                "xsel": ["xsel", "--clipboard", "--input"],
                "wl-copy": ["wl-copy", "--type", "text/plain;charset=utf-8"]}[self.clipboard]
        self.run(args, value.encode("utf-8"))
        self.key(("ctrl+v", (29, 47)))
        if enter:
            time.sleep(.15)
            self.key(KEYS["enter"])

    def volume(self, action):
        for tool, commands in VOLUME.items():
            if shutil.which(tool):
                try:
                    self.run([tool] + commands[action])
                    return
                except (OSError, subprocess.SubprocessError):
                    continue
        raise OSError("No working audio backend")

    def media(self):
        if shutil.which("playerctl"):
            try:
                self.run(["playerctl", "play-pause"])
                return
            except (OSError, subprocess.SubprocessError):
                pass
        self.key(("XF86AudioPlay", (164,)))

    def open(self, url):
        self.run(["gio", "open", url])
