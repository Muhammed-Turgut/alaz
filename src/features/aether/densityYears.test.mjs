/**
 * Lightweight Node assertions for density year / naming helpers.
 * Run: node src/features/aether/densityYears.test.mjs
 * (No frontend test runner in package.json — keep this self-contained.)
 */
import assert from "node:assert/strict";

const REMOTE_MAX = 10;
const YEAR_OPTS = ["1", "3", "5", "10", "15"];

function filterYearOpts(opts, { isRemote, remoteCap = REMOTE_MAX }) {
  return opts.filter((v) => {
    if (Number(v) <= remoteCap) return true;
    if (isRemote) return false;
    return true;
  });
}

function liveHeatLabel(style, en = true) {
  if (style === "heatmap_frp") {
    return en ? "FRP-weighted hotspot view" : "FRP ağırlıklı hotspot görünümü";
  }
  if (style === "heatmap") {
    return en ? "equal-weight detection density" : "eşit ağırlıklı tespit yoğunluğu";
  }
  return en ? "points" : "noktalar";
}

function effectiveDisplay(requested, effective, remoteCap = REMOTE_MAX) {
  const note =
    requested > effective && effective <= remoteCap
      ? `Asked ${requested}y; remote cap ${remoteCap}y → effective ${effective}y`
      : null;
  return { yearsLabel: effective, note };
}

// --- assertions ---
assert.deepEqual(filterYearOpts(YEAR_OPTS, { isRemote: false }), YEAR_OPTS);
assert.deepEqual(filterYearOpts(YEAR_OPTS, { isRemote: true }), ["1", "3", "5", "10"]);
assert.match(liveHeatLabel("heatmap"), /equal-weight/);
assert.match(liveHeatLabel("heatmap_frp"), /FRP-weighted/);
assert.doesNotMatch(liveHeatLabel("heatmap"), /FRP/);
const d = effectiveDisplay(15, 10);
assert.equal(d.yearsLabel, 10);
assert.match(d.note, /Asked 15y/);
assert.equal(effectiveDisplay(5, 5).note, null);

console.log("densityYears.test.mjs: ok");
