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
const FAULT_TEXT = {
  "daq-no-response": "DAQ will not answer the next query",
  "daq-offset": "DAQ readings are now 0.5 V high",
  "psu-error": "PSU pushed error -222 into its queue",
};
const FAULT_BADGE = { no_response: "no response", offset: "offset +0.5 V" };
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

const volts = (v) => `${v.toFixed(3)} V`;
const amps = (a) => `${a.toFixed(3)} A`;

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
  byTestId("daq-display").textContent = daq.last
    ? `${daq.last.value >= 0 ? "+" : ""}${daq.last.value.toFixed(4)} VDC  ·  CH ${daq.last.channel}`
    : "– – –";
  for (const number of RELAYS) {
    const relay = $(`#relay-${number}`);
    relay.classList.toggle("closed", daq.closed.includes(number));
    relay.classList.toggle("read", daq.last?.channel === number);
  }
  showBadges("daq", daq);
}

function print(text, kind = "") {
  const line = el("div", { class: `line ${kind}` }, text);
  output.append(line);
  output.scrollTop = output.scrollHeight;
}

// --- running ------------------------------------------------------------------

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function enqueue(job) {
  queue = queue.then(job).catch((error) => print(`internal error: ${error.message}`, "error"));
  return queue;
}

function setTarget(name) {
  target = name;
  for (const button of document.querySelectorAll("[data-target]")) {
    button.setAttribute("aria-pressed", String(button.dataset.target === name));
  }
  $("#prompt-label").textContent = `${name}>`;
}

function send(to, line) {
  enqueue(async () => {
    print(`${to}> ${line}`, "command");
    const result = await sim.send(to, line);
    if (result.timeout) {
      await sleep(TIMEOUT_MS);
      print(TIMEOUT_TEXT, "timeout");
    } else if (result.response === null) {
      print("(ok, no response)", "quiet");
    } else {
      print(result.response, "response");
    }
    render(result.state);
  });
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

function wire() {
  const chips = $("#chips");
  CHIPS.forEach((chip, i) => {
    const button = el("button", { type: "button", "data-testid": `chip-${i + 1}`, disabled: "" });
    button.append(el("span", { class: "step" }, String(i + 1)), el("span", {}, chip.label),
                  el("code", {}, chip.steps.map(([to, line]) => `${to}> ${line}`).join("  ·  ")));
    button.addEventListener("click", () => {
      for (const [to, line] of chip.steps) {
        setTarget(to);
        send(to, line);
      }
    });
    chips.append(button);
  });

  for (const button of document.querySelectorAll("[data-target]")) {
    button.addEventListener("click", () => setTarget(button.dataset.target));
  }
  $("#prompt").addEventListener("submit", (event) => {
    event.preventDefault();
    const line = input.value.trim();
    if (!line) return;
    history.push(line);
    historyAt = history.length;
    input.value = "";
    send(target, line);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    historyAt = Math.max(0, Math.min(history.length, historyAt + (event.key === "ArrowUp" ? -1 : 1)));
    input.value = history[historyAt] ?? "";
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
    $("#fallback-reason").textContent = `(${error.message})`;
    byTestId("fallback").hidden = false;
    return;
  }
  render(sim.state());
  $("#version").textContent = sim.version;
  for (const control of controls()) control.disabled = false;
  document.body.classList.remove("loading");
  status.textContent = "ready – try the buttons below";
  print("# scpi-sim is running in your browser. Pick an instrument and type, or use the buttons.", "note");
}

start();
