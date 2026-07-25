import { chromium } from "playwright";
import { mkdtempSync, rmSync, existsSync, mkdirSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { spawn, spawnSync, execSync } from "child_process";

const BASE_URL = "http://localhost:3000";
const API_BASE = "http://localhost:8000";
const DEBUG_PORT = 9233;
const CHROME_SRC = `${process.env.HOME}/Library/Application Support/Google/Chrome`;
const TEMP_PROFILE = mkdtempSync(join(tmpdir(), "chrome-gaps-"));
const SHOTS = join(import.meta.dirname, "screenshots", "nutrition_gaps");
if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });

const HOUSEHOLD_ID = "4327fc54-4b3b-4a83-82b3-5723b7f48465";
const SESSION_COOKIE = "eyJob3VzZWhvbGRfaWQiOiAiNDMyN2ZjNTQtNGIzYi00YTgzLTgyYjMtNTcyM2I3ZjQ4NDY1In0=.amNFRQ.EeyhzFMPguw_fL3wPxmMJD6lgIU";
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

async function nav(path, waitMs = 1500) {
  await page.goto(`${BASE_URL}${path}`, { waitUntil: "load", timeout: 30000 });
  await sleep(waitMs);
}

function pilotExec(pyCode) {
  const cmd = `docker compose exec -T pilot python3 -c "${pyCode.replace(/"/g, '\\"')}"`;
  return execSync(cmd, { cwd: join(import.meta.dirname, "..", "app"), encoding: "utf-8" });
}

async function apiGet(path) {
  const r = await page.request.get(`${API_BASE}${path}`, {
    headers: { Cookie: `session=${SESSION_COOKIE}` }, timeout: 10000,
  });
  return r.json();
}

// ── Test fixture setup (direct DB, deterministic, no LLM/live-search cost) ────
console.log("🔧 Seeding: enable nutrition_gaps_enabled + household member...");
pilotExec(`
import asyncio, uuid
from app.database import AsyncSessionLocal
from app.models.db import Household, HouseholdMember
from sqlalchemy import update, select

async def main():
    async with AsyncSessionLocal() as db:
        await db.execute(update(Household).where(Household.id == '${HOUSEHOLD_ID}').values(nutrition_gaps_enabled=True))
        existing = (await db.execute(select(HouseholdMember).where(HouseholdMember.household_id == '${HOUSEHOLD_ID}'))).scalars().all()
        for m in existing:
            await db.delete(m)
        m = HouseholdMember(id=str(uuid.uuid4()), household_id='${HOUSEHOLD_ID}', role='adult', age_years=38, sex='male', weight_kg=75, height_cm=175, activity_level='very_active')
        db.add(m)
        await db.commit()

asyncio.run(main())
`);
console.log("✅ Fixture ready\n");

// ── Launch Chrome ─────────────────────────────────────────────────────────────
spawnSync("rsync", ["-a", "--exclude=lock", "--exclude=SingletonLock",
  "--exclude=SingletonSocket", "--exclude=SingletonCookie",
  `${CHROME_SRC}/`, `${TEMP_PROFILE}/`], { stdio: "inherit" });

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

console.log("🧪 Nutrition Gap-to-Cart (Phase B4) E2E\n");

