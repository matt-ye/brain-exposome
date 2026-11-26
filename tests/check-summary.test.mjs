/* Result summary, history and report for the personal check-in.
   Run: node tests/check-summary.test.mjs */

import assert from 'node:assert/strict';
import {
  PERSONAL_ITEMS, summarize, snapshot, compareSnapshots, addToHistory, parseHistory,
  shareMessage, reportHtml, itemLabel, localDate,
} from '../src/lib/check-summary.mjs';

const zh = (a) => a;
const en = (_a, b) => b;
const f = (...keys) => keys.map(key => ({ key }));

// Air pollution alone must not make "all fine" unreachable (Taiwan is above WHO 5 µg/m³).
{
  const s = summarize(f('pm25'), {});
  assert.deepEqual(s.improve, []);
  assert.equal(s.band, 'clear');
  assert.deepEqual(s.env, ['pm25']);
  assert.equal(s.fine.length, 13);
}

// Every item lands in exactly one bucket; "unknown" is unsure, not fine and not a risk.
{
  const s = summarize(f('htn', 'smoking-former', 'pm25'), { 'in-diabetes': 'unknown', 'in-hearing': 'unknown' });
  assert.deepEqual(s.improve, ['htn']);
  assert.deepEqual(s.unsure, ['hearing', 'diabetes']);
  assert.equal(s.improve.length + s.unsure.length + s.fine.length, PERSONAL_ITEMS.length);
  assert.ok(!s.fine.includes('diabetes'));
  // Having quit is already done: listed as fine, not as something to work on.
  assert.ok(s.fine.includes('smoking-former') && !s.improve.includes('smoking-former'));
  assert.equal(s.band, 'few');
}

// Nothing is preselected: an unanswered item is "missing", never counted as fine.
{
  const s = summarize([], { 'in-htn': '', 'in-alcohol': '' });
  assert.deepEqual(s.missing, ['htn', 'alcohol']);
  assert.ok(!s.fine.includes('htn') && !s.fine.includes('alcohol'));
  assert.equal(s.improve.length + s.unsure.length + s.fine.length + s.missing.length, PERSONAL_ITEMS.length);
  const html = reportHtml({ date: 'd', sum: s, pm: null, brainAge: null }, zh);
  assert.ok(html.includes('尚未作答') && html.includes('血壓'));
}

// Bands follow the personal count only.
assert.equal(summarize(f('htn', 'diabetes', 'obesity'), {}).band, 'several');
assert.equal(summarize(f('htn', 'diabetes', 'obesity', 'ldl', 'inactive', 'alcohol'), {}).band, 'many');

// Labels resolve for both smoking keys.
assert.equal(itemLabel('smoking-former', zh), '吸菸（已戒）');
assert.equal(itemLabel('smoking', zh), '吸菸');
assert.equal(itemLabel('htn', en), 'Blood pressure');

// History: compare, same-day replace, cap, and tolerate junk in storage.
{
  const r = { meanPM: 17.26, dMid: 4.04 };
  const a = snapshot(summarize(f('htn', 'smoking', 'inactive'), { 'in-diabetes': 'unknown' }), r, '2026-06-01');
  assert.deepEqual(a, { t: '2026-06-01', improve: ['htn', 'inactive', 'smoking'], unsure: ['diabetes'], pm: 17.3, d: 4 });
  const b = snapshot(summarize(f('htn', 'smoking-former'), {}), r, '2026-11-26');
  const c = compareSnapshots(a, b);
  assert.deepEqual(c.improved, ['inactive', 'smoking']);       // quitting counts as an improvement
  assert.deepEqual(c.added, []);
  assert.deepEqual(c.resolvedUnsure, ['diabetes']);
  assert.equal(c.since, '2026-06-01');
  assert.deepEqual([c.before, c.now], [3, 1]);

  let h = addToHistory([], a);
  h = addToHistory(h, b);
  h = addToHistory(h, { ...b, improve: [] });          // same day → replaces
  assert.equal(h.length, 2);
  assert.deepEqual(h[1].improve, []);
  let many = [];
  for (let i = 0; i < 30; i++) many = addToHistory(many, { ...a, t: `2026-01-${String(i + 1).padStart(2, '0')}` });
  assert.equal(many.length, 24);
  assert.equal(many[23].t, '2026-01-30');

  assert.deepEqual(parseHistory('not json'), []);
  assert.deepEqual(parseHistory('{"a":1}'), []);
  assert.deepEqual(parseHistory(JSON.stringify([a, { t: 1 }, null])), [a]);
}

// Local date, not UTC: 07:30 in Taipei on the 26th is still the 25th in UTC.
assert.equal(localDate(new Date(2026, 10, 26, 7, 30)), '2026-11-26');

// The share text invites; it carries no result and no answers.
{
  const msg = shareMessage('https://brain-exposome.mattye.dev/', zh);
  assert.ok(msg.endsWith('https://brain-exposome.mattye.dev/'));
  assert.ok(!/風險因子|腦齡|\+\d/.test(msg), msg);
}

// Report: sections appear only when they have content; text is escaped.
{
  const sum = summarize(f('htn', 'pm25'), { 'in-diabetes': 'unknown' });
  const html = reportHtml({
    date: '2026-11-26', sum, rows: [['年齡', '<62>']], advice: { htn: '規律量血壓' },
    pm: 17.26, pmRef: 5, brainAge: 3.2, url: 'https://x.test/',
  }, zh);
  assert.ok(html.includes('可以開始調整的項目') && html.includes('血壓：規律量血壓'));
  assert.ok(html.includes('下次健檢可以順便確認') && html.includes('血糖'));
  assert.ok(html.includes('17.3 µg/m³') && html.includes('+3.2'));
  assert.ok(html.includes('&lt;62&gt;') && !html.includes('<62>'));
  const clear = reportHtml({ date: 'd', sum: summarize([], {}), pm: null, brainAge: null }, en);
  assert.ok(clear.includes('All 13') && !clear.includes('Worth working on') && !clear.includes('air quality'));
}

console.log('check-summary: all assertions passed');
