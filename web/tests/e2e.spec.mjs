// The demo page in a real browser: Pyodide, the instrument fronts, the terminal queue,
// faults, load failure, no third-party requests, narrow screens.
import { test, expect } from "@playwright/test";

const id = (page, testId) => page.getByTestId(testId);

async function ready(page) {
  await page.goto("/");
  await expect(id(page, "status")).toContainText("ready", { timeout: 60_000 });
}

async function lines(page) {
  return (await id(page, "terminal-output").locator(".line").allInnerTexts()).filter((l) => !l.startsWith("#"));
}

test("tour", async ({ page }) => {
  await ready(page);
  for (const n of [1, 2, 3, 4]) await id(page, `chip-${n}`).click();
  await expect(id(page, "terminal-output")).toContainText(/\+1\.(199|200)\d*E\+01/);
  await expect(id(page, "terminal-output")).toContainText("Rohde&Schwarz,HMP4040");
  await expect(id(page, "terminal-output")).toContainText("0.500");
  await expect(id(page, "psu-ch1-led")).toHaveAttribute("data-on", "true");
  await expect(id(page, "psu-ch1-out-a")).toContainText("0.500");
  expect(await lines(page)).toContain("(ok, no response)");
  await expect(id(page, "daq-display")).toContainText(/\+(11\.99|12\.00)\d* VDC/);

  await id(page, "chip-5").click();
  await expect(id(page, "daq-display")).toContainText(/\+(8\.99|9\.00)\d* VDC/);
  await id(page, "chip-6").click();
  await expect(id(page, "terminal-output")).toContainText('-113,"Undefined header"');
  await expect(id(page, "psu-err")).toBeHidden();
});

test("typing a command", async ({ page }) => {
  await ready(page);
  await id(page, "target-daq").click();
  await id(page, "terminal-input").fill("*IDN?");
  await id(page, "terminal-input").press("Enter");
  await expect(id(page, "terminal-output")).toContainText("daq> *IDN?");
  await expect(id(page, "terminal-output")).toContainText("Keysight Technologies,DAQ970A");
  await id(page, "terminal-input").press("ArrowUp");
  await expect(id(page, "terminal-input")).toHaveValue("*IDN?");
});

test("faults and reset", async ({ page }) => {
  await ready(page);
  await id(page, "chip-2").click();
  await id(page, "fault-daq-offset").click();
  await id(page, "chip-4").click();
  await expect(id(page, "daq-display")).toContainText(/\+12\.(49|50)\d* VDC/);
  await expect(id(page, "fault-badge-daq")).toBeVisible();

  await id(page, "fault-psu-error").click();
  await expect(id(page, "psu-err")).toBeVisible();

  await id(page, "fault-daq-no-response").click();
  const started = Date.now();
  await id(page, "chip-4").click();
  await expect(id(page, "terminal-output")).toContainText("VI_ERROR_TMO", { timeout: 10_000 });
  expect(Date.now() - started).toBeGreaterThanOrEqual(1900);

  await id(page, "reset").click();
  await expect(id(page, "psu-ch1-led")).toHaveAttribute("data-on", "false");
  await expect(id(page, "fault-badge-daq")).toBeHidden();
  await expect(id(page, "psu-err")).toBeHidden();
});

test("queued clicks keep their order", async ({ page }) => {
  await ready(page);
  await id(page, "fault-daq-no-response").click();
  await id(page, "chip-1").click();
  await id(page, "chip-4").click();
  await id(page, "chip-3").click();
  await expect(id(page, "terminal-output")).toContainText("MEAS:CURR?", { timeout: 10_000 });
  await expect.poll(async () => (await lines(page)).length, { timeout: 10_000 }).toBe(6);
  const out = await lines(page);
  expect(out[0]).toBe("psu> *IDN?");
  expect(out[1]).toMatch(/^Rohde&Schwarz,HMP4040/);
  expect(out[2]).toBe("daq> MEAS:VOLT:DC? (@101)");
  expect(out[3]).toContain("VI_ERROR_TMO");
  expect(out[4]).toBe("psu> MEAS:CURR?");
  expect(out[5]).toMatch(/^0\.000$/);
});

