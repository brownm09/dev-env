#!/usr/bin/env node
/**
 * Purpose: is a repo's Switchyard WIP lane free? Read-only.
 *
 * Usage: node switchyard-lane.js [owner/repo]
 *
 * Evaluates the SAME predicate the coordinator enforces, rather than anything adjacent to it.
 * Cited by symbol, never by line number -- a line number drifts silently, and this script's
 * guard pins the literals below, not where they sit:
 *
 *   - wipHold()             src/admission.ts  the cap check
 *   - openAttemptsByRepo()  src/store.ts      EVERY run's open attempts, no run filter, with
 *                                             the `checks` branch unioned in: a merge or build
 *                                             check under way holds its item's slot too
 *   - OPEN_OR_ORPHAN        src/store.ts      status IN ('dispatched','running'), OR an
 *                                             'interrupted' attempt whose orphan escalation
 *                                             is still open
 *
 * Deliberately does NOT use `switchyard status --json` -> lanes.by_lane: that is built from
 * store.holdingItems(run.id), which is scoped to ONE run, and laneStatus's own comment says
 * "Nothing enforces it." It is a Little's-Law observation, not the cap.
 *
 * This is a convenience pre-check. The authoritative answer is always the coordinator's own
 * refusal: dispatch and read the `dispatch_held` event ({cap, scope, limit, current, heldBy?,
 * heldByRun?, until?}). The caps are fail-safe, so an attempt that is held waits rather than
 * colliding.
 */
const fs = require('fs');
const path = require('path');

const SWITCHYARD = process.env.SWITCHYARD_SRC || 'C:/Users/brown/Git/switchyard';
const DB = process.env.SWITCHYARD_DB || path.join(require('os').homedir(), '.switchyard', 'state.db');

const USAGE = 'Usage: node switchyard-lane.js [owner/repo]   (env: SWITCHYARD_SRC, SWITCHYARD_DB)';
const arg = process.argv[2];
if (arg === '-h' || arg === '--help') {
  console.log(USAGE);
  process.exit(0);
}
const repoArg = arg || 'merickvaughn/lifting-logbook';
// Answering for something that isn't a repo would print "LANE FREE for --json" -- the one output
// shape this script must never produce. A mistyped flag is rejected, not answered.
if (!/^[\w.-]+\/[\w.-]+$/.test(repoArg)) {
  console.error(`REFUSING TO ANSWER: "${repoArg}" is not an owner/repo.`);
  console.error(USAGE);
  process.exit(2);
}

function refuse(lines) {
  for (const line of lines) console.error(line);
  process.exit(2);
}

function readSource(relative) {
  const full = path.join(SWITCHYARD, relative);
  try {
    return fs.readFileSync(full, 'utf8');
  } catch (err) {
    return refuse([
      `REFUSING TO ANSWER: could not read ${relative} (${err.code || err.message}).`,
      `  looked in: ${full}`,
      '  Point SWITCHYARD_SRC at a switchyard checkout.',
    ]);
  }
}

// The predicate and the caps are written out below. Guard both against source drift: if the
// sources no longer contain these literals, what this script encodes has moved and it must not
// answer. Both guards run before the store is opened, so a drifted checkout costs nothing.
const storeSrc = readSource('src/store.ts');
const STORE_EXPECTED = [
  `const OPEN_ATTEMPT = "status IN ('dispatched', 'running')"`,
  `a.status = 'interrupted'`,
  `e.orphan_attempt = a.number AND e.status = 'open'`,
  `c.left_behind = 0`,
];
const missingStore = STORE_EXPECTED.filter((lit) => !storeSrc.includes(lit));
if (missingStore.length > 0) {
  refuse([
    'REFUSING TO ANSWER: src/store.ts no longer matches the predicate this script encodes.',
    ...missingStore.map((m) => '  not found: ' + m),
    'Re-read OPEN_ATTEMPT / OPEN_OR_ORPHAN in src/store.ts and update this script.',
  ]);
}

