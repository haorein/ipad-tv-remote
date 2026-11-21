/* ES5 and XHR for older Safari. */
(function () {
    "use strict";
    function el(id) { return document.getElementById(id); }
    function show(id, visible) {
        if (visible) { el(id).removeAttribute("hidden"); }
        else { el(id).setAttribute("hidden", ""); }
    }

    var connected = false;
    var backend = "";
    var pad = el("pad");
    var mode = "";
    var primary = null;
    var startX, startY, lastX, lastY, lastAt;
    var dx = 0, dy = 0, scroll = 0, longPress, lastHold = 0;
    var busy = false, queue = [];
    var keys = [
        ["enter", "回车"], ["escape", "Esc"], ["tab", "Tab"], ["backspace", "退格"],
        ["delete", "Delete"], ["space", "空格"], ["up", "↑"], ["down", "↓"],
        ["left", "←"], ["right", "→"], ["home", "Home"], ["end", "End"],
        ["page-up", "Page Up"], ["page-down", "Page Down"]
    ];
    var browserKeys = [
        ["address", "地址栏"],
        ["back", "返回"], ["forward", "前进"], ["new-tab", "新标签"],
        ["close-tab", "关闭标签"], ["refresh", "刷新"], ["fullscreen", "全屏"],
        ["video-fullscreen", "视频全屏"]
    ];

    function status(message) {
        var node = el("status");
        var text = message || (connected ? "已连接 · " + backend.toUpperCase() : "等待登录");
        if (node.textContent !== text) {
            node.textContent = text;
            node.className = message ? "error" : (connected ? "online" : "");
        }
    }
    function connection(active) {
        connected = active;
        show("remote", active);
        show("logout", active);
        show("login", !active);
        status();
    }
    function request(path, data, done) {
        var xhr = new XMLHttpRequest();
        var complete = false;
        var timeout = setTimeout(function () {
            finish(0, {error: "连接超时"});
            xhr.abort();
        }, 4500);
        function finish(code, result) {
            if (complete) { return; }
            complete = true;
            clearTimeout(timeout);
            done(code, result);
        }
        xhr.open(data === null ? "GET" : "POST", "/api/" + path, true);
        if (data !== null) {
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-Remote-Request", "1");
        }
        xhr.onreadystatechange = function () {
            if (xhr.readyState === 4 && !complete) {
                var result = {};
                try { result = JSON.parse(xhr.responseText); } catch (ignore) {}
                finish(xhr.status, result || {});
            }
        };
        xhr.send(data === null ? null : JSON.stringify(data));
    }
    function action(path, data, done) {
        request(path, data, function (code, result) {
            if (code === 401) { connection(false); }
            if (code !== 200) { status(result.error || "连接中断，请重试"); }
            else { status(); if (done) { done(); } }
        });
    }
    function button(parent, text, path, id, color) {
        var node = document.createElement("button");
        node.type = "button";
        node.textContent = text;
        if (color) { node.style.borderLeftColor = color; }
        node.onclick = function () { action(path, {id: id}); };
        parent.appendChild(node);
    }
    function configure() {
        request("config", null, function (code, data) {
            if (code !== 200) {
                connection(false);
                if (code !== 401) { status(data.error || "连接中断，请重试"); }
                return;
            }
            backend = data.backend;
            el("sites").textContent = "";
            data.sites.forEach(function (site) {
                button(el("sites"), site.name, "open-site", site.id, site.color);
            });
            connection(true);
        });
    }

    el("login").onsubmit = function (event) {
        event.preventDefault();
        var field = el("password");
        var value = field.value;
        field.value = "";
        request("login", {password: value}, function (code, result) {
            if (code === 200) { configure(); }
            else { status(result.error || "连接中断，请重试"); }
        });
    };
    el("logout").onclick = function () {
        cancel();
        action("logout", {}, function () { connection(false); });
    };
    el("toggle").onclick = function () {
        var expand = this.getAttribute("aria-expanded") !== "true";
        this.setAttribute("aria-expanded", String(expand));
        show("keyboard", expand);
    };
    function sendText(enter) {
        var field = el("text");
        var value = field.value;
        if (!value) { return; }
        action("text", {text: value, enter: enter}, function () {
            if (field.value === value) { field.value = ""; }
        });
    }
    el("send").onclick = function () { sendText(false); };
    el("send-enter").onclick = function () { sendText(true); };
    el("search").onsubmit = function (event) {
        event.preventDefault();
        if (el("query").value) { action("search", {query: el("query").value}); }
    };
    keys.forEach(function (key) { button(el("keys"), key[1], "key", key[0]); });
    browserKeys.forEach(function (key) { button(el("browser-keys"), key[1], "key", key[0]); });
    Array.prototype.forEach.call(el("media").getElementsByTagName("button"), function (node) {
        node.onclick = function () {
            action(node.getAttribute("data-api"), {id: node.getAttribute("data-id")});
        };
    });

    function pump() {
        if (busy || !queue.length) { return; }
        busy = true;
        request("pointer", queue.shift(), function (code, result) {
            busy = false;
            if (code !== 200) {
                queue = queue.filter(function (item) { return item.action === "up"; });
                if (mode === "drag") { cancel(); }
                if (code === 401) { connection(false); }
                status(result.error || "触控板连接中断");
            } else if (connected) {
                status();
            }
            pump();
        });
    }
    function enqueue(data) {
        if (queue.length >= 8 && data.action !== "up") { return; }
        queue.push(data);
        pump();
    }
    function center(touches) { return (touches[0].clientY + touches[1].clientY) / 2; }
    function clearLongPress() { clearTimeout(longPress); }
    function tapEnded(touches) {
        return !touches.length || touches[0].identifier !== primary;
    }
    function limit(value, max) { return Math.max(-max, Math.min(max, value)); }
    function flush() {
        var x = limit(Math.round(dx), 120);
        var y = limit(Math.round(dy), 120);
        var steps = limit(scroll < 0 ? Math.ceil(scroll) : Math.floor(scroll), 8);
        if (x || y) {
            dx -= x;
            dy -= y;
            enqueue({action: "move", dx: x, dy: y});
        }
        if (steps) {
            scroll -= steps;
            enqueue({action: "scroll", steps: steps});
        }
    }
    function finishGesture(click) {
        clearLongPress();
        var drag = mode === "drag";
        mode = "";
        pad.className = "";
        if (drag) { enqueue({action: "up"}); }
        else if (click) { enqueue({action: "click"}); }
        dx = dy = scroll = 0;
    }
    function cancel() { finishGesture(false); }
    function startScroll(touches) {
        cancel();
        mode = "scroll";
        lastY = center(touches);
    }
    function touchStart(event) {
        event.preventDefault();
        if (event.touches.length === 2) { startScroll(event.touches); return; }
        if (event.touches.length !== 1 || mode) { cancel(); return; }
        var point = event.touches[0];
        primary = point.identifier;
        startX = lastX = point.clientX;
        startY = lastY = point.clientY;
        lastAt = Date.now();
        mode = "tap";
        longPress = setTimeout(function () {
            if (mode === "tap") {
                mode = "drag";
                pad.className = "dragging";
                lastHold = Date.now();
                enqueue({action: "down"});
            }
        }, 500);
    }
    function touchMove(event) {
        event.preventDefault();
        var touches = event.touches;
        if (touches.length === 2) {
            if (mode !== "scroll") { startScroll(touches); }
            var middle = center(touches);
            scroll = limit(scroll + (middle - lastY) / 22, 8);
            lastY = middle;
            return;
        }
        if (!mode || mode === "scroll" || touches.length !== 1 || tapEnded(touches)) { return; }
        var point = touches[0];
        if (mode === "tap") {
            if (Math.abs(point.clientX - startX) < 5 && Math.abs(point.clientY - startY) < 5) { return; }
            mode = "move";
            clearLongPress();
        }
        var now = Date.now();
        var px = point.clientX - lastX;
        var py = point.clientY - lastY;
        var speed = (Math.abs(px) + Math.abs(py)) / Math.max(8, now - lastAt);
        var gain = 1.1 + Math.min(.55, speed * .12);
        dx = limit(dx + px * gain, 120);
        dy = limit(dy + py * gain, 120);
        lastX = point.clientX;
        lastY = point.clientY;
        lastAt = now;
    }
    function touchEnd(event) {
        event.preventDefault();
        if (mode === "scroll" && event.touches.length === 2) {
            lastY = center(event.touches);
        } else if (mode === "scroll" || tapEnded(event.touches)) {
            flush();
            finishGesture(mode === "tap");
        }
    }

    var passive = false;
    try {
        var options = Object.defineProperty({}, "passive", {get: function () { passive = true; }});
        window.addEventListener("remote-passive-check", cancel, options);
        window.removeEventListener("remote-passive-check", cancel, options);
    } catch (ignore) {}
    var touchOptions = passive ? {passive: false} : false;
    pad.addEventListener("touchstart", touchStart, touchOptions);
    pad.addEventListener("touchmove", touchMove, touchOptions);
    pad.addEventListener("touchend", touchEnd, touchOptions);
    pad.addEventListener("touchcancel", function (event) { event.preventDefault(); cancel(); }, touchOptions);
    pad.addEventListener("contextmenu", function (event) { event.preventDefault(); }, false);
    document.addEventListener("visibilitychange", function () { if (document.hidden) { cancel(); } }, false);
    window.addEventListener("pagehide", cancel, false);
    window.addEventListener("blur", cancel, false);
    window.addEventListener("offline", cancel, false);
    setInterval(function () {
        if (!connected || busy) { return; }
        flush();
        if (mode === "drag" && Date.now() - lastHold > 400) {
            lastHold = Date.now();
            enqueue({action: "hold"});
        }
    }, 40);
    configure();
}());
