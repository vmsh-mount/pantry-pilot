/**
 * PantryPilot automated QA — uses real Chrome profile so Swiggy session is preserved.
 *
 * Usage:  node qa/run_qa.mjs
 *
 * Chrome must be CLOSED before running (Playwright can't attach to a running instance
 * when launching with a user-data-dir). If Chrome is open you'll get a lock error.
 */

import { chromium } from "playwright";
import { existsSync, mkdirSync, writeFileSync, rmSync } from "fs";
import { join } from "path";
import { spawn, execSync, spawnSync } from "child_process";
import { mkdtempSync } from "fs";
import { tmpdir } from "os";

const BASE_URL = "http://localhost:3000";
const SCREENSHOTS_DIR = join(import.meta.dirname, "screenshots");
const DEBUG_PORT = 9222;

if (!existsSync(SCREENSHOTS_DIR)) mkdirSync(SCREENSHOTS_DIR, { recursive: true });

const results = [];
let page, browser, chromeProc;

async function shot(name) {
  const file = join(SCREENSHOTS_DIR, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`  📸 ${name}.png`);
}

async function check(name, fn) {
  process.stdout.write(`▶ ${name} ... `);
  try {
    await fn();
    console.log("✅ PASS");
    results.push({ name, status: "PASS" });
  } catch (e) {
    console.log(`❌ FAIL: ${e.message}`);
    results.push({ name, status: "FAIL", error: e.message });
    await shot(`FAIL_${name.replace(/\s+/g, "_")}`).catch(() => {});
  }
}

async function nav(path, waitFor) {
  await page.goto(`${BASE_URL}${path}`, { waitUntil: "networkidle", timeout: 15000 });
  if (waitFor) await page.waitForSelector(waitFor, { timeout: 10000 });
  await page.waitForTimeout(800);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ─── Launch Chrome with remote debugging ─────────────────────────────────────

// Chrome refuses remote debugging on its default profile dir — copy it to a temp dir.
const CHROME_SRC = `${process.env.HOME}/Library/Application Support/Google/Chrome`;
const TEMP_PROFILE = mkdtempSync(join(tmpdir(), "chrome-qa-"));

console.log("📋 Copying Chrome profile to temp dir (keeps sessions)...");
spawnSync("rsync", ["-a", "--exclude=lock", "--exclude=SingletonLock",
  "--exclude=SingletonSocket", "--exclude=SingletonCookie",
  `${CHROME_SRC}/`, `${TEMP_PROFILE}/`], { stdio: "inherit" });

console.log("🚀 Launching Chrome with remote debugging...");
chromeProc = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  `--remote-debugging-port=${DEBUG_PORT}`,
  `--user-data-dir=${TEMP_PROFILE}`,
  "--no-first-run",
  "--no-default-browser-check",
  `${BASE_URL}`,
], { detached: false, stdio: "ignore" });

// Wait for Chrome to start and expose DevTools
await sleep(5000);

// Connect via CDP
browser = await chromium.connectOverCDP(`http://localhost:${DEBUG_PORT}`);
const ctx = browser.contexts()[0];
const pages = ctx.pages();
page = pages.find(p => p.url().includes("localhost:3000")) ?? pages[0];
if (!page) page = await ctx.newPage();

console.log("\n🧪 PantryPilot QA — starting\n");

// ── 1. Onboarding / redirect ─────────────────────────────────────────────────
await check("App loads at /", async () => {
  await page.goto(BASE_URL, { waitUntil: "networkidle", timeout: 20000 });
  await shot("01_root");
  // Should land on dashboard or onboarding
  const url = page.url();
  if (!url.includes("localhost:3000")) throw new Error(`Unexpected URL: ${url}`);
});

// ── 2. Dashboard ─────────────────────────────────────────────────────────────
await check("Dashboard loads", async () => {
  await nav("/dashboard");
  await shot("02_dashboard");
  // Look for known dashboard elements
  const hasHero = await page.locator("text=/calories|spend|₹/i").first().isVisible().catch(() => false);
  if (!hasHero) throw new Error("No hero tiles found on dashboard");
});

