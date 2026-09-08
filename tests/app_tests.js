/*
 * app_tests.js — tests for the app's own JavaScript.
 *
 * WHY THIS EXISTS. Everything else in this suite tests Python. The app's
 * logic - which decides whether a validator reads as down, offline,
 * recovering or steady, and which escapes host names before they are put into
 * HTML - had no automated check at all. The only verification the state
 * reclassification ever got was somebody opening the app and it looking
 * right, on a fleet that happened to be healthy.
 *
 * HOW THE FILES ARE LOADED. The app uses classic scripts, not modules, so
 * there is nothing to import. They are read and evaluated together in one vm
 * context, which is exactly how a browser runs them: one shared global scope,
 * in document order. The globals that live in xxops.html - cfg, prev, hosts,
 * mutedFor and friends - are supplied as stubs, because the point here is the
 * pure logic rather than the page.
 *
 * NO FRAMEWORK, to match the Python side. A failure throws, and the exit code
 * is what tests/test_app_js.py reads.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "app");

let passed = 0;
const failures = [];

function check(name, fn) {
  try {
    fn();
    passed++;
  } catch (e) {
    failures.push(name + "\n      " + (e && e.message ? e.message : String(e)));
  }
}

function eq(got, want, what) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g !== w) throw new Error((what ? what + ": " : "") + "got " + g + ", wanted " + w);
}

function truthy(v, what) {
  if (!v) throw new Error(what || "expected something truthy, got " + JSON.stringify(v));
}

// ---------------------------------------------------------------- the context

function load(files, stubs) {
  const ctx = Object.assign({
    console: Object.assign({}, console, { warn: () => {} }),
    fetch: () => { throw new Error("no network in tests"); },
    document: undefined, window: undefined,
  }, stubs || {});
  vm.createContext(ctx);
  for (const f of files) {
    const p = path.join(APP, f);
    if (!fs.existsSync(p)) throw new Error("missing " + f);
    vm.runInContext(fs.readFileSync(p, "utf8"), ctx, { filename: f });
  }
  return ctx;
}

/*
 * Reach a name declared inside the loaded scripts.
 *
 * A top-level `const` does NOT become a property of the context object - only
 * `function` declarations do - so ctx.esc is undefined even though esc exists.
 * Evaluating a wrapper inside the context returns a real callable instead.
 * Worth knowing: without this, half of these tests would have silently tested
 * nothing.
 */
function ref(ctx, name) {
  return vm.runInContext("(...a) => " + name + "(...a)", ctx);
}
function val(ctx, name) {
  return vm.runInContext(name, ctx);
}

// ------------------------------------------------------------------ formatters

const u = load(["xxops-util.js"], { notify: null, notifyMsg: "",
                                    paintSettings: () => {} });
const esc = ref(u, "esc"), gb = ref(u, "gb");
const dur = ref(u, "dur"), ago = ref(u, "ago");

check("esc escapes everything that can break out of an attribute", () => {
  eq(esc('a & b < c > d " e'), "a &amp; b &lt; c &gt; d &quot; e");
});

check("esc covers the double quote, which is the attribute case", () => {
  // Host names and fix titles are interpolated into href and data- attributes.
  // The server's markdown renderer had exactly this hole today.
  truthy(!esc('x" onmouseover=y').includes('" onmouseover'),
         "a quote escaped the attribute");
});

check("esc turns null and undefined into nothing, not the word", () => {
  eq(esc(null), "");
  eq(esc(undefined), "");
});

check("esc leaves ordinary text alone", () => {
  eq(esc("alpha_gw"), "alpha_gw");
});

check("gb has a dash for missing rather than a number", () => {
  eq(gb(null), "—");
  eq(gb(undefined), "—");
});

check("gb picks a unit a person would use", () => {
  eq(gb(2.5e9), "2.50 GB");
  eq(gb(180e6), "180 MB");
  eq(gb(4e3), "4 KB");
});

check("gb does not report zero bytes as missing", () => {
  // 0 is a real answer and a dash would be a lie.
  truthy(gb(0) !== "—", "zero was reported as unknown");
});

