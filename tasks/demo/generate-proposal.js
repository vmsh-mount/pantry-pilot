const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  PageBreak, LevelFormat, convertInchesToTwip, VerticalAlign,
  Header, Footer, PageNumber, TabStopType, TabStopPosition,
} = require("docx");
const fs = require("fs");

const NAVY = "1B2A4A";
const GREEN = "2D6A4F";
const GOLD = "B8860B";
const GRAY = "5A5A5F";
const LIGHT = "F4F1EA";

let sectionCounter = 0;
const toc = [];

const numbering = {
  config: [
    {
      reference: "principles",
      levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.START,
          style: { paragraph: { indent: { left: convertInchesToTwip(0.4), hanging: convertInchesToTwip(0.25) } } } },
      ],
    },
    {
      reference: "bullets",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.START,
          style: { paragraph: { indent: { left: convertInchesToTwip(0.35), hanging: convertInchesToTwip(0.2) } } } },
      ],
    },
  ],
};

// h1() numbers itself and records the title for the Contents page — single
// source of truth, so the outline and the section headings can never drift.
function h1(text) {
  sectionCounter += 1;
  toc.push({ num: sectionCounter, title: text });
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 480, after: 240 },
    children: [new TextRun({ text: `${sectionCounter}.  ${text}`, bold: true, color: NAVY, size: 30 })],
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 320, after: 140 },
    children: [new TextRun({ text, bold: true, color: GREEN, size: 24 })],
  });
}

function body(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 160, line: 300 },
    children: [new TextRun({ text, size: 22, color: "222222", italics: !!opts.italics })],
  });
}

function lede(text) {
  return new Paragraph({
    spacing: { after: 280, line: 320 },
    children: [new TextRun({ text, size: 25, color: NAVY, italics: true })],
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { after: 100, line: 280 },
    children: [new TextRun({ text, size: 22, color: "222222" })],
  });
}

function principle(num, title, text) {
  return [
    new Paragraph({
      spacing: { before: 240, after: 60 },
      shading: { type: ShadingType.CLEAR, fill: LIGHT },
      indent: { left: convertInchesToTwip(0.1) },
      children: [
        new TextRun({ text: `${num}.  `, bold: true, color: GOLD, size: 23 }),
        new TextRun({ text: title, bold: true, color: NAVY, size: 23 }),
      ],
    }),
    new Paragraph({
      spacing: { after: 200, line: 290 },
      indent: { left: convertInchesToTwip(0.3) },
      children: [new TextRun({ text, size: 21, color: "333333" })],
    }),
  ];
}

function pullquote(text, attribution) {
  const kids = [
    new Paragraph({
      spacing: { before: 200, after: attribution ? 60 : 200 },
      border: { left: { color: GOLD, style: BorderStyle.SINGLE, size: 24, space: 12 } },
      indent: { left: convertInchesToTwip(0.25) },
      children: [new TextRun({ text, italics: true, bold: true, color: NAVY, size: 26 })],
    }),
  ];
  if (attribution) {
    kids.push(new Paragraph({
      spacing: { after: 200 },
      indent: { left: convertInchesToTwip(0.25) },
      border: { left: { color: GOLD, style: BorderStyle.SINGLE, size: 24, space: 12 } },
      children: [new TextRun({ text: attribution, italics: true, color: GRAY, size: 20 })],
    }));
  }
  return kids;
}

function dayBeat(day, text) {
  return new Paragraph({
    spacing: { after: 160, line: 290 },
    children: [
      new TextRun({ text: `${day}  `, bold: true, color: GREEN, size: 22 }),
      new TextRun({ text, size: 22, color: "222222" }),
    ],
  });
}

function cell(text, opts = {}) {
  return new TableCell({
    width: { size: opts.width || 3000, type: WidthType.DXA },
    shading: opts.shading ? { type: ShadingType.CLEAR, fill: opts.shading } : undefined,
    verticalAlign: VerticalAlign.CENTER,
    margins: { top: 100, bottom: 100, left: 120, right: 120 },
    children: [new Paragraph({
      children: [new TextRun({ text, bold: !!opts.bold, size: 20, color: opts.color || "222222" })],
    })],
  });
}