// Caps: the defaults in src/admission.ts, unless the dispatch config overrides dispatch.lanes.wip.
// Every cap is read anchored to its own group. An unanchored /perRepo:\s*(\d+)/ would take
// whichever group comes first in the file, so reordering `wip` and `awaitingMerge` would quietly
// print the awaiting-merge cap as the lane's -- the very confusion this script exists to end.
const admission = readSource('src/admission.ts');
const ADMISSION_EXPECTED = ['wip: {', 'awaitingMerge: {'];
const missingAdmission = ADMISSION_EXPECTED.filter((lit) => !admission.includes(lit));
if (missingAdmission.length > 0) {
  refuse([
    'REFUSING TO ANSWER: src/admission.ts no longer has the cap groups this script reads.',
    ...missingAdmission.map((m) => '  not found: ' + m),
    'Re-read DEFAULT_LANE_POLICY in src/admission.ts and update this script.',
  ]);
}
const cap = (group, key) => {
  const m = admission.match(new RegExp(`${group}:\\s*\\{[^}]*\\b${key}:\\s*(\\d+)`));
  return m ? Number(m[1]) : NaN;
};
const perRepo = cap('wip', 'perRepo');
const overall = cap('wip', 'overall');
const onSub = cap('wip', 'overallOnSubscription');
const awaitingCap = cap('awaitingMerge', 'perRepo');
if ([perRepo, overall, onSub, awaitingCap].some(Number.isNaN)) {
  refuse(['REFUSING TO ANSWER: could not read the caps out of src/admission.ts.']);
}

let Database;
try {
  Database = require(path.join(SWITCHYARD, 'node_modules/better-sqlite3'));
} catch (err) {
  refuse([
    `REFUSING TO ANSWER: could not load better-sqlite3 from ${SWITCHYARD} (${err.code || err.message}).`,
    '  Run `npm install` in that checkout, or point SWITCHYARD_SRC at one that has it.',
  ]);
}

let db;
try {
  db = new Database(DB, { readonly: true });
} catch (err) {
  refuse([
    `REFUSING TO ANSWER: could not open the store at ${DB} (${err.code || err.message}).`,
    '  Point SWITCHYARD_DB at an existing switchyard state database.',
  ]);
}

// One query, both counts. The per-repo lane and the overall cap are derived from the same rows,
// as wipHold() derives them from one openAttemptsByRepo() call -- so the two can never diverge
// here by one of them forgetting the `checks` branch.
let holders;
let openOverall;
let awaiting;
try {
  const all = db.prepare(`
    SELECT a.run_id, a.item_id, i.repo, a.status, a.number, 'attempt' AS kind
    FROM attempts a JOIN items i ON i.run_id = a.run_id AND i.id = a.item_id
    WHERE ( a.status IN ('dispatched','running')
         OR ( a.status = 'interrupted'
              AND EXISTS (SELECT 1 FROM escalations e
                          WHERE e.run_id = a.run_id AND e.item_id = a.item_id
                            AND e.orphan_attempt = a.number AND e.status = 'open') ) )
    UNION ALL
    SELECT c.run_id, c.item_id, i.repo, 'check', NULL, 'check'
    FROM checks c JOIN items i ON i.run_id = c.run_id AND i.id = c.item_id
    WHERE c.left_behind = 0
  `).all();
  holders = all.filter((h) => h.repo.toLowerCase() === repoArg.toLowerCase());
  openOverall = all.length;
  awaiting = db.prepare(
    "SELECT COUNT(*) n FROM items WHERE lower(repo)=lower(?) AND status='awaiting_merge'",
  ).get(repoArg).n;
} finally {
  db.close();
}

console.log(`repo: ${repoArg}`);
console.log(`db:   ${DB}`);
console.log('');
console.log(`wip.perRepo              ${holders.length} / ${perRepo}   <- THE LANE`);
console.log(`wip.overall              ${openOverall} / ${overall}`);
console.log(`wip.overallOnSubscription  (cap ${onSub} when billed to the subscription)`);
console.log(`awaitingMerge.perRepo    ${awaiting} / ${awaitingCap}   <- a DIFFERENT cap; does not occupy the lane`);
console.log('');
if (holders.length === 0) {
  console.log('LANE FREE for ' + repoArg);
} else {
  console.log('LANE HELD by:');
  for (const h of holders) console.log(`  ${h.kind} ${h.run_id} ${h.item_id} status=${h.status}`);
  console.log('');
  console.log('An interrupted attempt is released by answering or lapsing its orphan escalation.');
}
console.log('');
console.log('Authoritative check: dispatch and read the coordinator\'s own `dispatch_held` event.');