check("dur crosses each boundary the way a person reads it", () => {
  eq(dur(45), "45s");
  eq(dur(90), "2m");
  eq(dur(5400), "1.5h");
  eq(dur(172800), "2.0d");
});

check("ago is relative to now", () => {
  const now = Date.now() / 1000;
  eq(ago(now - 300), "5m ago");
  eq(ago(now - 7200), "2h ago");
  eq(ago(now - 259200), "3d ago");
});

check("identicon is stable for a name", () => {
  eq(u.identicon("alpha", 24), u.identicon("alpha", 24));
});

check("identicon differs between names", () => {
  truthy(u.identicon("alpha", 24) !== u.identicon("bravo", 24),
         "two hosts drew the same avatar");
});

check("identicon does not put the name into the markup", () => {
  // It hashes the name rather than rendering it, so a hostile host name
  // cannot reach the DOM through an avatar.
  truthy(!u.identicon('<script>', 24).includes("<script>"),
         "a name reached the svg unescaped");
});

// ------------------------------------------------------------ classification

function ctxFor(over) {
  return Object.assign({
    cfg: { pairs: { alpha: "alpha_gw" }, stall: 600, lag: 5 },
    prev: {}, hosts: [], specs: {}, discovered: null,
    mutedFor: () => null, mutedScope: () => null,
  }, over || {});
}

function metrics(over) {
  return Object.assign({
    up: { alpha: 1, alpha_gw: 1 },
    gwsvc: { alpha_gw: 1 },
    gwround: { alpha_gw: 500 },
    gwchg30: { alpha_gw: 3 },
    chg30: { alpha: 3 },
    round: { alpha: 1000 },
    errf: { alpha: 0 },
    height: { alpha: 900 },
    authored: { alpha: 10 },
    fails: { alpha: 0 },
    secs: { alpha: 8 },
    ver: {}, bl: {}, roles: {},
  }, over || {});
}

function classify(mOver, ctxOver) {
  const c = load(["xxops-util.js", "xxops-data.js"], ctxFor(ctxOver));
  return c.build(metrics(mOver))[0];
}

check("a healthy validator is steady with no reason", () => {
  const r = classify();
  eq(r.state, "steady");
  eq(r.reason, null);
  eq(r.why, "");
});

check("a node reporting no round metric at all is offline, not steady", () => {
  /*
   * From a real incident: a slashed validator was chilled out of the active
   * set, its cMix wrapper parked forever on "waiting on consensus ready
   * state" and never launched cMix. No cMix process, no round lines, so the
   * producer emitted no round series AT ALL - absent, not frozen.
   *
   * Every stall check is conditional on the metric existing, so the node fell
   * through every branch and read as steady while earning nothing. A node
   * that stops ADVANCING was caught; one that stops REPORTING was not.
   */
  const r = classify({ round: {}, chg30: {} });
  eq(r.state, "offline");
  eq(r.reason, "no-rounds-reported");
});

check("an unreachable host is down, not merely not-reporting", () => {
  // The unreachable check must come first: no metrics at all is a different
  // situation from metrics arriving without a round number in them.
  const r = classify({ up: { alpha: 0, alpha_gw: 1 }, round: {}, chg30: {} });
  eq(r.reason, "node-unreachable");
});

check("an unreachable node host is DOWN, and only that is down", () => {
  const r = classify({ up: { alpha: 0, alpha_gw: 1 } });
  eq(r.state, "down");
  eq(r.reason, "node-unreachable");
});

check("a stuck cmix with an error file is OFFLINE, not down", () => {
  const r = classify({ errf: { alpha: 1 }, chg30: { alpha: 0 } });
  eq(r.state, "offline");
  eq(r.reason, "cmix-stuck");
});

check("up but not in rounds is OFFLINE - the waiting state", () => {
  const r = classify({ chg30: { alpha: 0 } });
  eq(r.state, "offline");
  eq(r.reason, "not-in-rounds");
});

check("an unreachable gateway warns but leaves the validator earning", () => {
  // Rounds are still landing. They will stop, and when they do the
  // not-in-rounds branch above takes over - classify on the observed fact,
  // not the predicted cause.
  const r = classify({ up: { alpha: 1, alpha_gw: 0 } });
  eq(r.state, "recovering");
  eq(r.reason, "gw-unreachable");
});

