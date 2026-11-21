# 用旧电脑驱动电视

给旧电脑装上带图形桌面的 Linux，通过 HDMI 等视频接口接到电视。用电脑上的浏览器播放 YouTube 等视频网站的内容，省下一个电视盒子。

手机或平板和 Linux 电脑连到同一可信局域网后，用移动浏览器打开遥控页面，就能把手里的设备当触控板和键盘，无需安装 App。可以点击、拖动、滚动、输入中英文，也能调音量、控制播放和快捷搜索 YouTube。页面设计也兼顾旧版 iPad Safari。

## Ubuntu X11 安装

在连接电视的 Linux 电脑上登录图形桌面，从桌面终端运行以下命令。主服务始终以普通用户运行。Ubuntu 主机若缺少依赖，可由管理员运行 `sudo apt install xdotool xclip playerctl`。

```sh
sh install.sh --check
sh install.sh --install
```

安装器在用户目录复制程序及用户服务文件，交互式设置至少 8 个字符的口令。建议设置更长的独立口令。配置默认在 `~/.config/ipad-tv-remote/`，支持 `XDG_CONFIG_HOME`。口令以加盐哈希保存，认证文件必须属于当前用户且权限为 `0600`。重复安装保留配置和口令。安装器只复制文件和创建初始配置，服务启动由以下命令控制。

Fedora 的 X11 依赖可用 `sudo dnf install python3 xdotool xclip playerctl glib2` 安装。Arch 系可用 `sudo pacman -S python xdotool xclip playerctl glib2`。已有可用的 `xsel` 时也可作为 X11 剪贴板工具。`playerctl` 可选，播放控制失败时会回退到媒体键。系统音量依次尝试 `wpctl`（PipeWire）、`pactl`（PulseAudio 或 PipeWire 的 PulseAudio 兼容服务）和 `amixer`（ALSA 的 Master 控件）。

## 配置和启动

编辑 `~/.config/ipad-tv-remote/config.json`。默认监听 IPv4 的 8765 端口，应用层只接受环回和 RFC1918 私网来源。建议把 `networks` 缩小到环回和 Linux 电脑所在的可信网段，例如 `["127.0.0.0/8", "192.168.1.0/24"]`。如希望只在指定接口监听，可将 `bind` 改成 Linux 电脑的固定私网 IP。使用 IP 地址时无需配置 `hosts`。使用可信本地域名时，在 `hosts` 中明确列出它。快捷网站支持固定 `id`、`name`、HTTP(S) `url` 和可选十六进制 `color`，须保留 `youtube` 条目。

在 Linux 电脑的图形桌面终端执行以下命令，导入 X11 会话变量，启动并启用用户服务，最后查看电脑的局域网地址。

```sh
systemctl --user daemon-reload
systemctl --user import-environment DISPLAY XDG_SESSION_TYPE
if [ -n "${XAUTHORITY:-}" ]; then
    systemctl --user import-environment XAUTHORITY
fi
systemctl --user enable --now ipad-tv-remote.service
systemctl --user status ipad-tv-remote.service
ip -4 -brief address
```

Linux 电脑上可先打开 `http://127.0.0.1:8765/`（若绑定专用接口，改用对应地址）。手机或平板与电脑连到同一可信局域网后，在移动浏览器中打开 `http://<Linux电脑的局域网IP>:8765/`，输入安装时设置的遥控口令。浏览器支持「添加到主屏幕」时，可以把遥控页面放到主屏幕。

局域网 HTTP 会明文传输口令、会话和输入内容。共享或不可信网络应使用可信 VPN，或由管理员部署 HTTPS 反向代理并配置 Secure Cookie、来源限制和 HTTPS 同源校验。当前程序直接提供 HTTP。禁止公网暴露和路由器端口转发。若已启用 UFW 且确实需要规则，先由管理员确认实际可信子网。以下示例只允许指定子网进入 TCP 8765：

```sh
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp
# 撤销同一条规则：
sudo ufw delete allow from 192.168.1.0/24 to any port 8765 proto tcp
```

## Wayland

在 Wayland 桌面上，`auto` 会选择 ydotool，也可在配置中设为 `wayland`。安装发行版提供的 `ydotool` 1.x（包含 `ydotoold`）和 `wl-clipboard`。Debian/Ubuntu 可通过 `apt-cache policy ydotool` 查看候选版本，Fedora 和 Arch 分别可通过 `dnf info ydotool`、`pacman -Si ydotool` 查看包信息。检查 `ydotool mousemove --help` 是否有 `--wheel`。

