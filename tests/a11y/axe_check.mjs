#!/usr/bin/env node
/**
 * AX-1 — axe-core accessibility gate for the SUPER_TRADEMAN dashboard.
 *
 * Scans the running dashboard with axe-core (tags: wcag2a, wcag2aa, wcag21a,
 * wcag21aa) and FAILS (exit 1) on any critical/serious violation.
 *
 * Prereq: dashboard already serving (the a11y.yml workflow starts server.py
 * with API_INSECURE_DEV=1 and a dummy API_SESSION_SECRET before calling us).
 *
 * Usage:
 *   BASE_URL=http://127.0.0.1:8123 node axe_check.mjs
 *   # BASE_URL defaults to http://127.0.0.1:${API_PORT:-8080}
 */
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";

const BASE_URL =
  process.env.BASE_URL || `http://127.0.0.1:${process.env.API_PORT || 8080}`;

const browser = await chromium.launch();
try {
  // @axe-core/playwright requires an explicit BrowserContext (it refuses
  // pages created via browser.newPage()).
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(BASE_URL, { waitUntil: "load", timeout: 30000 });

  // Give the client-side sim a beat to paint its first frames.
  await page.waitForTimeout(750);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  const blocking = results.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious",
  );

  console.log(`axe scan of ${BASE_URL}`);
  console.log(
    `rules run: ${results.testEngine?.version ?? "?"} | passes: ${results.passes.length} | violations: ${results.violations.length}`,
  );
  for (const v of results.violations) {
    const mark = blocking.includes(v) ? "BLOCKING" : "minor";
    console.log(`\n[${mark}] (${v.impact}) ${v.id}: ${v.help}`);
    for (const node of v.nodes.slice(0, 3)) {
      console.log(`   -> ${node.target.join(" ")}`);
    }
    if (v.nodes.length > 3) console.log(`   … +${v.nodes.length - 3} more`);
  }

  console.log(
    `\nSummary: ${results.violations.length} total violation(s), ${blocking.length} critical/serious`,
  );

  if (blocking.length > 0) {
    console.error("FAIL: critical/serious axe violations present");
    process.exit(1);
  }
  console.log("PASS: zero critical/serious violations");
} finally {
  await browser.close();
}
