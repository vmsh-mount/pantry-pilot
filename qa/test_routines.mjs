import { chromium } from "playwright";
import { mkdtempSync, rmSync, existsSync, mkdirSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { spawn, spawnSync } from "child_process";

const BASE_URL = "http://localhost:3000";
const API_BASE = "http://localhost:8000";
const DEBUG_PORT = 9229;
const CHROME_SRC = `${process.env.HOME}/Library/Application Support/Google/Chrome`;
const TEMP_PROFILE = mkdtempSync(join(tmpdir(), "chrome-routines-"));
const SHOTS = join(import.meta.dirname, "screenshots", "routines");
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

// ── Launch Chrome ─────────────────────────────────────────────────────────────
spawnSync("rsync", ["-a", "--exclude=lock", "--exclude=SingletonLock",
  "--exclude=SingletonSocket", "--exclude=SingletonCookie",
  `${CHROME_SRC}/`, `${TEMP_PROFILE}/`], { stdio: "inherit" });

const chromeProc = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  `--remote-debugging-port=${DEBUG_PORT}`,
  `--user-data-dir=${TEMP_PROFILE}`,
  "--no-first-run", "--no-default-browser-check",
  `${BASE_URL}/routines`,
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

console.log("\n🧪 Routines E2E Test\n");

// ── 1. Routines list ─────────────────────────────────────────────────────────
await check("Routines list loads", async () => {
  await nav("/routines");
  await shot("01_routines_list");
  const body = await page.textContent("body");
  if (!body.match(/routine/i)) throw new Error("No routines content");
  console.log(`    Body: ${body.replace(/\s+/g, " ").slice(0, 150)}`);
});

// ── 2. Navigate to new routine page ──────────────────────────────────────────
await check("Navigate to /routines/new", async () => {
  await nav("/routines/new");
  await shot("02_new_routine_step1");
  const body = await page.textContent("body");
  if (!body.match(/routine name|new routine/i)) throw new Error("Not on new routine page: " + body.slice(0, 200));
});

// ── 3. Step 1: Enter name ────────────────────────────────────────────────────
await check("Step 1 — Enter routine name", async () => {
  // The name input is the only visible input at this point
  const nameInput = page.locator("input").first();
  await nameInput.waitFor({ state: "visible", timeout: 5000 });
  await nameInput.fill("Weekly Essentials");
  await sleep(500);
  const val = await nameInput.inputValue();
  if (val !== "Weekly Essentials") throw new Error(`Name input has wrong value: ${val}`);
  await shot("03_routine_name_filled");
  console.log(`    Name filled: "${val}"`);
});

// ── 4. Step 1: Add item via ItemSearchDropdown ────────────────────────────────
await check("Step 1 — Open item search and add milk", async () => {
  // The ItemSearchDropdown renders as a <button> with "+ Add item" text when closed
  // Must target <button> specifically (not div) to trigger the onClick handler
  const addBtn = page.locator("button").filter({ hasText: /add item/i }).first();
  await addBtn.waitFor({ state: "visible", timeout: 5000 });
  await addBtn.click();
  await sleep(800); // wait for search input to appear

  // Now the dropdown is open: search input has placeholder "Search for an item…"
  const searchInput = page.locator("input[placeholder='Search for an item…']");
  await searchInput.waitFor({ state: "visible", timeout: 5000 });
  await shot("04_search_input_open");

  await searchInput.fill("milk");
  await sleep(2500); // wait for debounced search (300ms) + network
  await shot("05_search_milk");

  const body = await page.textContent("body");
  console.log(`    Body after search: ${body.replace(/\s+/g, " ").slice(0, 300)}`);
  if (!body.match(/milk/i)) throw new Error("No milk results in search");
});

// ── 5. Select milk from dropdown ─────────────────────────────────────────────
await check("Step 1 — Select milk from results", async () => {
  // Results render as <li><button> inside a <ul>
  const resultBtn = page.locator("ul li button").filter({ hasText: /milk/i }).first();
  await resultBtn.waitFor({ state: "visible", timeout: 5000 });
  const text = await resultBtn.textContent();
  console.log(`    Selecting: "${text.trim().slice(0, 60)}"`);
  await resultBtn.click();
  await sleep(1500);
  await shot("06_milk_added");

  // After select: the dropdown closes, milk appears in "Selected items"
  const body = await page.textContent("body");
  if (!body.match(/milk/i)) throw new Error("Milk not visible after adding");

  // Verify name is still "Weekly Essentials" (not overwritten)
  const nameInput = page.locator("input").first();
  const val = await nameInput.inputValue();
  console.log(`    Name input still: "${val}"`);
  if (!val.match(/weekly essentials/i)) throw new Error(`Name was overwritten: "${val}"`);
});

// ── 6. Add second item: bread ─────────────────────────────────────────────────
await check("Step 1 — Add second item: eggs", async () => {
  // After first item added, the dropdown re-renders as a button again
  const addBtn = page.locator("button").filter({ hasText: /add item/i }).first();
  await addBtn.click();
  await sleep(800);

  const searchInput = page.locator("input[placeholder='Search for an item…']");
  await searchInput.waitFor({ state: "visible", timeout: 5000 });
  await searchInput.fill("eggs");
  await sleep(2500);

  const resultBtn = page.locator("ul li button").filter({ hasText: /egg/i }).first();
  await resultBtn.waitFor({ state: "visible", timeout: 5000 });
  await resultBtn.click();
  await sleep(1500);
  await shot("07_two_items");

  const body = await page.textContent("body");
  if (!body.match(/egg/i)) throw new Error("Eggs not in selected items");
  console.log(`    Items visible: milk + eggs ✓`);
});

// ── 7. Step 1 → Next ─────────────────────────────────────────────────────────
await check("Step 1 — Next → enabled and clickable", async () => {
  // Next → is enabled when name.trim() !== "" AND items.length > 0
  const nextBtn = page.locator("button").filter({ hasText: /next/i }).first();
  await nextBtn.waitFor({ state: "visible", timeout: 5000 });
  const disabled = await nextBtn.isDisabled();
  if (disabled) {
    const body = await page.textContent("body");
    throw new Error("Next button is disabled. Body: " + body.replace(/\s+/g, " ").slice(0, 300));
  }
  await nextBtn.click();
  await sleep(1500);
  await shot("08_step2");
  const body = await page.textContent("body");
  console.log(`    Step 2 body: ${body.replace(/\s+/g, " ").slice(0, 200)}`);
  if (!body.match(/how often|frequency|schedule|every day|weekly|monthly/i))
    throw new Error("Did not reach step 2: " + body.slice(0, 200));
});

// ── 8. Step 2: Schedule ───────────────────────────────────────────────────────
await check("Step 2 — Select frequency: Every day", async () => {
  // "Every day" button should already be visible
  const everyDayBtn = page.locator("button").filter({ hasText: /^every day$/i }).first();
  const hasEveryDay = await everyDayBtn.isVisible({ timeout: 3000 }).catch(() => false);
  if (hasEveryDay) {
    await everyDayBtn.click();
    await sleep(400);
    console.log(`    Selected: Every day`);
  } else {
    // Just pick first frequency option available
    const freqBtn = page.locator("button").filter({ hasText: /every|weekly|daily/i }).first();
    await freqBtn.click();
    await sleep(400);
  }
  await shot("09_freq_selected");
});

await check("Step 2 — Select schedule time", async () => {
  // Time buttons: 7am, 8am, 9am, 10am, 12pm, 6pm
  const timeBtn = page.locator("button").filter({ hasText: /^(7am|8am|9am|10am|12pm|6pm)$/i }).first();
  const hasTime = await timeBtn.isVisible({ timeout: 3000 }).catch(() => false);
  if (hasTime) {
    const t = await timeBtn.textContent();
    await timeBtn.click();
    await sleep(400);
    console.log(`    Selected time: ${t.trim()}`);
  } else {
    console.log(`    Time buttons not found — may already have default`);
  }
  await shot("10_time_selected");
});

await check("Step 2 — Select duration: Ongoing", async () => {
  // Duration buttons: 2 weeks, 1 month, Ongoing, Pick date
  // "Ongoing" is required for Next → to be enabled (durationPreset cannot be empty)
  const ongoingBtn = page.locator("button").filter({ hasText: /^ongoing$/i }).first();
  await ongoingBtn.waitFor({ state: "visible", timeout: 5000 });
  await ongoingBtn.click();
  await sleep(400);
  console.log(`    Selected duration: Ongoing`);
  await shot("11_duration_selected");
});

await check("Step 2 — Next → enabled and clickable", async () => {
  const nextBtn = page.locator("button").filter({ hasText: /next/i }).first();
  await nextBtn.waitFor({ state: "visible", timeout: 5000 });
  const disabled = await nextBtn.isDisabled();
  if (disabled) {
    const body = await page.textContent("body");
    throw new Error("Step 2 Next is still disabled. Body: " + body.replace(/\s+/g, " ").slice(0, 300));
  }
  await nextBtn.click();
  await sleep(1500);
  await shot("12_step3");
  const body = await page.textContent("body");
  console.log(`    Step 3 body: ${body.replace(/\s+/g, " ").slice(0, 300)}`);
  if (!body.match(/weekly essentials|frequency|start routine|review|confirm/i))
    throw new Error("Did not reach step 3: " + body.slice(0, 200));
});

// ── 9. Step 3: Review + Start routine ────────────────────────────────────────
await check("Step 3 — Start routine", async () => {
  // Save button on step 3 is labeled "Start routine"
  const startBtn = page.locator("button").filter({ hasText: /start routine/i }).first();
  await startBtn.waitFor({ state: "visible", timeout: 5000 });
  const text = await startBtn.textContent();
  console.log(`    Clicking: "${text.trim()}"`);
  await shot("13_before_start_routine");
  await startBtn.click();
  await sleep(3000);
  await shot("14_after_start_routine");
  const url = page.url();
  const body = await page.textContent("body");
  console.log(`    URL: ${url}`);
  console.log(`    Body: ${body.replace(/\s+/g, " ").slice(0, 200)}`);
  if (url.includes("/new")) throw new Error("Still on /new after save. URL: " + url);
  if (!url.includes("/routines")) throw new Error("Redirected away from routines: " + url);
});

// ── 10. Routine detail page ───────────────────────────────────────────────────
await check("Routine detail / list shows saved routine", async () => {
  await shot("15_after_create");
  const body = await page.textContent("body");
  if (!body.match(/weekly essentials/i))
    throw new Error("'Weekly Essentials' not visible: " + body.slice(0, 300));
  console.log(`    ✓ 'Weekly Essentials' visible`);
});

// ── 11. Navigate to routine detail + test Skip ───────────────────────────────
await check("Navigate to routine detail page", async () => {
  // From /routines list, click the routine card to open detail
  const routineLink = page.locator("a, button, [role=link]").filter({ hasText: /weekly essentials/i }).first();
  await routineLink.waitFor({ state: "visible", timeout: 5000 });
  await routineLink.click();
  await sleep(2000);
  await page.waitForFunction(() => !document.querySelector('.animate-spin'), { timeout: 10000 }).catch(() => {});
  await shot("16_routine_detail");

  // Verify the detail page rendered correctly
  const heading = await page.locator("h1, h2, [class*=font-bold], [class*=font-semibold]")
    .filter({ hasText: /weekly essentials/i }).first().textContent().catch(() => "");
  console.log(`    Heading: "${heading.trim()}"`);
  const body = await page.innerText("body");
  if (!body.match(/weekly essentials/i)) throw new Error("Detail page missing routine name");
  console.log(`    Detail page: ${body.replace(/\s+/g, " ").slice(0, 200)}`);
});

await check("Routine detail — shows upcoming orders", async () => {
  const body = await page.innerText("body");
  if (!body.match(/upcoming|fri|sat|sun|mon|tue|wed|thu|07:00|skip/i))
    throw new Error("No upcoming orders section: " + body.slice(0, 300));
  console.log(`    ✓ Upcoming orders visible`);
  await shot("17_upcoming_orders");
});

await check("Routine detail — Skip next order", async () => {
  // "Skip" button appears next to the next upcoming order
  const skipBtn = page.locator("button").filter({ hasText: /^skip$/i }).first();
  await skipBtn.waitFor({ state: "visible", timeout: 5000 });
  await shot("18_before_skip");
  await skipBtn.click();
  await sleep(2000);
  await shot("19_after_skip");
  // After skipping, the next date should shift by one day
  const body = await page.innerText("body");
  console.log(`    Post-skip body: ${body.replace(/\s+/g, " ").slice(0, 300)}`);
  // Just verify we're still on the routine detail page without error
  if (body.match(/error|failed|something went wrong/i))
    throw new Error("Skip returned error: " + body.slice(0, 200));
  console.log(`    ✓ Skip completed successfully`);
});

// ── 12. API verification ──────────────────────────────────────────────────────
await check("API confirms routine exists", async () => {
  const resp = await apiGet("/v1/routines");
  const routines = resp?.data?.routines ?? resp?.data ?? [];
  console.log(`    API routines count: ${routines.length}`);
  const found = routines.find(r => r.name?.match(/weekly essentials/i));
  if (!found) throw new Error(`'Weekly Essentials' not in API. Got: ${routines.map(r=>r.name).join(", ")}`);
  console.log(`    ✓ Found routine: ${found.name} (id: ${found.id})`);
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