test("long input is cut and harmless", async ({ page }) => {
  await ready(page);
  await id(page, "terminal-input").fill("VOLT " + "9".repeat(10_000));
  expect((await id(page, "terminal-input").inputValue()).length).toBeLessThanOrEqual(256);
  await id(page, "terminal-input").press("Enter");
  await id(page, "chip-1").click();
  await expect(id(page, "terminal-output")).toContainText("Rohde&Schwarz,HMP4040");
  await expect(id(page, "status")).toContainText("ready");
});

test("a failed load shows the fallback", async ({ page }) => {
  await page.route("**/*.wasm", (route) => route.abort());
  await page.goto("/");
  await expect(id(page, "fallback")).toBeVisible({ timeout: 30_000 });
  await expect(id(page, "retry")).toBeVisible();
  await expect(id(page, "chip-1")).toBeDisabled();
  await expect(id(page, "fallback").locator("img")).toHaveJSProperty("complete", true);
  expect(await id(page, "fallback").locator("img").evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);
});

test("no requests to other hosts", async ({ page, baseURL }) => {
  const foreign = [];
  page.on("request", (request) => {
    if (!request.url().startsWith(new URL(baseURL).origin) && !request.url().startsWith("data:")) foreign.push(request.url());
  });
  await ready(page);
  await id(page, "chip-1").click();
  await expect(id(page, "terminal-output")).toContainText("HMP4040");
  expect(foreign).toEqual([]);
});

for (const colorScheme of ["light", "dark"]) {
  test(`narrow screen, ${colorScheme}`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await page.emulateMedia({ colorScheme });
    await ready(page);
    await id(page, "chip-2").click();
    await expect(id(page, "psu-ch1-led")).toHaveAttribute("data-on", "true");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  });
}

test("a .wasm 404 shows the fallback quickly", async ({ page }) => {
  await page.route("**/*.wasm", (route) => route.fulfill({ status: 404, body: "not found" }));
  await page.goto("/");
  await expect(id(page, "fallback")).toBeVisible({ timeout: 15_000 });
});

test("only versioned files are cached for good", async ({ request }) => {
  const manifest = await (await request.get("/py/manifest.json")).json();
  const runtime = await request.get(`/pyodide/${manifest.pyodide}/pyodide.asm.wasm`);
  expect(runtime.status()).toBe(200);
  expect(runtime.headers()["cache-control"]).toContain("immutable");
  for (const path of ["/", "/py/manifest.json", "/app.js", "/py/demo.py"]) {
    expect((await request.get(path)).headers()["cache-control"]).toBe("no-cache");
  }
});

test("robots.txt allows indexing", async ({ request }) => {
  const robots = await request.get("/robots.txt");
  expect(robots.status()).toBe(200);
  expect(await robots.text()).toMatch(/User-agent: \*\s+Allow: \//);
});

test("filled buttons are readable in dark mode", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await ready(page);
  const ratio = await page.evaluate(() => {
    const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number);
    const lum = ([r, g, b]) => {
      const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const style = getComputedStyle(document.querySelector(".prompt button"));
    const [a, b] = [lum(rgb(style.color)), lum(rgb(style.backgroundColor))].sort((x, y) => y - x);
    return (a + 0.05) / (b + 0.05);
  });
  expect(ratio).toBeGreaterThanOrEqual(4.5);
});

// How much of the canvas a colour covers, so a test sees whether a trace is really drawn.
async function pixels(page, [r, g, b]) {
  return page.getByTestId("scope-screen").evaluate((canvas, color) => {
    const { data } = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
    let count = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (Math.abs(data[i] - color[0]) < 40 && Math.abs(data[i + 1] - color[1]) < 40 && Math.abs(data[i + 2] - color[2]) < 40) count++;
    }
    return count;
  }, [r, g, b]);
}
const CH1 = [0xf2, 0xd6, 0x48];
const CH2 = [0x4f, 0xd2, 0xe8];

