// The page: draws the instrument fronts from the simulator's state and runs the terminal.
// Every command, fault and reset goes through one queue, so output keeps the click order.

import { loadSim } from "./sim.js";

const TIMEOUT_MS = 2000;
const TIMEOUT_TEXT = "timeout – no response (VI_ERROR_TMO in PyVISA)";
const POWER_ON = "INST OUT1; VOLT 12; OUTP:SEL ON;:OUTP:GEN ON";
const CHIPS = [
  { label: "Who are you?", steps: [["psu", "*IDN?"]] },
  { label: "Channel 1 to 12 V, output on", steps: [["psu", POWER_ON]] },
  { label: "Measure the current", steps: [["psu", "MEAS:CURR?"]] },
  { label: "DAQ measures the 12 V rail", steps: [["daq", "MEAS:VOLT:DC? (@101)"]] },
  { label: "Drop to 9 V and measure again", steps: [["psu", "VOLT 9"], ["daq", "MEAS:VOLT:DC? (@101)"]] },
  { label: "Send garbage, read the error queue", steps: [["psu", "FOO"], ["psu", "SYST:ERR?"]] },
];
const SCOPE_CHIPS = [
  { label: "Measure the sine", steps: [["scope", ":MEAS:ITEM? FREQ,CHAN1"], ["scope", ":MEAS:ITEM? VPP,CHAN1"]] },
  { label: "Read the waveform, a binary block", steps: [["scope", ":WAV:PRE?"], ["scope", ":WAV:DATA?"]] },
  { label: "Too sensitive: it clips, Vpp reads wrong", steps: [["scope", ":CHAN1:SCAL 0.1"], ["scope", ":MEAS:ITEM? VPP,CHAN1"]] },
  { label: "Back to 500 mV/div, 12 V rail on CH2", steps: [["scope", ":CHAN1:SCAL 0.5"], ["scope", ":CHAN2:SCAL 5"], ["scope", ":CHAN2:DISP ON"], ["psu", POWER_ON]] },
  { label: "Trigger above the signal: the query hangs", steps: [["scope", ":TRIG:EDG:LEV 1.5"], ["scope", ":SING"], ["scope", "*OPC?"]] },
  { label: "Lower the level: it fires, run again", steps: [["scope", ":TRIG:EDG:LEV 0"], ["scope", "*OPC?"], ["scope", ":RUN"]] },
  { label: "Catch a board's power-on: inrush on CH3, 3.3 V rail on CH4",
    steps: [["psu", "INST OUT3; VOLT 12; CURR 3; OUTP:SEL OFF"],
            ["scope", ":CHAN1:DISP OFF;:CHAN2:DISP OFF;:CHAN3:DISP ON;:CHAN3:SCAL 0.05;:CHAN3:OFFS -0.15;:CHAN4:DISP ON;:CHAN4:SCAL 1;:CHAN4:OFFS -2"],
            ["scope", ":TIM:SCAL 0.002;:TIM:OFFS 0.008;:TRIG:EDG:SOUR CHAN3;:TRIG:EDG:LEV 0.02;:SING"],
            ["psu", "OUTP:SEL ON;:OUTP:GEN ON"]] },
  { label: "Read the inrush peak (100 mV/A) and the rail", steps: [["scope", ":MEAS:ITEM? VMAX,CHAN3"], ["scope", ":MEAS:ITEM? VMAX,CHAN4"]] },
];
const FAULT_TEXT = {
  "daq-no-response": "DAQ will not answer the next query",
  "daq-offset": "DAQ readings are now 0.5 V high",
  "psu-error": "PSU pushed error -222 into its queue",
  "scope-no-trigger": "the scope's trigger no longer fires",
  "scope-noise": "the scope's inputs now see 100 mV of noise",
  "scope-probe-off": "the probe fell off CH1: 0 V at the input",
};
const FAULT_BADGE = { no_response: "no response", offset: "offset +0.5 V", no_trigger: "no trigger", noise: "noise",
                      signal: "probe off" };
const SCOPE_STATUS = { triggered: "T'D", auto: "AUTO", wait: "WAIT", stop: "STOP" };
const TRACE_COLORS = { 1: "#f2d648", 2: "#4fd2e8", 3: "#e87fd0", 4: "#5fa8ff" };
const SCREEN_ROWS = 8;
const SCREEN_RANGE = 5.2; // divisions above and below center that the screen shows: the converter's whole range
const LIVE_MS = 300;
const RELAYS = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110];