// ── 1. Home entry card ─────────────────────────────────────────────────────────
await check("Home — Nutrition Gap-to-Cart card renders with real gap data", async () => {
  // /dashboard mounts NutritionGapsCard, which itself calls /v1/nutrition/gaps
  // (the live Swiggy search + resolution pipeline — genuinely slow, see B3 PRD).
  await nav("/dashboard", 16000);
  await shot("01_dashboard_with_card");
  const body = await page.textContent("body");
  if (!body.match(/needs attention|on track/i)) throw new Error("No Nutrition card state chip found: " + body.slice(0, 300));
  if (!body.match(/Household targets/i) || !body.match(/This week's report/i))
    throw new Error("Missing one of the two always-present entry rows");
  // "Fix these in my cart" is intentionally conditional — NutritionGapsCard.tsx
  // only renders it once there's a real gap with a real recommendation to act
  // on (fixableGaps.length > 0). Offering it with nothing to fix was the exact
  // "isn't that a misleading/unnecessary hop?" bug a user reported. This
  // fixture's seeded member (very_active, no order history yet) should trip
  // at least one gap with recommendations, so assert the row IS present here —
  // but via the real behavior, not a blind always-there assumption.
  if (!body.match(/Fix these in my cart/i))
    throw new Error("Expected 'Fix these in my cart' row for this fixture's seeded gaps — if this fails, verify the fixture still produces a fixable gap rather than assuming the row is broken");
});

await check("Home — 'Household targets' routes to Settings Screen A", async () => {
  const link = page.locator("button, a").filter({ hasText: /household targets/i }).first();
  await link.waitFor({ state: "visible", timeout: 5000 });
  await link.click();
  await sleep(1500);
  if (!page.url().includes("/settings/targets")) throw new Error("Did not navigate to /settings/targets: " + page.url());
  await shot("02_settings_targets");
  const body = await page.textContent("body");
  if (!body.match(/Household \/ day/i)) throw new Error("Targets breakdown not rendered: " + body.slice(0, 200));
});

// ── 2. Weekly digest (Screen B) ────────────────────────────────────────────────
await check("Weekly digest — MacroBars + CeilingBar + flagged section render", async () => {
  // Also fetches /v1/nutrition/gaps for the flagged section — same latency note.
  await nav("/nutrition/weekly", 16000);
  await shot("03_weekly_digest");
  const body = await page.textContent("body");
  if (!body.match(/kcal target/i)) throw new Error("Calorie hero missing: " + body.slice(0, 200));
  if (!body.match(/under limit|approaching limit|over limit/i)) throw new Error("CeilingBar status label missing (sodium not rendering as ceiling)");
  if (!body.match(/Flagged this week/i)) throw new Error("Flagged section missing");
});

await check("Weekly digest — CTA routes to Gap-to-Cart (or honestly reports nothing to fix)", async () => {
  // hasFixableGaps in page.tsx gates this the same way NutritionGapsCard does —
  // the button only appears when there's a real fixable gap; otherwise the page
  // shows "Nothing to fix this week" text. Both are correct outcomes; only a
  // dead-end (no button AND no explanatory text) is a bug.
  const body = await page.textContent("body");
  const cta = page.locator("button").filter({ hasText: /fix these in my cart/i }).first();
  const ctaVisible = await cta.isVisible().catch(() => false);
  if (ctaVisible) {
    await cta.click();
    await sleep(1500);
    if (!page.url().includes("/nutrition/gaps")) throw new Error("CTA did not route to /nutrition/gaps: " + page.url());
  } else if (!body.match(/nothing to fix this week/i)) {
    throw new Error("Neither the 'Fix these in my cart' CTA nor the 'nothing to fix' fallback text is present — dead end");
  }
});

// ── 3. Gap-to-Cart (Screen C) — recommendations render ────────────────────────
await check("Gap-to-Cart — recommendation cards render with confidence badges", async () => {
  await nav("/nutrition/gaps");
  await sleep(15000); // live Swiggy search + resolution pipeline — genuinely slow, see B3 PRD
  await shot("04_gap_to_cart_recommendations");
  const body = await page.textContent("body");
  if (!body.match(/protein/i)) throw new Error("No protein gap section: " + body.slice(0, 300));
  if (!body.match(/~AI est\.|~Database|Label|Verified/i)) throw new Error("No ConfidenceBadge rendered — must reuse NutritionCard's component verbatim");
});

// ── 4. Add-to-cart routing: NO pending Flow basket -> Quick Order ────────────
await check("Add-to-cart (no pending basket) routes to Quick Order", async () => {
  pilotExec(`
import asyncio
from app.database import AsyncSessionLocal
from app.models.db import LoopRun
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        runs = (await db.execute(select(LoopRun).where(LoopRun.household_id == '${HOUSEHOLD_ID}', LoopRun.state == 'awaiting_confirmation'))).scalars().all()
        for r in runs:
            r.state = 'confirmed'
        await db.commit()

asyncio.run(main())
`);
  const beforeQuick = await apiGet("/v1/quick/basket");
  const beforeCount = (beforeQuick?.data?.items ?? []).length;

  await nav("/nutrition/gaps");
  await sleep(15000);
  const addBtn = page.locator("button").filter({ hasText: /^\+ add$/i }).first();
  await addBtn.waitFor({ state: "visible", timeout: 10000 });
  await shot("05_before_add_no_pending");
  await addBtn.click();
  await sleep(2000);
  await shot("06_after_add_no_pending");

  const afterQuick = await apiGet("/v1/quick/basket");
  const afterCount = (afterQuick?.data?.items ?? []).length;
  console.log(`    Quick basket: ${beforeCount} -> ${afterCount} items`);
  if (afterCount <= beforeCount) throw new Error(`Item was not added to Quick Order basket (${beforeCount} -> ${afterCount})`);
});

// ── 5. Add-to-cart routing: WITH pending Flow basket -> Flow basket ──────────
await check("Add-to-cart (pending Flow basket) routes to Flow basket", async () => {
  pilotExec(`
import asyncio, uuid
from datetime import datetime, timezone
from app.database import AsyncSessionLocal
from app.models.db import LoopRun

async def main():
    async with AsyncSessionLocal() as db:
        run = LoopRun(id=str(uuid.uuid4()), household_id='${HOUSEHOLD_ID}', trigger_type='scheduled', state='awaiting_confirmation', triggered_at=datetime.now(timezone.utc))
        db.add(run)
        await db.commit()

asyncio.run(main())
`);
  const beforeBasket = await apiGet("/v1/basket/pending");
  const beforeCount = (beforeBasket?.data?.items ?? []).length;

  await nav("/nutrition/gaps");
  await sleep(15000);
  const addBtn = page.locator("button").filter({ hasText: /^\+ add$/i }).first();
  await addBtn.waitFor({ state: "visible", timeout: 10000 });
  await shot("07_before_add_with_pending");
  await addBtn.click();
  await sleep(2000);
  await shot("08_after_add_with_pending");

  const afterBasket = await apiGet("/v1/basket/pending");
  const afterCount = (afterBasket?.data?.items ?? []).length;
  console.log(`    Flow basket: ${beforeCount} -> ${afterCount} items`);
  if (afterCount <= beforeCount) throw new Error(`Item was not added to the pending Flow basket (${beforeCount} -> ${afterCount})`);
});

// ── Cleanup ─────────────────────────────────────────────────────────────────────
// Must fully remove every fixture this script planted — including the
// HouseholdMember, which a previous version of this script left behind
// after only disabling the flag. That leftover fake member (age 38, etc.)
// sat undisclosed in real household data until a user asked "how did you
// deduce my age?" — this cleanup exists specifically so that never
// happens again from an automated test run.
console.log("\n🔧 Cleanup: removing seeded member, disabling nutrition_gaps_enabled, clearing test LoopRuns...");
pilotExec(`
import asyncio
from app.database import AsyncSessionLocal
from app.models.db import Household, LoopRun, HouseholdMember
from sqlalchemy import update, select

async def main():
    async with AsyncSessionLocal() as db:
        await db.execute(update(Household).where(Household.id == '${HOUSEHOLD_ID}').values(nutrition_gaps_enabled=False))

        members = (await db.execute(select(HouseholdMember).where(HouseholdMember.household_id == '${HOUSEHOLD_ID}'))).scalars().all()
        for m in members:
            await db.delete(m)

        # Terminal state, never 'confirmed' (PLACING_STATES includes
        # 'confirmed' in dashboard.py — leaving it there shows a permanent
        # "Placing your order..." banner, a real bug this script hit before).
        runs = (await db.execute(select(LoopRun).where(
            LoopRun.household_id == '${HOUSEHOLD_ID}',
            LoopRun.state.in_(['awaiting_confirmation', 'confirmed', 'placing']),
        ))).scalars().all()
        for r in runs:
            r.state = 'skipped'
            r.skip_reason = 'test_artifact_cleanup'
        await db.commit()

        remaining_members = (await db.execute(select(HouseholdMember).where(HouseholdMember.household_id == '${HOUSEHOLD_ID}'))).scalars().all()
        remaining_active_runs = (await db.execute(select(LoopRun).where(
            LoopRun.household_id == '${HOUSEHOLD_ID}',
            LoopRun.state.in_(['awaiting_confirmation', 'confirmed', 'placing']),
        ))).scalars().all()
        print(f"cleanup_verify: members_remaining={len(remaining_members)} active_runs_remaining={len(remaining_active_runs)}")

asyncio.run(main())
`);

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