优先由管理员按发行版的服务和设备权限规则启动 `ydotoold`，将其 socket 限定为专用组可写（`0660`）或仅桌面用户可写（`0600`）。若包自带的守护进程没有配置私有 socket，在包版本支持的前提下，管理员可以在发行版服务的 `ExecStart` 中设置 `--socket-path=/run/ydotool/remote.sock --socket-perm=0600 --socket-own=UID:GID`。将 `UID:GID` 替换为 Linux 桌面用户的数字 ID，并用 `RuntimeDirectory=ydotool` 创建守护进程目录。路径及现有服务覆盖方式应先用 `systemctl cat ydotoold.service` 核对。只有守护进程需要接触 `/dev/uinput`，严禁对该设备设置全员写权限。参数见 [ydotool 上游文档](https://github.com/ReimuNotMoe/ydotool/blob/master/manpage/ydotoold.8.scd)。

在桌面用户的 systemd 单元覆盖文件中设置实际 socket，例如用 `systemctl --user edit ipad-tv-remote.service` 写入：

```ini
[Service]
Environment=YDOTOOL_SOCKET=/run/ydotool/remote.sock
```

用 `stat "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"` 和 `stat /run/ydotool/remote.sock` 核对两个 socket，确认后者只允许授权账号访问。在桌面终端以普通用户执行 `YDOTOOL_SOCKET=/run/ydotool/remote.sock ydotool mousemove -x 1 -y 0` 验证指针可移动，再用 `systemctl --user import-environment WAYLAND_DISPLAY XDG_SESSION_TYPE` 和 `systemctl --user restart ipad-tv-remote.service` 启动。发行版缺少兼容版本或无法配置专用权限时，退出桌面，在 GNOME 登录界面的齿轮菜单选择「Ubuntu on Xorg」或发行版提供的 X11 会话，然后按 X11 步骤安装。

## 实机验证和排错

1. 在 Linux 桌面打开遥控页面。确认未登录时页面要求输入口令，错误口令被拒绝，正确口令显示后端。确认同一局域网中的手机或平板能打开页面，从配置未允许的来源访问返回 403。终端可用 `curl -i http://127.0.0.1:8765/api/config` 检查未认证请求返回 401。
2. 用手机或平板的触控板移动指针、轻点、双指滚动，并长按约半秒后拖动。释放后确认按钮松开。再次拖住并离开遥控页面或中断 Wi‑Fi，确认服务端在正常工具可用时约 1.5 秒自动释放。
3. 在 Linux 桌面的文本编辑器中测试发送 `English 中文 🙂`、发送并回车、退格和方向键。再在浏览器中检查 Enter、Esc 和常用快捷键。文字会通过剪贴板粘贴并覆盖之前的内容。应用须支持 Ctrl+V。
4. 播放测试视频，分别试音量增减、静音和播放／暂停。在桌面声音设置核对默认输出的变化。若使用 PulseAudio，可在桌面终端用 `pactl info` 查看当前服务和默认输出。用 `playerctl status` 查看可控媒体会话。
5. 点击 YouTube 按钮确认 Firefox 或当前默认浏览器打开网站。查询 `中文 空格 & + ? #`，核对 YouTube 搜索框显示原关键词。
6. 退出桌面后重新登录，重新执行 `systemctl --user status ipad-tv-remote.service` 并从手机或平板连接。若服务未随登录启动，检查 `systemctl --user is-active graphical-session.target`。若桌面环境没有激活此 target，可先在图形终端手动启动，或用该桌面的自启动机制在导入图形环境后调用 `systemctl --user start ipad-tv-remote.service`。
7. 在手机或平板上检查旋转屏幕、软键盘、中文输入法和触控板。使用旧 iPad 时，在 Safari 普通标签页和从主屏幕启动的页面中分别测试。

查看日志和当前图形用户服务环境：

```sh
journalctl --user -u ipad-tv-remote.service -n 50 --no-pager
systemctl --user show-environment | grep -E '^(DISPLAY|XAUTHORITY|WAYLAND_DISPLAY|XDG_SESSION_TYPE|XDG_RUNTIME_DIR)='
```

出现 503 时先检查图形变量、后端工具及桌面应用焦点。浏览器打不开时在图形终端验证 `gio open https://www.youtube.com/`。若服务遇到启动限速，修正配置后执行 `systemctl --user reset-failed ipad-tv-remote.service`，再执行 `systemctl --user start ipad-tv-remote.service`。若 Linux 电脑可访问遥控页面，但手机或平板无法连接，核对绑定 IP、来源网段、主机防火墙和 Wi‑Fi 的客户端隔离。

## 验证状态

| 项目 | 结果 |
| --- | --- |
| 环境发现 | Ubuntu 20.04.6 LTS、GNOME/X11、`DISPLAY=:0`、Firefox。测试环境的 Python 为 3.7.9，用户服务使用系统 Python 3.8.10。检测到了 `pactl`、`amixer`、`gio`、`xdg-open` 和音频运行目录。 |
| 安装和联网 | 已安装 `xdotool`、`xclip` 和 `playerctl`，依赖检查通过。测试沙箱无法连接 X11、音频、媒体或 systemd 用户总线，也禁止 IPv4 监听和网卡探测。使用真实依赖启动进程已通过配置和后端初始化，随后在监听阶段退出。LAN IP、实际音频后端、用户服务和实机桌面动作仍待在图形桌面终端验证。 |
| 自动化 | 通过内存 HTTP 测试和桌面命令替身验证登录、限速、Cookie、同源、来源限制、白名单、搜索编码、拖动超时和后端命令参数。Node 合成触控事件覆盖轻点、移动、长按、释放、取消、离开页面和双指滚动。 |
| 安装脚本 | 临时用户目录测试通过，重装与卸载均保留自定义配置和口令。已用真实 X11 依赖复测临时目录安装。卸载测试的 systemd 调用使用替身。`systemd-analyze verify` 返回成功。 |
| 页面布局 | 使用 QtWebKit 和模拟的已登录会话，在 1024×768、768×1024 和 320×568 下检查了页面宽度、按钮尺寸、键盘面板的展开收起，以及网站和浏览器快捷键的请求。 |
| 移动端实机 | 手机和平板的实机测试待完成，包括登录、触控手势、中文输入和屏幕旋转。旧 iPad Safari 还需验证从主屏幕启动。 |

## 卸载和开发检查

`sh uninstall.sh` 停用用户服务并移除安装的程序，保留口令、网站配置和自定义服务覆盖文件。修改配置后用 `systemctl --user restart ipad-tv-remote.service` 使其生效。

```sh
/usr/bin/python3 -m unittest discover -s tests -v
node tests/touch.js
node --check static/app.js
sh -n install.sh
sh -n uninstall.sh
```
