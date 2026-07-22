// parity_check_node.js — CI-side twin of the in-browser parity badge.
// Replays frozen Python assessment and fail-closed verifier vectors through
// the JS engine; exits non-zero on any divergence.
const fs = require("fs");
const path = require("path");

const cp = require(path.join(__dirname, "collabproof.js"));
const src = fs.readFileSync(path.join(__dirname, "parity_vectors.js"), "utf8");
const window = {};
eval(src); // assigns window.PARITY_VECTORS

const { total, failures } = cp.runParity(window.PARITY_VECTORS);
if (failures.length) {
  console.error(`PARITY FAILED: ${failures.length}/${total} vectors diverge`);
  for (const f of failures.slice(0, 5))
    console.error(JSON.stringify(f, null, 1));
  process.exit(1);
}
const assessmentCount = window.PARITY_VECTORS.assessments?.length || 0;
const verifierCount = window.PARITY_VECTORS.verifications?.length || 0;
console.log(
  `PARITY OK: ${assessmentCount}/${assessmentCount} assessments and ` +
  `${verifierCount}/${verifierCount} verifier cases match Python (${total} total)`
);
