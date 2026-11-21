/* Exercise touch gestures and site selection with synthetic events and XHR. */
"use strict";
var assert = require("assert");
var fs = require("fs");
var vm = require("vm");
var actions = [];
var nodes = {};
var timers = {};
var clock = 0;
var nextTimer = 0;
var windowEvents = {};
var interval;
var siteRequests = [];
var configCode = 200, reads = 0;
var mediaRequests = [];
var delayPointer = false, pendingPointer;
var config = {backend: "x11", active_site: null, sites: [
    {id: "first", name: "视频 A", searchable: true},
    {id: "second", name: "视频 B", searchable: true},
    {id: "third", name: "视频 C", searchable: false}
]};

function node() {
    var attributes = {}, children = [], text = "";
    var element = {
        events: {},
        style: {},
        addEventListener: function (event, fn) { this.events[event] = fn; },
        setAttribute: function (name, value) { attributes[name] = String(value); },
        getAttribute: function (name) { return name in attributes ? attributes[name] : null; },
        removeAttribute: function (name) { delete attributes[name]; },
        appendChild: function (child) { children.push(child); },
        getElementsByTagName: function () { return children; }
    };
    Object.defineProperty(element, "textContent", {
        get: function () { return text; },
        set: function (value) { text = value; children = []; }
    });
    return element;
}
function XHR() {
    this.open = function (_, path) { this.path = path; };
    this.setRequestHeader = function () {};
    this.respond = function (status, data) {
        this.responseText = JSON.stringify(data);
        this.readyState = 4;
        this.status = status;
        this.onreadystatechange();
    };
    this.send = function (body) {
        if (this.path === "/api/config") {
            reads += 1;
            this.respond(configCode, configCode === 200 ? config : {});
            return;
        }
        if (this.path === "/api/pointer") {
            actions.push(JSON.parse(body));
            if (delayPointer) { pendingPointer = this; return; }
        }
        if (this.path === "/api/media") { mediaRequests.push(JSON.parse(body)); }
        if (this.path === "/api/open-site" || this.path === "/api/search") {
            this.body = JSON.parse(body);
            siteRequests.push(this);
        } else {
            this.respond(200, {});
        }
    };
}

var fullscreen = node();
fullscreen.setAttribute("data-api", "media");
fullscreen.setAttribute("data-id", "video-fullscreen");
nodes.media = node();
nodes.media.appendChild(fullscreen);
vm.runInNewContext(fs.readFileSync("static/app.js", "utf8"), {
    document: {
        getElementById: function (id) { return nodes[id] || (nodes[id] = node()); },
        createElement: node,
        addEventListener: function () {}
    },
    window: {
        addEventListener: function (event, fn) { windowEvents[event] = fn; },
        removeEventListener: function (event) { delete windowEvents[event]; }
    },
    XMLHttpRequest: XHR,
    Date: {now: function () { return clock; }},
    setTimeout: function (fn, ms) {
        timers[++nextTimer] = {at: clock + ms, fn: fn};
        return nextTimer;
    },
    clearTimeout: function (timer) { delete timers[timer]; },
    setInterval: function (fn, ms) { assert.strictEqual(ms, 40); interval = fn; }
});

function advance(ms) {
    clock += ms;
    Object.keys(timers).forEach(function (id) {
        var timer = timers[id];
        if (timer && timer.at <= clock) {
            delete timers[id];
            timer.fn();
        }
    });
}
function point(id, x, y) { return {identifier: id, clientX: x, clientY: y}; }
function touch(name, points) {
    var prevented = false;
    nodes.pad.events["touch" + name]({
        touches: points,
        preventDefault: function () { prevented = true; }
    });
    assert(prevented);
}
function expect(expected) {
    assert.deepStrictEqual(actions.splice(0).map(function (event) { return event.action; }), expected);
}

var initial = point(1, 30, 30);
touch("start", [initial]);
touch("end", []);
expect(["click"]);

touch("start", [initial]);
touch("move", [point(1, 75, 40)]);
assert.strictEqual(actions.length, 0);
interval();
assert(actions[0].dx > 0 && actions[0].dx <= 120);
touch("end", []);
expect(["move"]);

touch("start", [initial]);
advance(500);
advance(401);
interval();
touch("move", [point(1, 50, 30)]);
touch("end", []);
expect(["down", "hold", "move", "up"]);

touch("start", [initial]);
advance(500);
touch("cancel", []);
expect(["down", "up"]);

touch("start", [initial]);
advance(500);
windowEvents.pagehide();
expect(["down", "up"]);