check("a stalled gateway does not make the validator offline", () => {
  const r = classify({ gwchg30: { alpha_gw: 0 } });
  eq(r.state, "recovering");
  eq(r.reason, "gw-stalled");
});

check("a round failure alone is recovering", () => {
  const r = classify({ errf: { alpha: 1 } });
  eq(r.state, "recovering");
  eq(r.reason, "round-failure");
});

check("a stopped gateway service is recovering - cmix keeps earning", () => {
  const r = classify({ gwsvc: { alpha_gw: 0 } });
  eq(r.state, "recovering");
  eq(r.reason, "gw-service-off");
});

check("chain lag beyond the threshold is recovering", () => {
  const r = classify({ height: { alpha: 900, other: 950 } });
  eq(r.state, "recovering");
  eq(r.reason, "chain-lag");
});

check("the node branches are tested before the gateway ones", () => {
  // Both wrong at once: the node's own state must win, or an operator is
  // sent to look at a gateway while the validator is the problem.
  const r = classify({ chg30: { alpha: 0 }, up: { alpha: 1, alpha_gw: 0 } });
  eq(r.state, "offline");
  eq(r.reason, "not-in-rounds");
});

check("an unreachable node beats everything else", () => {
  const r = classify({ up: { alpha: 0, alpha_gw: 0 },
                       errf: { alpha: 1 }, chg30: { alpha: 0 } });
  eq(r.reason, "node-unreachable");
});

// ------------------------------------------------------------------- wording

const d = load(["xxops-util.js", "xxops-data.js"], ctxFor());
const REASONS = val(d, "REASONS");
const reasonText = ref(d, "reasonText");

check("every reason code the branches can set has wording", () => {
  const src = fs.readFileSync(path.join(APP, "xxops-data.js"), "utf8");
  const set = [...new Set([...src.matchAll(/reason="([a-z-]+)"/g)].map(m => m[1]))];
  truthy(set.length > 5, "found only " + set.length + " reason codes - the parse is wrong");
  const missing = set.filter(c => !(c in REASONS));
  eq(missing, [], "codes with no wording");
});

check("no wording is unreachable", () => {
  const src = fs.readFileSync(path.join(APP, "xxops-data.js"), "utf8");
  const set = new Set([...src.matchAll(/reason="([a-z-]+)"/g)].map(m => m[1]));
  const orphans = Object.keys(REASONS).filter(c => !set.has(c));
  eq(orphans, [], "wording no branch can produce");
});

check("an unknown code says nothing rather than the word undefined", () => {
  eq(reasonText("no-such-code", {}), "");
});

check("no reason produces no sentence", () => {
  eq(reasonText(null, {}), "");
});

check("the two interpolated reasons put the number in", () => {
  truthy(reasonText("gw-stalled", { gwIdle: 42 }).includes("42"));
  truthy(reasonText("chain-lag", { lag: 9 }).includes("9"));
});

check("build always supplies the values the interpolated reasons need", () => {
  /*
   * reasonText("gw-stalled", {}) would render the word "undefined" into a
   * sentence an operator reads. It cannot happen today because there is
   * exactly one call site and it always passes both values - so the property
   * worth asserting is that, rather than pretending the function guards
   * itself. If a second call site ever appears, this is the test that should
   * have been read first.
   */
  const src = fs.readFileSync(path.join(APP, "xxops-data.js"), "utf8");
  const calls = [...src.matchAll(/reasonText\([^\n]*/g)].map(m => m[0]);
  const real = calls.filter(c => !c.includes("code, v"));
  eq(real.length, 1, "expected exactly one call site, found " + real.length
     + ": " + JSON.stringify(real));
  truthy(real[0].includes("gwIdle") && real[0].includes("lag"),
         "the call site does not supply both interpolated values: " + real[0]);
});

// ------------------------------------------------------------------- results

if (failures.length) {
  console.log("\n" + failures.length + " failed, " + passed + " passed\n");
  for (const f of failures) console.log("  FAIL  " + f);
  process.exit(1);
}
console.log(passed + " javascript tests pass");
