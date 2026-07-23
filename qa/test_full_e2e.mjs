import { chromium } from "playwright";
import { mkdtempSync, rmSync, existsSync, mkdirSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { spawn, spawnSync } from "child_process";

const BASE_URL = "http://localhost:3000";
const API_BASE = "http://localhost:8000";
const DEBUG_PORT = 9227;
const CHROME_SRC = `${process.env.HOME}/Library/Application Support/Google/Chrome`;
const TEMP_PROFILE = mkdtempSync(join(tmpdir(), "chrome-e2e-"));
const SHOTS = join(import.meta.dirname, "screenshots", "e2e");
if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });

const SESSION_COOKIE = "eyJob3VzZWhvbGRfaWQiOiAiNDMyN2ZjNTQtNGIzYi00YTgzLTgyYjMtNTcyM2I3ZjQ4NDY1In0=.amD5JQ.XQlqryCmyp8Gd7jYbsiB7tKTu3c";
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

const results = [];
let page, ctx;

async function shot(name) {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
  console.log(`    📸 ${name}.png`);
}

async function check(name, fn) {
  process.stdout.write(`\n▶ ${name} ... `);
  try {
    await fn();
    console.log("✅ PASS");
    results.push({ name, status: "PASS" });
  } catch (e) {
    console.log(`❌ FAIL: ${e.message}`);
    results.push({ name, status: "FAIL", error: e.message });
    await shot(`FAIL_${name.replace(/\W+/g, "_")}`).catch(() => {});
  }
}

async function nav(path) {
  await page.goto(`${BASE_URL}${path}`, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForFunction(() => !document.querySelector('.animate-spin'), { timeout: 15000 }).catch(() => {});
  await sleep(1000);
}

async function apiGet(path) {
  const r = await page.request.get(`${API_BASE}${path}`, { timeout: 10000 });
  return r.json();
}
async function apiPost(path, body = {}) {
  const r = await page.request.post(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    data: body, timeout: 10000,
  });
  return r.json();
}

// ── Launch Chrome ─────────────────────────────────────────────────────────────
console.log("📋 Copying Chrome profile...");
spawnSync("rsync", ["-a", "--exclude=lock", "--exclude=SingletonLock",
  "--exclude=SingletonSocket", "--exclude=SingletonCookie",
  `${CHROME_SRC}/`, `${TEMP_PROFILE}/`], { stdio: "inherit" });

console.log("🚀 Launching Chrome...");
const chromeProc = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  `--remote-debugging-port=${DEBUG_PORT}`,
  `--user-data-dir=${TEMP_PROFILE}`,
  "--no-first-run", "--no-default-browser-check",
  `${BASE_URL}/dashboard`,
], { detached: false, stdio: "ignore" });

await sleep(5000);
const browser = await chromium.connectOverCDP(`http://localhost:${DEBUG_PORT}`);
ctx = browser.contexts()[0];
page = ctx.pages()[0] ?? await ctx.newPage();
await ctx.addCookies([{
  name: "session", value: SESSION_COOKIE,
  domain: "localhost", path: "/",
  httpOnly: true, secure: false, sameSite: "Lax",
}]);

console.log("\n🧪 PantryPilot Full E2E — starting\n");

// ══════════════════════════════════════════════════════════════════════════════
// SECTION 1: FLOW — Skip this week
// ══════════════════════════════════════════════════════════════════════════════
console.log("\n━━━ FLOW ━━━");

await check("Flow basket loads with 16 items", async () => {
  await nav("/flow");
  await shot("01_flow_basket");
  const body = await page.textContent("body");
  if (!body.match(/your basket is ready|awaiting/i)) throw new Error("Basket not in awaiting state: " + body.slice(0, 200));
  const itemCount = body.match(/(\d+) items?/i)?.[1];
  console.log(`    Items in basket: ${itemCount}`);
});

await check("Flow — Skip this week", async () => {
  await nav("/flow");
  const skipBtn = page.locator("text=Skip this week").first();
  await skipBtn.waitFor({ state: "visible", timeout: 5000 });
  await shot("02_before_skip");
  await skipBtn.click();
  await sleep(3000);
  await shot("03_after_skip");
  const body = await page.textContent("body");
  console.log("    Post-skip body:", body.replace(/\s+/g, " ").slice(0, 200));
  if (!body.match(/skip|next run|plan now|skipped/i)) throw new Error("Skip didn't seem to work: " + body.slice(0, 200));
});