const $ = (selector) => document.querySelector(selector);
const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`);
const status = byTestId("status");
const output = byTestId("terminal-output");
const input = byTestId("terminal-input");
const controls = () => document.querySelectorAll(".console button, .console input");

let sim = null;
let target = "psu";
let queue = Promise.resolve();
const history = [];
let historyAt = 0;
let draft = ""; // what was typed before ↑ went into the history
let busy = 0;
let scopeVisible = true;

// --- drawing ------------------------------------------------------------------

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
}

function buildFronts() {
  const channels = $("#psu-channels");
  for (let n = 1; n <= 4; n++) {
    const channel = el("div", { class: "channel", id: `psu-ch${n}` });
    const head = el("div", { class: "channel-head" });
    head.append(el("span", { class: "led", "data-testid": `psu-ch${n}-led`, "data-on": "false" }),
                el("span", {}, `CH${n}`), el("span", { class: "mode", id: `psu-ch${n}-mode` }, "OFF"));
    const actual = el("div", { class: "actual" });
    actual.append(el("span", { "data-testid": `psu-ch${n}-out-v` }, "0.000 V"),
                  el("span", { "data-testid": `psu-ch${n}-out-a` }, "0.000 A"));
    channel.append(head, actual, el("div", { class: "setting", id: `psu-ch${n}-set` }, "set –"));
    channels.append(channel);
  }
  const relays = $("#daq-relays");
  for (const number of RELAYS) relays.append(el("span", { class: "relay", id: `relay-${number}` }, String(number)));
}

const OVERLOAD = 9.9e37; // what a multimeter returns for an overrange reading
const volts = (v) => `${v.toFixed(3)} V`;
const amps = (a) => `${a.toFixed(3)} A`;

// the DAQ's display: DC volts with four decimals, resistance in Ω, kΩ or MΩ
function reading({ function: kind, value }) {
  if (Math.abs(value) >= OVERLOAD) return kind === "RES" ? "OVLD Ω" : "OVLD VDC";
  if (kind === "RES") {
    const [factor, prefix] = [[1e6, "M"], [1e3, "k"], [1, ""]].find(([f]) => Math.abs(value) >= f) ?? [1, ""];
    return `${(value / factor).toFixed(4)} ${prefix}Ω`;
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)} VDC`;
}

function showBadges(prefix, state) {
  byTestId(`${prefix}-err`).hidden = !state.error;
  const badge = byTestId(`fault-badge-${prefix}`);
  badge.hidden = state.faults.length === 0;
  badge.textContent = state.faults.map((kind) => `fault: ${FAULT_BADGE[kind] ?? kind}`).join(" · ");
}

function render(state) {
  const psu = state.psu;
  $("#psu-general").classList.toggle("on", psu.general);
  psu.channels.forEach((ch, i) => {
    const n = i + 1;
    const on = ch.mode !== "OFF";
    byTestId(`psu-ch${n}-led`).dataset.on = String(on);
    byTestId(`psu-ch${n}-out-v`).textContent = volts(ch.out_v);
    byTestId(`psu-ch${n}-out-a`).textContent = amps(ch.out_a);
    $(`#psu-ch${n}-mode`).textContent = ch.mode;
    $(`#psu-ch${n}-set`).textContent = `set ${volts(ch.set_v)} · ${amps(ch.set_a)}${ch.active && !psu.general ? " · armed" : ""}`;
    $(`#psu-ch${n}`).classList.toggle("selected", psu.selected === n);
    $(`#psu-ch${n}`).classList.toggle("on", on);
  });
  showBadges("psu", psu);

  const daq = state.daq;
  byTestId("daq-display").textContent = daq.last ? `${reading(daq.last)}  ·  CH ${daq.last.channel}` : "– – –";
  for (const number of RELAYS) {
    const relay = $(`#relay-${number}`);
    relay.classList.toggle("closed", daq.closed.includes(number));
    relay.classList.toggle("read", daq.last?.channel === number);
  }
  showBadges("daq", daq);
  renderScope(state.scope);
}

