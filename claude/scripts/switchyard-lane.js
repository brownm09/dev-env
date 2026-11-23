#!/usr/bin/env node
/**
 * Is a repo's Switchyard WIP lane free? Read-only.
 *
 * Usage: node switchyard-lane.js [owner/repo]
 *
 * Evaluates the SAME predicate the coordinator enforces, rather than anything adjacent to it:
 *
 *   - wipHold()            src/admission.ts   the cap check
 *   - openAttemptsByRepo() src/store.ts:526   EVERY run's open attempts, no run filter
 *   - OPEN_OR_ORPHAN       src/store.ts:184   status IN ('dispatched','running'), OR an
 *                                             'interrupted' attempt whose orphan escalation
 *                                             is still open
 *   - checks.left_behind=0 src/store.ts:535   a check under way holds its item's slot too
 *
 * Deliberately does NOT use `switchyard status --json` -> lanes.by_lane: that is built from
 * store.holdingItems(run.id), which is scoped to ONE run, and laneStatus's own comment says
 * "Nothing enforces it." It is a Little's-Law observation, not the cap.
 *
 * This is a convenience pre-check. The authoritative answer is always the coordinator's own
 * refusal: dispatch and read the `dispatch_held` event ({cap, scope, limit, current, heldBy,
 * heldByRun}). The caps are fail-safe, so an attempt that is held waits rather than colliding.
 */
const fs = require('fs');
const path = require('path');

const SWITCHYARD = process.env.SWITCHYARD_SRC || 'C:/Users/brown/Git/switchyard';
const DB = process.env.SWITCHYARD_DB || path.join(require('os').homedir(), '.switchyard', 'state.db');
const repoArg = process.argv[2] || 'merickvaughn/lifting-logbook';

// The predicate is written out below. Guard it against source drift: if store.ts no longer
// contains these literals, the predicate has moved and this script must not answer.
const storeSrc = fs.readFileSync(path.join(SWITCHYARD, 'src/store.ts'), 'utf8');
const EXPECTED = [
  `const OPEN_ATTEMPT = "status IN ('dispatched', 'running')"`,
  `a.status = 'interrupted'`,
  `e.orphan_attempt = a.number AND e.status = 'open'`,
  `c.left_behind = 0`,
];
const missing = EXPECTED.filter((lit) => !storeSrc.includes(lit));
if (missing.length > 0) {
  console.error('REFUSING TO ANSWER: src/store.ts no longer matches the predicate this script encodes.');
  for (const m of missing) console.error('  not found: ' + m);
  console.error('Re-read OPEN_ATTEMPT / OPEN_OR_ORPHAN in src/store.ts and update this script.');
  process.exit(2);
}

const Database = require(path.join(SWITCHYARD, 'node_modules/better-sqlite3'));
const db = new Database(DB, { readonly: true });

const holders = db.prepare(`
  SELECT a.run_id, a.item_id, i.repo, a.status, a.number, 'attempt' AS kind
  FROM attempts a JOIN items i ON i.run_id = a.run_id AND i.id = a.item_id
  WHERE lower(i.repo) = lower(?)
    AND ( a.status IN ('dispatched','running')
       OR ( a.status = 'interrupted'
            AND EXISTS (SELECT 1 FROM escalations e
                        WHERE e.run_id = a.run_id AND e.item_id = a.item_id
                          AND e.orphan_attempt = a.number AND e.status = 'open') ) )
  UNION ALL
  SELECT c.run_id, c.item_id, i.repo, 'check', NULL, 'check'
  FROM checks c JOIN items i ON i.run_id = c.run_id AND i.id = c.item_id
  WHERE lower(i.repo) = lower(?) AND c.left_behind = 0
`).all(repoArg, repoArg);

// Caps: the defaults in src/admission.ts, unless the dispatch config overrides dispatch.lanes.wip.
const admission = fs.readFileSync(path.join(SWITCHYARD, 'src/admission.ts'), 'utf8');
const perRepo = Number((admission.match(/perRepo:\s*(\d+)/) || [])[1] ?? NaN);
const overall = Number((admission.match(/wip:\s*\{\s*overall:\s*(\d+)/) || [])[1] ?? NaN);
const onSub = Number((admission.match(/overallOnSubscription:\s*(\d+)/) || [])[1] ?? NaN);
if ([perRepo, overall, onSub].some(Number.isNaN)) {
  console.error('REFUSING TO ANSWER: could not read the wip caps out of src/admission.ts.');
  process.exit(2);
}

const allHolders = db.prepare(`
  SELECT i.repo, COUNT(*) n
  FROM attempts a JOIN items i ON i.run_id = a.run_id AND i.id = a.item_id
  WHERE a.status IN ('dispatched','running')
     OR ( a.status = 'interrupted'
          AND EXISTS (SELECT 1 FROM escalations e
                      WHERE e.run_id = a.run_id AND e.item_id = a.item_id
                        AND e.orphan_attempt = a.number AND e.status = 'open') )
  GROUP BY i.repo
`).all();
const openOverall = allHolders.reduce((s, r) => s + r.n, 0);

const awaiting = db.prepare("SELECT COUNT(*) n FROM items WHERE lower(repo)=lower(?) AND status='awaiting_merge'").get(repoArg).n;

console.log(`repo: ${repoArg}`);
console.log(`db:   ${DB}`);
console.log('');
console.log(`wip.perRepo              ${holders.length} / ${perRepo}   <- THE LANE`);
console.log(`wip.overall              ${openOverall} / ${overall}`);
console.log(`wip.overallOnSubscription  (cap ${onSub} when billed to the subscription)`);
console.log(`awaitingMerge.perRepo    ${awaiting} / 3   <- a DIFFERENT cap; does not occupy the lane`);
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
db.close();