// cantSplit stops a row from breaking across a page — without it, a table
// that lands near a page boundary can shear a row in half, stranding a
// header-only fragment on the next page (the exact bug this fixes).
function row(cells) {
  return new TableRow({ cantSplit: true, children: cells });
}

const compareTable = new Table({
  width: { size: 9350, type: WidthType.DXA },
  columnWidths: [3117, 3117, 3116],
  rows: [
    new TableRow({
      tableHeader: true,
      cantSplit: true,
      children: [
        cell("Capability", { bold: true, color: "FFFFFF", shading: NAVY, width: 3117 }),
        cell("Typical quick-commerce flow today", { bold: true, color: "FFFFFF", shading: NAVY, width: 3117 }),
        cell("PantryPilot", { bold: true, color: "FFFFFF", shading: NAVY, width: 3116 }),
      ],
    }),
    row([
      cell("Reordering", { width: 3117 }), cell("Fully manual, every single week", { width: 3117 }),
      cell("Senses, plans, and asks for one confirm", { width: 3116, shading: LIGHT }) ]),
    row([
      cell("Recurring purchases", { width: 3117 }), cell("Manual repeat, or forgotten entirely", { width: 3117 }),
      cell("Routines — set once, fires on schedule", { width: 3116, shading: LIGHT }) ]),
    row([
      cell("Nutrition visibility", { width: 3117 }), cell("None", { width: 3117 }),
      cell("Real macros + micronutrients, every order", { width: 3116, shading: LIGHT }) ]),
    row([
      cell("Nutrition action", { width: 3117 }), cell("N/A", { width: 3117 }),
      cell("Gap detected → ranked fix → one tap to cart", { width: 3116, shading: LIGHT }) ]),
    row([
      cell("Personalization", { width: 3117 }), cell("Household-level at best", { width: 3117 }),
      cell("Per-member targets — age, sex, weight, activity", { width: 3116, shading: LIGHT }) ]),
    row([
      cell("Trust model", { width: 3117 }), cell("N/A", { width: 3117 }),
      cell("Confidence disclosed per item, never smoothed over", { width: 3116, shading: LIGHT }) ]),
  ],
});

// ── Build body content first so h1() can populate the Contents list below ──