function drawScreen(scope) {
  const canvas = $("#scope-screen");
  const ratio = window.devicePixelRatio || 1;
  const width = Math.round(canvas.clientWidth * ratio) || canvas.width;
  const height = Math.round(canvas.clientHeight * ratio) || canvas.height;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const g = canvas.getContext("2d");
  const yOf = (divisions) => height / 2 - (divisions / SCREEN_RANGE) * (height / 2);
  g.clearRect(0, 0, width, height);

  // beyond the eight divisions: the headroom a real screen does not show, but the data has
  g.fillStyle = "rgba(255,255,255,.05)";
  g.fillRect(0, 0, width, yOf(SCREEN_ROWS / 2));
  g.fillRect(0, yOf(-SCREEN_ROWS / 2), width, height - yOf(-SCREEN_ROWS / 2));

  g.lineWidth = Math.max(1, ratio * 0.5);
  g.strokeStyle = "rgba(125,255,181,.16)";
  g.beginPath();
  for (let column = 0; column <= scope.columns; column++) {
    const x = Math.round((column / scope.columns) * width) + 0.5;
    g.moveTo(x, yOf(SCREEN_ROWS / 2));
    g.lineTo(x, yOf(-SCREEN_ROWS / 2));
  }
  for (let row = -SCREEN_ROWS / 2; row <= SCREEN_ROWS / 2; row++) {
    const y = Math.round(yOf(row)) + 0.5;
    g.moveTo(0, y);
    g.lineTo(width, y);
  }
  g.stroke();

  g.lineWidth = 1.5 * ratio;
  g.lineJoin = "round";
  for (const trace of scope.traces) {
    g.strokeStyle = TRACE_COLORS[trace.channel];
    g.beginPath();
    trace.divisions.forEach((value, i) => {
      const x = (i / (trace.divisions.length - 1)) * width;
      if (i === 0) g.moveTo(x, yOf(value));
      else g.lineTo(x, yOf(value));
    });
    g.stroke();
  }

  // the trigger: the level on the left, the moment at the top
  g.fillStyle = TRACE_COLORS[scope.trigger.source];
  const level = Math.max(-SCREEN_RANGE, Math.min(SCREEN_RANGE, scope.trigger.divisions));
  const size = 7 * ratio;
  g.beginPath();
  g.moveTo(0, yOf(level) - size);
  g.lineTo(size * 1.4, yOf(level));
  g.lineTo(0, yOf(level) + size);
  g.fill();
  g.fillStyle = "#e7eaee";
  g.beginPath();
  g.moveTo(width / 2 - size, 0);
  g.lineTo(width / 2, size * 1.4);
  g.lineTo(width / 2 + size, 0);
  g.fill();
}