touch("start", [initial]);
touch("start", [initial, point(2, 60, 30)]);
touch("move", [point(1, 30, 78), point(2, 60, 78)]);
interval();
assert.strictEqual(actions[0].steps, 2);
touch("end", [initial]);
touch("end", []);
expect(["scroll"]);
console.log("Touch simulation passed");

delayPointer = true;
touch("start", [initial]);
touch("move", [point(1, 80, 45)]);
touch("end", []);
fullscreen.onclick();
assert.deepStrictEqual(mediaRequests, []);
delayPointer = false;
pendingPointer.respond(200, {});
expect(["move"]);
assert.deepStrictEqual(mediaRequests.splice(0), [{id: "video-fullscreen"}]);
console.log("Video fullscreen waits for the final pointer move");

function siteButton(index) { return nodes.sites.getElementsByTagName("button")[index]; }
function submitted(id) { nodes[id].onsubmit({preventDefault: function () {}}); }
function siteReply(path, data, code) {
    assert.strictEqual(siteRequests.length, 1);
    var req = siteRequests.shift();
    assert.strictEqual(req.path, "/api/" + path);
    assert.deepStrictEqual(req.body, data);
    req.respond(code, code === 200 ? {} : {error: "请求失败"});
}
function assertSite(index) {
    assert.strictEqual(nodes.search.getAttribute("hidden"), null);
    assert.strictEqual(nodes["search-label"].textContent, "在「" + config.sites[index].name + "」中搜索");
    config.sites.forEach(function (_, i) {
        assert.strictEqual(siteButton(i).getAttribute("aria-pressed"), String(i === index));
    });
}

assert.strictEqual(nodes.search.getAttribute("hidden"), "");
siteButton(0).onclick();
siteButton(1).onclick();
assert.strictEqual(siteButton(1).disabled, true);
assert.strictEqual(nodes.search.getAttribute("hidden"), "");
siteReply("open-site", {id: "first"}, 200);
assertSite(0);
var keywords = "中文 & + / #";
nodes.query.value = keywords;
submitted("search");
submitted("search");
siteButton(1).onclick();
siteReply("search", {id: "first", query: keywords}, 200);
siteButton(1).onclick();
submitted("search");
siteReply("open-site", {id: "second"}, 503);
assertSite(0);
assert.strictEqual(nodes.query.value, keywords);
siteButton(1).onclick();
siteReply("open-site", {id: "second"}, 200);
assertSite(1);
assert.strictEqual(nodes.query.value, "");
nodes.query.value = keywords;
submitted("search");
config.active_site = "third"; // Another remote opened the third site.
siteReply("search", {id: "second", query: keywords}, 409);
assertSite(2);
assert.strictEqual(nodes["search-help"].getAttribute("hidden"), null);
assert.strictEqual(nodes["search-submit"].textContent, "粘贴到搜索框并回车");
assert.strictEqual(nodes.query.value, "");
assert.strictEqual(siteRequests.length, 0);
nodes.query.value = keywords;
submitted("search");
siteReply("search", {id: "third", query: keywords, mode: "paste"}, 200);

siteButton(1).onclick();
nodes.logout.onclick();
siteReply("open-site", {id: "second"}, 200); // Ignore a late site reply after logout.
assert.strictEqual(nodes.search.getAttribute("hidden"), "");
assert.strictEqual(nodes.query.value, "");
config.active_site = "second";
submitted("login");
assertSite(1);
assert.strictEqual(nodes["search-help"].getAttribute("hidden"), "");
console.log("Site search simulation passed");

// A gesture in flight and a media action queued behind it must not replay after resume.
delayPointer = true;
touch("start", [initial]);
touch("end", []);
fullscreen.onclick();
pendingPointer.respond(0, {});
delayPointer = false;
assert.strictEqual(nodes.reconnect.getAttribute("hidden"), null);
assert.strictEqual(nodes.login.getAttribute("hidden"), "");
assert.strictEqual(nodes.remote.getAttribute("hidden"), "");
var beforeRetry = reads;
configCode = 0;
advance(2000);
assert.strictEqual(reads, beforeRetry + 1);
configCode = 200;
advance(4000);
assertSite(1);
assert.strictEqual(nodes.reconnect.getAttribute("hidden"), "");
assert.strictEqual(nodes.remote.getAttribute("hidden"), null);
expect(["click"]);
assert.deepStrictEqual(mediaRequests, []);
windowEvents.offline();
configCode = 401;
windowEvents.online();
assert.strictEqual(nodes.reconnect.getAttribute("hidden"), "");
assert.strictEqual(nodes.login.getAttribute("hidden"), null);
assert.strictEqual(nodes.remote.getAttribute("hidden"), "");
console.log("Resume retries read-only requests, discards input and handles expired sessions");