const bodyChildren = [
  h1("The Thesis"),
  lede("Quick commerce solved speed. Nobody has solved thinking about groceries at all."),
  body("Every quick-commerce app in India today competes on the same axis — minutes to doorstep. That race is close to won, and won means commoditized: 10-minute delivery is table stakes across Instamart, Blinkit, and Zepto alike. The next competitive edge isn’t going to be found in logistics. It’s going to be found in whether the app disappears into a household’s life, or stays one more weekly chore."),
  body("PantryPilot is that disappearance. It removes the weekly grocery decision entirely — and while it’s already inside the loop of what a household buys, it does something nobody in Indian grocery delivery currently does: it tells that household what they actually ate this week, and puts the fix for what they’re missing directly in their cart. Not a diet app. Not a wellness dashboard nobody opens. A grocery order that already knows."),

  h1("Why Now"),
  bullet("Delivery speed has become a hygiene factor, not a differentiator — every serious player already promises minutes, not hours."),
  bullet("Retention in grocery delivery is brutally hard, and not because delivery is slow. It’s because remembering to order is itself the friction — every week, forever, for the life of the user relationship."),
  bullet("Health and nutrition awareness in urban India is rising sharply — fitness tracking, label scrutiny, protein-conscious diets — but it lives in apps that have never once talked to a grocery cart."),
  bullet("Nobody has connected “what my family actually eats” to “what I’m buying,” even though the second is the direct, unambiguous evidence for the first. Swiggy already has the order history. Nobody has turned it into an experience."),
  body("That’s the gap. And it’s a gap PantryPilot is built to close using data Instamart already generates — not new infrastructure, not a new checkout, not a new catalog. A layer, not a rebuild."),

  h1("The Insight"),
  body("Two truths, unconnected today, that PantryPilot treats as one problem:"),
  bullet("People don’t want to shop for groceries. They want groceries to have already been thought about."),
  bullet("Nobody can answer a simple, honest question about their own household — “did we get enough protein and iron into three growing kids this week?” — not because they don’t care, but because no system connects a grocery receipt to a nutrition fact."),
  body("If you’re already going to build the intelligence to know what a pantry needs, you might as well know what a body needs too. Those aren’t two products. They’re the same loop, run twice on the same data."),

  h1("Product Philosophy"),
  body("Five beliefs shaped every design decision in this product — not features, commitments:"),
  ...principle(1, "Confirm, don’t decide.",
    "Automation earns the right to act by asking first, every time, until trust is earned. The WhatsApp confirm isn’t a formality — it’s the entire reason this can be called “autonomous” without being alarming. There is no code path in this product where an order is placed without an explicit human yes."),
  ...principle(2, "Every gram is accounted for, honestly.",
    "A calorie estimate for fresh spinach and a lab-verified label for packaged paneer are never shown as if they carry the same certainty. Confidence is disclosed per item, not smoothed into one comforting number."),
  ...principle(3, "The system should get quieter as it gets better.",
    "Every edit a user makes — remove this, add that — is a signal, not a nuisance. A version of PantryPilot that has earned trust asks less and gets more right the first time."),
  ...principle(4, "Nutrition without judgment.",
    "This is not a diet app and never will be. It never tells anyone what they should want. It states, factually, what a week of real orders contained against what their own household actually needs — and hands them a one-tap way to close a gap, not a lecture about willpower."),
  ...principle(5, "It rides on what already exists.",
    "PantryPilot doesn’t ask Swiggy to change checkout, catalog, or logistics. It’s a layer on Instamart’s own MCP surface — every order still ships through Swiggy’s existing fulfillment, address book, and catalog. What changes isn’t the core business. What changes is how often, and how intelligently, someone decides to buy."),

  h1("A Week With PantryPilot"),
  body("This is what the loop feels like from inside a household — no dashboards, no logging, nothing to remember.", { italics: true }),
  dayBeat("Monday", "the pantry senses low — Sunday’s rice and dal are thinning. The system knows before anyone opens an app."),
  dayBeat("Tuesday, morning", "a WhatsApp message arrives with a ready basket — rice, dal, milk, curd, tomatoes, priced and built from the household’s own habits. One tap: confirmed. Order placed."),
  dayBeat("Tuesday, evening", "the order lands, and within seconds so does its nutrition — calories and protein tallied, a quiet note that iron has been trending low across the last two orders."),
  dayBeat("Wednesday", "a last-minute dinner plan means paneer and bananas through Quick Order — no re-planning, just search and add."),
  dayBeat("Thursday", "the weekly digest lands on the dashboard: protein’s on target, but iron and fiber are short this week. Beneath it, three ranked, in-stock, priced suggestions — spinach, dates, oats — one tap away from fixing exactly that."),
  dayBeat("Friday", "milk and curd arrive on their own — the Routine set up weeks ago, quietly doing its job. No decision required."),
  dayBeat("Sunday", "the dashboard reads “on track.” Nobody opened a calorie tracker. Nobody remembered to reorder anything. The loop closed itself."),

  h1("How It Works"),
  h2("Flow — the autonomous ordering engine"),
  body("A five-stage pipeline: Sense (current pantry state and consumption pace), Plan–Rules (a deterministic reorder-threshold engine, diet- and allergy-aware), Plan–LLM (a model pass that catches category gaps the rules engine can’t — “no vegetables this week”), Optimize (resolves every generic item to a real, priced, in-stock SKU), and Confirm & Place (a WhatsApp basket the household can edit or approve — every edit becomes a preference signal for next time)."),
  h2("The nutrition engine"),
  body("Instamart’s catalog and MCP surface don’t return nutrition-label data — there’s no calorie or macro field on a SKU today, so this can’t be a simple lookup. Every order is instead resolved through a confidence-ranked waterfall built entirely outside Swiggy’s data — Open Food Facts for labelled packaged goods, USDA FoodData Central as fallback, and an LLM estimate (always disclosed as an estimate) for fresh produce and anything with no database match. The result rolls into a weekly household digest, checked against per-member targets computed with the Mifflin-St Jeor equation — not a flat household average, a real number for the very-active parent and a different real number for the child."),
  h2("Gap-to-Cart"),
  body("For every nutrient a household came in short on, the system searches the live catalog, ranks candidates by nutrient delivered per rupee, filters to diet-safe and in-stock, and puts the top picks one tap from the cart that’s already open. It doesn’t diagnose a problem and leave. It closes it."),

  h1("Why This Wins for Swiggy"),
  bullet("Order frequency — Routines convert forgettable, recurring purchases into standing orders, a direct lever on frequency that doesn’t require the user to think weekly."),
  bullet("Completion rate — Flow delivers baskets pre-built and priced; confirming a ready basket is a lower-friction action than building a cart from a blank search, which should lift confirm-to-placed conversion against a cold-start flow."),
  bullet("Basket value — Gap-to-Cart is upsell grounded in a real, personal reason (“your family is short on iron this week”), not a generic “people also bought.” It isn’t selling — it’s telling the household something true, which is the basis for the bet that users act on it more readily."),
  bullet("A data asset nobody else in Indian quick commerce has — household-level nutrition intake, tied to real orders, at scale. Over time, a foundation for partnerships in wellness, insurance, and preventive health that sit entirely outside grocery revenue itself."),
  bullet("Zero infrastructure lift — this rides Swiggy’s existing MCP surface. No new checkout, no new catalog, no new fulfillment. Additive demand generation on rails Swiggy has already built."),

  new Paragraph({ spacing: { before: 200 }, children: [] }),
  compareTable,

  h1("What's Already Real"),
  body("This is not a concept deck. The full loop — onboarding, Flow, Quick Order, Routines, Nutrition Snapshot, Weekly Digest, Gap-to-Cart — is built and functioning end-to-end, and has been run start to finish in internal testing, including a recorded walkthrough."),
  bullet("A real nutrition resolution pipeline — Open Food Facts + USDA + LLM waterfall — not a mocked stub."),
  bullet("Per-member personalized targets using an established clinical formula, not a flat household guess."),
  bullet("A working WhatsApp confirm-and-edit loop, with edits recorded as real preference signals."),
  bullet("Quick Order, live today — direct search, add, and checkout for anything that shouldn’t wait for the weekly plan, running on the same catalog and pricing as Flow."),
  bullet("Routines, live today — recurring purchases (a weekly milk top-up, a monthly detergent restock) set once and fired on schedule, with zero weekly decision cost."),
  body("This is a working product at the “ready for a real pilot” stage — built specifically to prove the thesis above with real households, not to describe it."),
  body("Worth stating plainly rather than leaving implicit: this is pre-launch, with no live user base yet, and it currently runs this demo against a curated internal catalog rather than live Swiggy data — Instamart’s MCP endpoint has been returning 403 on core calls since July 26. Same code path, same parsing logic, a substituted data source, disclosed here rather than hidden. Restoring that access is the first item below.", { italics: true }),

  h1("The Proposal"),
  bullet("Live MCP access restored and confirmed, so ongoing development and demos run against real Instamart data going forward."),
  bullet("Staging environment whitelist — a staging endpoint already exists but currently rejects our token; whitelisting removes the need for any dry-run workaround during testing."),
  bullet("A defined pilot: a real cohort of Instamart households, live for a fixed window, measured against the numbers Swiggy already tracks — order frequency, basket value, and repeat-order retention — plus two adoption metrics specific to this product: share of households with an active Routine, and Quick Order’s share of total orders placed. Together they’re the direct evidence for the frequency and completion-rate gains claimed above, not just the household-level totals."),
  bullet("A path to WhatsApp Business template approval, if the pilot moves toward production messaging at scale — BSP choice still open."),

  h1("Vision Forward"),
  body("Beyond groceries: the same loop — sense, plan, confirm, close — extends naturally to any recurring household need Instamart already delivers. Not a new product each time. The same pattern, applied wider."),
  body("A genuine household health layer: not a diet app, but the first system that can honestly say what a family ate this month, sourced from what they actually bought — a foundation for wellness, insurance, and preventive-health partnerships no delivery app currently has the data to support."),
  body("The long-term bet is simple: grocery delivery stops being a search-and-cart experience and becomes an ambient one — the app that knows before you do, and only shows up to ask, “is this right?”"),

  // Flows directly after "Vision Forward" rather than forcing a fresh,
  // mostly-empty page — a forced break here stranded the quote alone
  // under a wall of blank space below it.
  new Paragraph({ spacing: { before: 500 }, children: [] }),
  ...pullquote(
    "We didn’t build another way to shop for groceries faster. We built the thing that means you stop having to shop for them at all — and while it’s at it, it keeps an honest eye on whether your family’s actually eating well."
  ),
];