test("the scope draws what the simulator captured", async ({ page }) => {
  await ready(page);
  await expect(id(page, "scope-status")).toHaveText("T'D");
  await expect(id(page, "scope-readout")).toContainText("CH1 500 mV/div");
  await expect(id(page, "scope-readout")).toContainText("200 µs/div");
  expect(await pixels(page, CH1)).toBeGreaterThan(500);
  expect(await pixels(page, CH2)).toBeLessThan(100); // only the trigger marker's neighbours at most
});

test("scope tour", async ({ page }) => {
  await ready(page);
  const output = id(page, "terminal-output");
  await id(page, "chip-7").click();
  await expect(output).toContainText(/(9\.99|1\.00)\d*e\+0[23]/);
  await id(page, "chip-8").click();
  await expect(output).toContainText("#9000001200<1200 bytes of binary data>");
  await expect(output).toContainText("0,0,1200,1,");

  await id(page, "chip-9").click(); // 100 mV/div clips: 1.02 V instead of 2 V
  await expect(output).toContainText(/1\.0[0-9]\d*e\+00/);
  await expect(id(page, "scope-readout")).toContainText("CH1 100 mV/div");

  await id(page, "chip-10").click();
  await expect(id(page, "scope-readout")).toContainText("CH2 5 V/div");
  await expect(id(page, "psu-ch1-led")).toHaveAttribute("data-on", "true");
  await expect.poll(() => pixels(page, CH2)).toBeGreaterThan(500);

  await id(page, "chip-11").click();
  await expect(id(page, "scope-status")).toHaveText("WAIT");
  await expect(output).toContainText("VI_ERROR_TMO", { timeout: 10_000 });
  await id(page, "chip-12").click();
  await expect(id(page, "scope-status")).toHaveText("T'D", { timeout: 10_000 });
  await expect(id(page, "target-scope")).toHaveAttribute("aria-pressed", "true");
});

test("scope faults and the terminal", async ({ page }) => {
  await ready(page);
  await id(page, "fault-scope-no-trigger").click();
  await expect(id(page, "scope-status")).toHaveText("AUTO");
  await expect(id(page, "fault-badge-scope")).toContainText("no trigger");
  await id(page, "reset").click();
  await expect(id(page, "scope-status")).toHaveText("T'D");
  await expect(id(page, "fault-badge-scope")).toBeHidden();

  await id(page, "fault-scope-probe-off").click();
  await expect(id(page, "fault-badge-scope")).toContainText("probe off");
  await id(page, "reset").click();

  await id(page, "target-scope").click();
  await id(page, "terminal-input").fill("*IDN?");
  await id(page, "terminal-input").press("Enter");
  await expect(id(page, "terminal-output")).toContainText("RIGOL TECHNOLOGIES,DS1054Z");
  await id(page, "terminal-input").fill("NOPE");
  await id(page, "terminal-input").press("Enter");
  await expect(id(page, "scope-err")).toBeVisible();
});

test("a running scope keeps capturing, a stopped one does not", async ({ page }) => {
  await ready(page);
  await id(page, "fault-scope-noise").click();
  const frame = () => id(page, "scope-screen").evaluate((canvas) => canvas.toDataURL());
  await expect.poll(async () => {
    const first = await frame();
    await page.waitForTimeout(700);
    return first !== (await frame());
  }).toBe(true);
  await id(page, "target-scope").click();
  await id(page, "terminal-input").fill(":STOP");
  await id(page, "terminal-input").press("Enter");
  await expect(id(page, "scope-status")).toHaveText("STOP");
  const stopped = await frame();
  await page.waitForTimeout(700);
  expect(await frame()).toBe(stopped);
});