function engineering(value, unit) {
  if (value === 0) return `0 ${unit}`;
  const steps = [[1, ""], [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"]];
  const [factor, prefix] = steps.find(([f]) => Math.abs(value) >= f) ?? steps[steps.length - 1];
  return `${+(value / factor).toPrecision(3)} ${prefix}${unit}`;
}

function renderScope(scope) {
  const status = byTestId("scope-status");
  status.textContent = SCOPE_STATUS[scope.status];
  status.dataset.state = scope.status;
  const channels = scope.traces.map((t) => `CH${t.channel} ${engineering(t.scale, "V")}/div`);
  const slope = { POS: "↑", NEG: "↓", EITHER: "↕" }[scope.trigger.slope];
  byTestId("scope-readout").textContent = [
    ...channels, `${engineering(scope.time_scale, "s")}/div`,
    `trigger CH${scope.trigger.source} ${slope} ${engineering(scope.trigger.level, "V")}`,
  ].join("  ·  ");
  drawScreen(scope);
  scopeRunning = scope.status !== "stop";
  showBadges("scope", scope);
}

// a running scope keeps capturing: redraw while it is on screen, nothing else is going on,
// and the visitor has not asked for less motion
let scopeRunning = true;
function live() {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (sim && scopeRunning && scopeVisible && !busy && !document.hidden && !still) {
    enqueue(async () => renderScope(sim.scopeState()));
  }
}

function print(text, kind = "") {
  const line = el("div", { class: `line ${kind}` }, text);
  output.append(line);
  output.scrollTop = output.scrollHeight;
}

// --- running ------------------------------------------------------------------

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// a Python exception arrives with its whole traceback; the last line says what went wrong
function lastLine(error) {
  const lines = String(error?.message ?? error).trim().split("\n");
  return lines[lines.length - 1].trim();
}

function enqueue(job) {
  queue = queue.then(job).catch((error) => print(`internal error: ${lastLine(error)}`, "error"));
  return queue;
}

function setTarget(name) {
  target = name;
  for (const button of document.querySelectorAll("[data-target]")) {
    button.setAttribute("aria-pressed", String(button.dataset.target === name));
  }
  $("#prompt-label").textContent = `${name}>`;
}

// switch: a tour button moves the prompt to each step's instrument when that step runs
function send(to, line, { switch: switchTarget = false } = {}) {
  busy++;
  enqueue(async () => {
    if (switchTarget) setTarget(to);
    const result = await sim.send(to, line);
    print(`${to}> ${result.line}`, "command"); // as the instrument got it: cut, no control characters
    if (result.timeout) {
      await sleep(TIMEOUT_MS);
      print(TIMEOUT_TEXT, "timeout");
    } else if (result.response == null) { // Python None arrives as undefined
      print("(ok, no response)", "quiet");
    } else {
      print(result.response, "response");
    }
    render(result.state);
  }).finally(() => { busy--; });
}

function inject(fault) {
  enqueue(async () => {
    render(await sim.inject(fault));
    print(`# fault: ${FAULT_TEXT[fault]}`, "note");
  });
}

function reset() {
  enqueue(async () => {
    render(await sim.reset());
    print("# bench reset: outputs off, faults cleared, error queues empty", "note");
  });
}

function addChips(parent, list, first) {
  list.forEach((chip, i) => {
    const button = el("button", { type: "button", "data-testid": `chip-${first + i}`, disabled: "" });
    button.append(el("span", { class: "step" }, String(first + i)), el("span", {}, chip.label),
                  el("code", {}, chip.steps.map(([to, line]) => `${to}> ${line}`).join("  ·  ")));
    button.addEventListener("click", () => {
      for (const [to, line] of chip.steps) send(to, line, { switch: true });
    });
    parent.append(button);
  });
}

function wire() {
  addChips($("#chips"), CHIPS, 1);
  addChips($("#scope-chips"), SCOPE_CHIPS, CHIPS.length + 1);

  for (const button of document.querySelectorAll("[data-target]")) {
    button.addEventListener("click", () => setTarget(button.dataset.target));
  }
  $("#prompt").addEventListener("submit", (event) => {
    event.preventDefault();
    const line = input.value.trim();
    if (!line) return;
    history.push(line);
    historyAt = history.length;
    draft = "";
    input.value = "";
    send(target, line);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    if (historyAt === history.length) draft = input.value;
    historyAt = Math.max(0, Math.min(history.length, historyAt + (event.key === "ArrowUp" ? -1 : 1)));
    input.value = historyAt === history.length ? draft : history[historyAt];
  });
  for (const button of document.querySelectorAll("[data-fault]")) {
    button.addEventListener("click", () => inject(button.dataset.fault));
  }
  byTestId("reset").addEventListener("click", reset);
  byTestId("retry").addEventListener("click", () => location.reload());
}

async function start() {
  buildFronts();
  wire();
  try {
    sim = await loadSim((text) => { status.textContent = text; });
  } catch (error) {
    document.body.classList.replace("loading", "failed");
    status.textContent = "could not start";
    $("#fallback-reason").textContent = `(${lastLine(error)})`;
    byTestId("fallback").hidden = false;
    return;
  }
  render(sim.state());
  $("#version").textContent = sim.version;
  for (const control of controls()) control.disabled = false;
  document.body.classList.remove("loading");
  status.textContent = "ready – try the buttons below";
  print("# scpi-sim is running in your browser. Pick an instrument and type, or use the buttons.", "note");
  new IntersectionObserver(([entry]) => { scopeVisible = entry.isIntersecting; }).observe($("#scope-screen"));
  setInterval(live, LIVE_MS);
  window.addEventListener("resize", () => enqueue(async () => renderScope(sim.scopeState())));
}

start();
