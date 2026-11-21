/* Exercise the ES5 touch state machine with synthetic events and XHR. */
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

function node() {
    return {
        events: {},
        style: {},
        addEventListener: function (event, fn) { this.events[event] = fn; },
        setAttribute: function () {},
        removeAttribute: function () {},
        appendChild: function () {},
        getElementsByTagName: function () { return []; }
    };
}
function XHR() {
    this.open = function (_, path) { this.path = path; };
    this.setRequestHeader = function () {};
    this.send = function (body) {
        if (this.path === "/api/pointer") { actions.push(JSON.parse(body)); }
        this.responseText = '{"backend":"x11","sites":[]}';
        this.readyState = 4;
        this.status = 200;
        this.onreadystatechange();
    };
}

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
