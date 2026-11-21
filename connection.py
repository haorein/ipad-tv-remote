"""Show the phone connection addresses in a local page on the TV computer."""
import base64
from html import escape
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import NamedTuple

from server import PRIVATE, load_config


class Endpoint(NamedTuple):
    interface: str
    url: str
    alternate: bool
    mac: str = ""
    broadcast: str = ""
    wake_status: str = ""


def wifi_wake_status(interface):
    try:
        phy = (Path("/sys/class/net") / interface / "phy80211" / "name").read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", phy):
            raise ValueError
        result = subprocess.run(["iw", "phy", phy, "info"], check=True, timeout=3,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "未能读取无线唤醒能力。请在电脑上用 iw list 检查 WoWLAN support。"
    if b"wake up on magic packet" in result.stdout:
        return "无线驱动报告支持唤醒包。请按 README 启用 WoWLAN，然后合盖实测。"
    return "当前无线驱动未报告支持唤醒包。可在接电使用电视时让电脑保持运行。"


def connection_addresses(config, interfaces):
    networks = [net for net in config["networks"] if net.version == 4 and not net.is_loopback]
    addresses = set()
    for interface in interfaces:
        name = interface["ifname"]
        if (not name.isprintable() or "/" in name or name in (".", "..")
                or interface.get("operstate") in ("DOWN", "LOWERLAYERDOWN")):
            continue
        kind = interface.get("linkinfo", {}).get("info_kind", "")
        alternate = False
        if kind in ("wireguard", "tun", "gre", "gretap") or name.startswith(("wg", "tun", "tap", "tailscale")):
            label, alternate = "VPN / 隧道", True
        elif kind == "veth" or name.startswith(("docker", "virbr", "veth", "br-")):
            label, alternate = "虚拟网络", True
        elif name.startswith("wl") or (Path("/sys/class/net") / name / "phy80211").is_dir():
            label = "Wi-Fi"
        elif name.startswith(("en", "eth")):
            label = "有线网络"
        else:
            label = "网络接口"
        wake_status = None
        for info in interface["addr_info"]:
            if info["family"] != "inet" or info["scope"] != "global":
                continue
            link = ipaddress.IPv4Interface("{}/{}".format(info["local"], info["prefixlen"]))
            if link.ip.is_loopback or not any(link.ip in net for net in PRIVATE):
                continue
            if config["bind"] not in ("0.0.0.0", str(link.ip)) or not networks:
                continue
            # networks filters the phone's IP, which can differ from the TV's subnet.
            routed = not any(link.network.overlaps(net) for net in networks)
            title = "{} · {}".format(label, name)
            if routed:
                title += "（仅允许从配置的其他网段访问）"
            url = "http://{}:{}/".format(link.ip, config["port"])
            mac = interface.get("address", "")
            if (not alternate and not routed and not kind and interface.get("link_type") == "ether"
                    and link.network.prefixlen <= 30 and re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac)
                    and mac != "00:00:00:00:00:00" and not (int(mac[:2], 16) & 1)):
                if label == "Wi-Fi" and wake_status is None:
                    wake_status = wifi_wake_status(name)
                addresses.add(Endpoint(title, url, False, mac, str(link.network.broadcast_address), wake_status or ""))
            else:
                addresses.add(Endpoint(title, url, alternate or routed))
    return sorted(addresses, key=lambda entry: (entry.alternate, entry.interface, entry.url))