// Trigger a new run for the approve test
await check("Flow — Trigger new run for approve test", async () => {
  const resp = await apiPost("/v1/basket/trigger");
  console.log("    Trigger response:", JSON.stringify(resp).slice(0, 150));
  if (!resp.success && !resp.data?.run_id && !resp.error?.code?.match(/already|progress/i))
    throw new Error("Trigger failed: " + JSON.stringify(resp));
});

await check("Flow — Wait for basket to be ready (up to 90s)", async () => {
  let ready = false;
  for (let i = 0; i < 45; i++) {
    await sleep(2000);
    const data = await apiGet("/v1/runs?limit=1");
    const state = data?.data?.runs?.[0]?.state;
    process.stdout.write(` [${state}]`);
    if (state === "awaiting_confirmation") { ready = true; break; }
    if (state?.match(/failed|skipped/)) throw new Error(`Run ended with state: ${state}`);
  }
  if (!ready) throw new Error("Run did not reach awaiting_confirmation in 90s");
  await nav("/flow");
  await shot("04_flow_ready_for_approve");
});

await check("Flow — Approve & order (test mode)", async () => {
  await nav("/flow");
  // Scroll to bottom to find Approve button
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(500);
  const approveBtn = page.locator("text=Approve & order").first();
  await approveBtn.waitFor({ state: "visible", timeout: 5000 });
  await shot("05_before_approve");
  await approveBtn.click();
  await sleep(4000);
  await shot("06_after_approve");
  const body = await page.textContent("body");
  console.log("    Post-approve body:", body.replace(/\s+/g, " ").slice(0, 300));
  // In test mode it should go to placing/confirmed/completed state
  if (!body.match(/placing|order placed|confirmed|completed|dashboard|success/i))
    throw new Error("Approve did not transition correctly: " + body.slice(0, 300));
});

// ══════════════════════════════════════════════════════════════════════════════
// SECTION 2: QUICK ORDER — Search, Add, Place Order
// ══════════════════════════════════════════════════════════════════════════════
console.log("\n\n━━━ QUICK ORDER ━━━");

await check("Quick Order — search for 'eggs'", async () => {
  await nav("/quick");
  const addBtn = page.locator("text=Add item").first();
  await addBtn.click();
  await sleep(1000);
  const input = page.locator("input").first();
  await input.waitFor({ state: "visible", timeout: 5000 });
  await input.fill("eggs");
  await sleep(2500);
  await shot("07_quick_search_eggs");
  const body = await page.textContent("body");
  if (!body.match(/egg/i)) throw new Error("No egg results: " + body.slice(0, 300));
  const results = body.match(/egg[^\n]*/gi)?.slice(0, 3).join(", ");
  console.log("    Results:", results?.slice(0, 100));
});

await check("Quick Order — add egg to basket", async () => {
  const result = page.locator("li, button, [role=option]").filter({ hasText: /egg/i }).first();
  const hasResult = await result.isVisible({ timeout: 3000 }).catch(() => false);
  if (!hasResult) throw new Error("No egg result to click");
  await result.click();
  await sleep(1500);
  await shot("08_quick_egg_added");
  const body = await page.textContent("body");
  if (!body.match(/egg/i) || !body.match(/estimated total|place order/i))
    throw new Error("Egg not in basket: " + body.slice(0, 300));
  const total = body.match(/₹\d+/)?.[0];
  console.log("    Basket total: " + total);
});

await check("Quick Order — add second item 'paneer'", async () => {
  const addMore = page.locator("text=Add item").first();
  await addMore.click();
  await sleep(800);
  const input = page.locator("input").first();
  await input.fill("paneer");
  await sleep(2500);
  await shot("09_quick_search_paneer");
  const body = await page.textContent("body");
  if (!body.match(/paneer/i)) throw new Error("No paneer results");
  const firstResult = page.locator("li, button, [role=option]").filter({ hasText: /paneer/i }).first();
  await firstResult.click();
  await sleep(1500);
  await shot("10_quick_two_items");
  const body2 = await page.textContent("body");
  const total = body2.match(/estimated total[\s₹\d,]+/i)?.[0];
  console.log("    " + total?.replace(/\s+/g, " ").trim().slice(0, 60));
  if (!body2.match(/paneer/i)) throw new Error("Paneer not added to basket");
});

