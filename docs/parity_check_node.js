// parity_check_node.js — CI-side twin of the in-browser parity badge.
// Replays the frozen Python vectors through the JS engine; exits non-zero on
// any divergence. Run: node docs/parity_check_node.js
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
console.log(`PARITY OK: JS engine matches Python spec on ${total}/${total} vectors`);