def discover_addresses(config):
    result = subprocess.run(["ip", "-j", "-d", "address", "show", "up"], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
    return connection_addresses(config, json.loads(result.stdout))


def qr_image(url):
    try:
        result = subprocess.run(["qrencode", "-t", "PNG", "-s", "10", "-m", "4", "-o", "-", url],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""
    if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        return ""
    data = base64.b64encode(result.stdout).decode("ascii")
    return '<img src="data:image/png;base64,{}" alt="遥控页面二维码">'.format(data)


def connection_page(addresses, problem):
    cards = []
    for endpoint in addresses:
        image = qr_image(endpoint.url)
        wake = ""
        if endpoint.mac:
            wake = ('<details><summary>从休眠唤醒电脑</summary>'
                    '{}'
                    '<p>电脑启用网络唤醒后，在手机的 WoL 工具中保存以下信息。</p>'
                    '<p>网卡 MAC <code>{}</code><br>广播地址 <code>{}</code><br>UDP 端口 <code>9</code></p>'
                    '<p>休眠后，在同一家庭网络发送唤醒包，再回到遥控器。</p></details>'
                    ).format("<p>{}</p>".format(escape(endpoint.wake_status)) if endpoint.wake_status else "",
                             escape(endpoint.mac), escape(endpoint.broadcast))
        cards.append('<section><h2>{}</h2>{}<p class="url">{}</p>{}</section>'.format(
            escape(endpoint.interface), image or "<p>请在手机浏览器中输入下方网址。</p>", escape(endpoint.url), wake))
    regular = sum(not endpoint.alternate for endpoint in addresses)
    content = "".join(cards[:regular])
    if regular and regular < len(cards):
        content += "<details><summary>其他网络接口</summary>{}</details>".format("".join(cards[regular:]))
    else:
        content += "".join(cards[regular:])
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>连接手机或平板</title>
<style>
* {{box-sizing:border-box}} body {{margin:0;background:#101617;color:#f1f6f3;font:24px/1.5 sans-serif}}
main {{max-width:1150px;margin:auto;padding:4vh 4vw;text-align:center}} h1 {{margin:0;font-size:1.8em}}
p {{margin:.45em 0 1em}} section {{display:inline-block;vertical-align:top;max-width:100%;margin:0 .35em 1em;
padding:.8em 1em;text-align:center;background:#1b2423;border:1px solid #53615c;border-radius:12px}}
h2 {{font-size:1em;margin:0 0 .7em}} img {{display:block;margin:auto;max-width:100%;height:auto}}
.url {{font: bold 1.1em/1.4 monospace;color:#b3eece;overflow-wrap:anywhere;margin:.7em 0 0}}
summary {{cursor:pointer;margin:0 0 .8em}}
section details {{margin-top:1em;font-size:.75em}}
@media (max-width:600px) {{body {{font-size:18px}} section {{margin:0 0 1em}}}}
@media(max-width:580px) {{body {{font-size:18px}} section {{display:block;margin:0 0 1em}}}}
</style></head><body><main><h1>连接手机或平板</h1><p>{}</p>{}</main></body></html>""".format(
        escape(problem or "手机或平板连到与电脑相同的可信局域网，扫描对应的二维码或输入网址，再输入遥控口令。"),
        content)


def show_page(markup):
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ipad-tv-remote"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    page = cache / "connection.html"
    file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache, delete=False)
    try:
        with file:
            file.write(markup)
        os.replace(file.name, page)
    finally:
        if os.path.exists(file.name):
            os.unlink(file.name)
    try:
        subprocess.run(["gio", "open", page.resolve().as_uri()], check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        print("请在 Linux 电脑的浏览器中打开 {}".format(page), file=sys.stderr)


def main():
    if os.geteuid() == 0:
        print("请使用图形桌面普通用户运行。", file=sys.stderr)
        return 1
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ipad-tv-remote"
    addresses, problem = [], ""
    try:
        config = load_config(config_dir / "config.json")
        if ipaddress.ip_address(config["bind"]).is_loopback:
            problem = "服务仅监听本机回环地址。请在配置中选择可信局域网地址并重启服务。"
        else:
            addresses = discover_addresses(config)
            if not addresses:
                problem = "找不到可用的局域网地址。请连接家庭网络，检查 config.json 的 bind 和 networks，再打开「连接手机」。"
    except (OSError, subprocess.SubprocessError, TypeError, KeyError, ValueError):
        problem = "无法读取网络接口或遥控配置。请检查 ip 命令和 config.json，或在项目目录重新运行 sh install.sh --setup。"
    if problem:
        print(problem, file=sys.stderr)
    for endpoint in addresses:
        print("{}  {}".format(endpoint.interface, endpoint.url))
    try:
        show_page(connection_page(addresses, problem))
    except OSError:
        print("无法写入本地连接页面。可在手机浏览器中输入上方网址。", file=sys.stderr)
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