// ── Contents — built from the same h1() calls that number the sections, so
//    it can never list a title differently than the heading itself says ──

const contentsChildren = [
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 0, after: 300 },
    children: [new TextRun({ text: "Contents", bold: true, color: NAVY, size: 30 })],
  }),
  ...toc.map((item) => new Paragraph({
    tabStops: [{ type: TabStopType.LEFT, position: convertInchesToTwip(0.4) }],
    indent: { left: convertInchesToTwip(0.4), hanging: convertInchesToTwip(0.4) },
    spacing: { after: 140 },
    children: [
      new TextRun({ text: `${item.num}.\t`, size: 23, color: "222222" }),
      new TextRun({ text: item.title, size: 23, color: "222222" }),
    ],
  })),
  new Paragraph({ children: [new PageBreak()] }),
];

const footer = new Footer({
  children: [
    new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      border: { top: { color: "CCCCCC", style: BorderStyle.SINGLE, size: 4, space: 4 } },
      children: [
        new TextRun({ text: "PantryPilot — Product Proposal", size: 16, color: GRAY }),
        new TextRun({ text: "\tPage ", size: 16, color: GRAY }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: GRAY }),
      ],
    }),
  ],
});

const doc = new Document({
  numbering,
  styles: {
    default: {
      document: { run: { font: "Calibri" } },
    },
  },
  sections: [
    // ── Cover page — no header/footer, deliberately blank canvas ─────
    {
      properties: { page: { size: { width: 12240, height: 15840 } } },
      children: [
        new Paragraph({ spacing: { before: 3400 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "PANTRYPILOT", bold: true, color: NAVY, size: 64 })] }),
        new Paragraph({ spacing: { before: 200, after: 100 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "The Autonomous Grocery Layer for Instamart", bold: true, color: GREEN, size: 32 })] }),
        new Paragraph({ spacing: { before: 60 }, alignment: AlignmentType.CENTER,
          border: { top: { color: GOLD, style: BorderStyle.SINGLE, size: 8, space: 14 } },
          children: [new TextRun({ text: " ", size: 4 })] }),
        new Paragraph({ spacing: { before: 400 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "A Product Proposal", italics: true, color: GRAY, size: 26 })] }),
        new Paragraph({ spacing: { before: 100 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Closing the loop from pantry, to plate, to cart", italics: true, color: GRAY, size: 22 })] }),
        new Paragraph({ spacing: { before: 3600 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Prepared for the Swiggy Instamart team", color: GRAY, size: 20 })] }),
        new Paragraph({ spacing: { before: 40 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "July 2026", color: GRAY, size: 18 })] }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },
    // ── Contents ────────────────────────────────────────────────────
    {
      properties: { page: { size: { width: 12240, height: 15840 } } },
      footers: { default: footer },
      children: contentsChildren,
    },
    // ── Main body ───────────────────────────────────────────────────
    {
      properties: { page: { size: { width: 12240, height: 15840 } } },
      footers: { default: footer },
      children: bodyChildren,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("PantryPilot-Product-Proposal.docx", buf);
  console.log("written");
});
