// Makes site/img/bench.png (fallback and link-preview image, 1200×630) from the running demo:
//   node tests/screenshot.mjs [url]        default http://localhost:8080
import { chromium } from "@playwright/test";

const url = process.argv[2] ?? process.env.DEMO_URL ?? "http://localhost:8080";
const out = new URL("../site/img/bench.png", import.meta.url).pathname;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, colorScheme: "light" });
await page.goto(url);
await page.getByTestId("status").filter({ hasText: "ready" }).waitFor({ timeout: 60_000 });
for (const n of [1, 2, 7, 10]) await page.getByTestId(`chip-${n}`).click();
await page.getByTestId("scope-readout").filter({ hasText: "CH2 5 V/div" }).waitFor();
await page.waitForTimeout(500);
// the supply, the scope and the terminal fit the picture; the DAQ does not
await page.evaluate(() => {
  document.querySelector("#daq").style.display = "none";
  for (const wiring of document.querySelectorAll(".wiring")) wiring.style.display = "none";
  window.scrollTo(0, 0);
});
await page.waitForTimeout(500);
const box = await page.locator("section.bench").boundingBox();
await page.screenshot({ path: out, clip: { x: 0, y: Math.max(0, box.y - 24), width: 1200, height: 630 } });
await browser.close();
console.log(`wrote ${out}`);