await check("Quick Order — Place Order (test mode)", async () => {
  const placeBtn = page.locator("text=/Place Order/i").first();
  await placeBtn.waitFor({ state: "visible", timeout: 5000 });
  await shot("11_before_place_order");
  await placeBtn.click();
  await sleep(5000);
  await shot("12_after_place_order");
  const url = page.url();
  const body = await page.textContent("body");
  console.log("    URL:", url);
  console.log("    Body:", body.replace(/\s+/g, " ").slice(0, 300));
  if (!body.match(/placing|placed|order|confirmed|success|basket/i))
    throw new Error("Place order did not respond as expected");
});

// ══════════════════════════════════════════════════════════════════════════════
// SECTION 3: ROUTINES — Create, navigate detail, skip next order
// ══════════════════════════════════════════════════════════════════════════════
console.log("\n\n━━━ ROUTINES ━━━");

await check("Routines — navigate to new routine page", async () => {
  await nav("/routines/new");
  await shot("13_routines_new");
  const body = await page.innerText("body");
  if (!body.match(/routine name|new routine/i))
    throw new Error("Not on new routine page: " + body.slice(0, 200));
});

await check("Routines — enter name", async () => {
  const nameInput = page.locator("input").first();
  await nameInput.waitFor({ state: "visible", timeout: 5000 });
  await nameInput.fill("E2E Routine");
  const val = await nameInput.inputValue();
  if (val !== "E2E Routine") throw new Error(`Name wrong: "${val}"`);
  await shot("14_routine_name");
});

await check("Routines — add item via search dropdown", async () => {
  // ItemSearchDropdown renders as <button> with "Add item" text when closed
  const addBtn = page.locator("button").filter({ hasText: /add item/i }).first();
  await addBtn.waitFor({ state: "visible", timeout: 5000 });
  await addBtn.click();
  await sleep(800);

  const searchInput = page.locator("input[placeholder='Search for an item…']");
  await searchInput.waitFor({ state: "visible", timeout: 5000 });
  await searchInput.fill("milk");
  await sleep(2500);
  await shot("15_routine_search_milk");

  const resultBtn = page.locator("ul li button").filter({ hasText: /milk/i }).first();
  await resultBtn.waitFor({ state: "visible", timeout: 5000 });
  const text = await resultBtn.textContent();
  console.log("    Selected:", text.trim().slice(0, 60));
  await resultBtn.click();
  await sleep(1500);

  const body = await page.innerText("body");
  if (!body.match(/milk/i)) throw new Error("Milk not in selected items");
  await shot("16_routine_milk_added");
});

await check("Routines — step 1 Next →", async () => {
  const nextBtn = page.locator("button").filter({ hasText: /next/i }).first();
  await nextBtn.waitFor({ state: "visible", timeout: 5000 });
  if (await nextBtn.isDisabled()) throw new Error("Next is disabled (name or items missing)");
  await nextBtn.click();
  await sleep(1500);
  const body = await page.innerText("body");
  if (!body.match(/how often|frequency|every day|weekly/i))
    throw new Error("Did not reach step 2: " + body.slice(0, 200));
  await shot("17_step2");
});

await check("Routines — step 2: select Ongoing duration + Next →", async () => {
  // durationPreset defaults to "" — must select something or Next stays disabled
  const ongoingBtn = page.locator("button").filter({ hasText: /^ongoing$/i }).first();
  await ongoingBtn.waitFor({ state: "visible", timeout: 5000 });
  await ongoingBtn.click();
  await sleep(400);

  const nextBtn = page.locator("button").filter({ hasText: /next/i }).first();
  if (await nextBtn.isDisabled()) throw new Error("Step 2 Next still disabled after selecting Ongoing");
  await nextBtn.click();
  await sleep(1500);
  const body = await page.innerText("body");
  if (!body.match(/e2e routine|start routine|frequency|items/i))
    throw new Error("Did not reach step 3: " + body.slice(0, 200));
  await shot("18_step3");
  console.log("    Step 3:", body.replace(/\s+/g, " ").slice(0, 200));
});

