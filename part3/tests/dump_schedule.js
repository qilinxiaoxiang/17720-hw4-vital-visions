// Prints the JS plan (and a level string) for each parameter set given as a
// JSON array on argv[2]. Used by test_schedule_parity.py.
'use strict';
const path = require('path');
const S = require(path.join(__dirname, '..', 'schedule.js'));

const cases = JSON.parse(process.argv[2]);
const out = cases.map((params) => {
  const plan = S.buildPlan(params);
  return { plan: plan, levels: S.levelString(plan), summary: S.summary(plan, 120) };
});
process.stdout.write(JSON.stringify(out));