await check("Dashboard — recent orders section", async () => {
  const hasOrders = await page.locator("text=/recent orders/i").isVisible().catch(() => false);
  if (!hasOrders) throw new Error("Recent orders section missing");
});

// ── 3. Orders ────────────────────────────────────────────────────────────────
await check("Orders page loads", async () => {
  await nav("/orders");
  await shot("03_orders");
  const body = await page.textContent("body");
  if (!body.match(/order|swiggy/i)) throw new Error("No order content found");
});

// ── 4. Quick Order ───────────────────────────────────────────────────────────
await check("Quick Order page loads", async () => {
  await nav("/quick-order");
  await shot("04_quick_order");
  const body = await page.textContent("body");
  if (!body.match(/quick|order|cart/i)) throw new Error("No quick-order content found");
});

// ── 5. Routines ──────────────────────────────────────────────────────────────
await check("Routines page loads", async () => {
  await nav("/routines");
  await shot("05_routines");
  const body = await page.textContent("body");
  if (!body.match(/routine|schedule/i)) throw new Error("No routines content found");
});

// ── 6. Runs ──────────────────────────────────────────────────────────────────
await check("Runs page loads", async () => {
  await nav("/runs");
  await shot("06_runs");
  const body = await page.textContent("body");
  if (!body.match(/run|flow/i)) throw new Error("No runs content found");
});

// ── 7. Pantry ────────────────────────────────────────────────────────────────
await check("Pantry page loads", async () => {
  await nav("/pantry");
  await shot("07_pantry");
  const body = await page.textContent("body");
  if (!body.match(/pantry|item|stocked|low|depleted/i)) throw new Error("No pantry content found");
});

await check("Pantry — items grouped by category", async () => {
  // Categories appear as section headers
  const categories = await page.locator("h2, h3").allTextContents();
  if (categories.length === 0) throw new Error("No category headers found");
  console.log(`    Categories: ${categories.slice(0, 5).join(", ")}`);
});

// ── 8. Settings ──────────────────────────────────────────────────────────────
await check("Settings page loads", async () => {
  await nav("/settings");
  await shot("08_settings");
  const body = await page.textContent("body");
  if (!body.match(/setting|preference|household/i)) throw new Error("No settings content found");
});

// ── 9. Nav links present ─────────────────────────────────────────────────────
await check("Nav has all main links", async () => {
  await nav("/dashboard");
  const nav_links = await page.locator("nav a, aside a").allTextContents();
  const text = nav_links.join(" ").toLowerCase();
  const missing = ["order", "pantry", "routine"].filter(k => !text.includes(k));
  if (missing.length) throw new Error(`Nav missing: ${missing.join(", ")}`);
});

// ── 10. API health ───────────────────────────────────────────────────────────
await check("Backend /health responds", async () => {
  const resp = await page.request.get("http://localhost:8000/health", { timeout: 5000 });
  if (!resp.ok()) throw new Error(`HTTP ${resp.status()}`);
});

// ── Summary ──────────────────────────────────────────────────────────────────
console.log("\n─────────────────────────────────────────────");
const passed = results.filter(r => r.status === "PASS").length;
const failed = results.filter(r => r.status === "FAIL").length;
console.log(`Results: ${passed} passed, ${failed} failed (${results.length} total)\n`);
results.forEach(r => {
  const icon = r.status === "PASS" ? "✅" : "❌";
  console.log(`  ${icon} ${r.name}${r.error ? ` — ${r.error}` : ""}`);
});

const reportPath = join(SCREENSHOTS_DIR, "report.json");
writeFileSync(reportPath, JSON.stringify(results, null, 2));
console.log(`\nScreenshots + report saved to: qa/screenshots/`);

await browser.close();
chromeProc.kill();
try { rmSync(TEMP_PROFILE, { recursive: true, force: true }); } catch {}
process.exit(failed > 0 ? 1 : 0);