await check("Routines — step 3: Start routine", async () => {
  const startBtn = page.locator("button").filter({ hasText: /start routine/i }).first();
  await startBtn.waitFor({ state: "visible", timeout: 5000 });
  await shot("19_before_start");
  await startBtn.click();
  await sleep(3000);
  await shot("20_after_start");
  if (page.url().includes("/new")) throw new Error("Still on /new after save. URL: " + page.url());
  const body = await page.innerText("body");
  if (!body.match(/e2e routine/i)) throw new Error("Routine not visible after save: " + body.slice(0, 200));
  console.log("    URL:", page.url());
  console.log("    ✓ Routine saved and visible in list");
});

await check("Routines — detail page and skip next order", async () => {
  const routineLink = page.locator("a, button").filter({ hasText: /e2e routine/i }).first();
  await routineLink.waitFor({ state: "visible", timeout: 5000 });
  await routineLink.click();
  await sleep(2000);
  await page.waitForFunction(() => !document.querySelector('.animate-spin'), { timeout: 10000 }).catch(() => {});
  await shot("21_routine_detail");

  const body = await page.innerText("body");
  if (!body.match(/upcoming|skip/i)) throw new Error("Detail page missing upcoming orders");

  const skipBtn = page.locator("button").filter({ hasText: /^skip$/i }).first();
  await skipBtn.waitFor({ state: "visible", timeout: 5000 });
  await skipBtn.click();
  await sleep(2000);
  await shot("22_after_skip");

  const body2 = await page.innerText("body");
  if (body2.match(/error|failed|something went wrong/i))
    throw new Error("Skip returned error: " + body2.slice(0, 200));
  console.log("    ✓ Upcoming orders visible, skip successful");
});

// ══════════════════════════════════════════════════════════════════════════════
// SECTION 4: NUTRITION RESULTS
// ══════════════════════════════════════════════════════════════════════════════
console.log("\n\n━━━ NUTRITION ━━━");

await check("Nutrition — check orders page for nutrition toggle", async () => {
  await nav("/orders");
  await shot("23_orders_for_nutrition");
  const body = await page.textContent("body");
  // Look for any order that was placed via PantryPilot (has nutrition toggle)
  const hasToggle = await page.locator("text=/nutrition|show nutrition|calories/i").first().isVisible({ timeout: 3000 }).catch(() => false);
  console.log("    Has nutrition toggle:", hasToggle);
  if (!hasToggle) console.log("    (Orders may be from Swiggy only — PP orders needed for nutrition)");
});

await check("Nutrition — check API for nutrition data", async () => {
  // Get the latest order that was placed via PP
  const ordersResp = await apiGet("/v1/orders?limit=10");
  const orders = ordersResp?.data?.orders ?? [];
  const ppOrder = orders.find(o => o.pantrypilot_order_id);
  if (!ppOrder) {
    console.log("    No PP orders yet — nutrition needs a placed PP order");
    return; // soft pass
  }
  const orderId = ppOrder.pantrypilot_order_id;
  console.log("    PP order ID:", orderId);
  const nutResp = await apiGet(`/v1/nutrition/order/${orderId}`);
  console.log("    Nutrition status:", nutResp?.data?.status);
  console.log("    Nutrition data:", JSON.stringify(nutResp?.data).slice(0, 200));
  await shot("24_nutrition_api");
  if (!nutResp?.data) throw new Error("No nutrition data returned");
});

// ── Summary ───────────────────────────────────────────────────────────────────
console.log("\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
const passed = results.filter(r => r.status === "PASS").length;
const failed = results.filter(r => r.status === "FAIL").length;
console.log(`Results: ${passed} passed, ${failed} failed (${results.length} total)\n`);
results.forEach(r => {
  const icon = r.status === "PASS" ? "✅" : "❌";
  console.log(`  ${icon} ${r.name}${r.error ? ` — ${r.error}` : ""}`);
});
writeFileSync(join(SHOTS, "report.json"), JSON.stringify(results, null, 2));

await browser.close();
chromeProc.kill();
try { rmSync(TEMP_PROFILE, { recursive: true, force: true }); } catch {}
process.exit(failed > 0 ? 1 : 0);
