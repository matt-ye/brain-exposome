/* Result summary for the personal check-in — pure (no DOM, no storage), so it can be
   tested with node and shared by the result view and the printable report.

   Why this exists (2026-11 design feedback): a parent sent this check by an adult child
   should come away knowing what is already fine and what to do next, not feeling like a
   patient. So the result leads with personal habits/health, keeps air pollution apart as
   an environmental factor (with the WHO 5 µg/m³ reference almost everyone in Taiwan is
   above it, so counting it would make "all fine" unreachable), and lists what is unsure
   as something to check at the next health exam. The brain-age number is unchanged. */

/* The 13 personal items the wizard asks about. Each maps the flagged factor key(s)
   computeResult() can emit, and the form field whose "unknown" answer means "not sure". */
export const PERSONAL_ITEMS = [
  { key: 'education', field: 'in-education', label: ['教育程度', 'Education'] },
  { key: 'hearing', field: 'in-hearing', label: ['聽力', 'Hearing'] },
  { key: 'vision', field: 'in-vision', label: ['視力', 'Vision'] },
  { key: 'htn', field: 'in-htn', label: ['血壓', 'Blood pressure'] },
  { key: 'diabetes', field: 'in-diabetes', label: ['血糖', 'Blood sugar'] },
  { key: 'obesity', field: 'in-bmi', label: ['體位（BMI）', 'Body weight (BMI)'] },
  { key: 'ldl', field: 'in-cholesterol', label: ['膽固醇（LDL）', 'Cholesterol (LDL)'] },
  { key: 'inactive', field: 'in-inactive', label: ['身體活動', 'Physical activity'] },
  { key: 'depression', field: 'in-depression', label: ['情緒與心理健康', 'Mood & mental health'] },
  { key: 'isolation', field: 'in-isolation', label: ['社交往來', 'Social contact'] },
  { key: 'smoking', alt: ['smoking-former'], field: 'in-smoking', label: ['吸菸', 'Smoking'] },
  { key: 'alcohol', field: 'in-alcohol', label: ['飲酒', 'Alcohol'] },
  { key: 'tbi', field: 'in-tbi', label: ['頭部外傷', 'Head injury'] },
];
export const ENV_KEYS = ['pm25'];
/* Still in the brain-age calculation (risk keeps falling for years after quitting), but the
   person has already done the thing: listing it under "worth working on" reads as blame. */
export const DONE_KEYS = ['smoking-former'];

/* factors: computeResult().factors ([{key}]); answers: {fieldId: value}.
   A flagged item is "to improve"; an unflagged item answered "unknown" is "unsure";
   everything else is "fine". */
export function summarize(factors, answers = {}) {
  const flagged = new Set(factors.map(f => f.key));
  const improve = [], unsure = [], fine = [];
  for (const it of PERSONAL_ITEMS) {
    const hit = [it.key, ...(it.alt || [])].find(k => flagged.has(k));
    if (hit && !DONE_KEYS.includes(hit)) improve.push(hit);
    else if (hit) fine.push(hit);
    else if (answers[it.field] === 'unknown') unsure.push(it.key);
    else fine.push(it.key);
  }
  const env = ENV_KEYS.filter(k => flagged.has(k));
  const n = improve.length;
  const band = n === 0 ? 'clear' : n <= 2 ? 'few' : n <= 5 ? 'several' : 'many';
  return { improve, unsure, fine, env, band };
}

export function itemLabel(key, L) {
  if (key === 'smoking-former') return L('吸菸（已戒）', 'Smoking (quit)');
  const it = PERSONAL_ITEMS.find(i => i.key === key || (i.alt || []).includes(key));
  return it ? L(it.label[0], it.label[1]) : key;
}

/* ── History: what a saved check keeps. Only the outcome, never the raw answers. ── */
/* The user's local calendar date (toISOString would give the UTC date: before 08:00 in
   Taiwan that is still yesterday). */
export function localDate(d = new Date()) {
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function snapshot(sum, r, t = localDate()) {
  return {
    t,
    improve: [...sum.improve], unsure: [...sum.unsure],
    pm: r.meanPM == null ? null : Math.round(r.meanPM * 10) / 10,
    d: Math.round(r.dMid * 10) / 10,
  };
}

/* What changed from an earlier saved check to now. Keys are compared by item, so a
   current smoker who quit (smoking → smoking-former) counts as a change, not a repeat. */
export function compareSnapshots(prev, cur) {
  const p = new Set(prev.improve), c = new Set(cur.improve);
  return {
    since: prev.t,
    improved: prev.improve.filter(k => !c.has(k)),
    added: cur.improve.filter(k => !p.has(k)),
    resolvedUnsure: prev.unsure.filter(k => !cur.unsure.includes(k)),
    before: prev.improve.length,
    now: cur.improve.length,
  };
}

/* Newest last; a re-save on the same day replaces that day's entry; keep the last `max`. */
export function addToHistory(history, snap, max = 24) {
  const h = (Array.isArray(history) ? history : []).filter(s => s && s.t !== snap.t);
  h.push(snap);
  return h.slice(-max);
}

export function parseHistory(raw) {
  try {
    const h = JSON.parse(raw || '[]');
    return Array.isArray(h) ? h.filter(s => s && typeof s.t === 'string' && Array.isArray(s.improve)
      && Array.isArray(s.unsure)) : [];
  } catch { return []; }
}

/* An invitation to the tool, not anyone's result: nothing personal goes into it. */
export function shareMessage(url, L) {
  return L(
    `我發現一個大約 5 分鐘的腦健康小檢查，可以看看哪些習慣已經做得很好、哪些可以再加強。所有計算都在自己的手機或電腦上完成，資料不會上傳。有空可以試試看：${url}`,
    `Here's a ~5-minute brain-health check-in: it shows which habits are already in good shape and which are worth working on. Everything runs on your own device — nothing is uploaded. Have a look when you have a moment: ${url}`
  );
}

const esc = s => String(s).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));

/* Printable report. data: { date, sum, rows:[[label, answer]], advice:{key:text}, pm, pmRef,
   brainAge, url }. Plain markup; the page's print stylesheet lays it out. */
export function reportHtml(data, L) {
  const { date, sum, rows = [], advice = {}, pm, pmRef, brainAge, url } = data;
  const li = keys => keys.map(k => `<li>${esc(itemLabel(k, L))}${advice[k] ? `：${esc(advice[k])}` : ''}</li>`).join('');
  const sec = (title, body) => `<section class="rpt-sec"><h2>${title}</h2>${body}</section>`;
  const lead = sum.improve.length === 0
    ? L('目前這 13 項生活與健康因子都在建議範圍內。', 'All 13 lifestyle and health factors are currently within the recommended range.')
    : L(`${sum.fine.length} 項在建議範圍內，${sum.improve.length} 項可以開始調整。`,
        `${sum.fine.length} in the recommended range, ${sum.improve.length} worth working on.`);
  return [
    `<header class="rpt-head"><h1>${L('腦健康檢查報告', 'Brain-health check-in report')}</h1>`,
    `<p>${L('填寫日期', 'Date')}：${esc(date)}</p><p class="rpt-lead">${lead}</p></header>`,
    sum.improve.length ? sec(L('可以開始調整的項目', 'Worth working on'), `<ul>${li(sum.improve)}</ul>`) : '',
    sum.unsure.length ? sec(L('下次健檢可以順便確認', 'To check at your next health exam'), `<ul>${li(sum.unsure)}</ul>`) : '',
    sum.fine.length ? sec(L('已在建議範圍內', 'Already in the recommended range'),
      `<p>${sum.fine.map(k => esc(itemLabel(k, L))).join('、')}</p>`) : '',
    pm == null ? '' : sec(L('居住環境（空氣品質）', 'Where you live (air quality)'),
      `<p>${L(`居住史時間加權 PM2.5 年均約 ${pm.toFixed(1)} µg/m³（WHO 建議 ≤ ${pmRef}）。這是大環境因素，個人可以用空氣清淨機、空品不佳時減少戶外劇烈活動來降低暴露。`,
        `Time-weighted annual PM2.5 over your residence history ≈ ${pm.toFixed(1)} µg/m³ (WHO guideline ≤ ${pmRef}). This is an environmental factor; an air purifier and less outdoor exertion on poor-air days reduce personal exposure.`)}</p>`),
    brainAge == null ? '' : sec(L('教育性腦齡估計', 'Educational brain-age estimate'),
      `<p>≈ +${brainAge.toFixed(1)} ${L('年。這是族群研究換算出的比喻數字，不是影像量測，也不是個人預測。', 'years — an illustration converted from population studies, not an imaging measurement or a personal prediction.')}</p>`),
    rows.length ? sec(L('填答摘要', 'Your answers'),
      `<table class="rpt-table"><tbody>${rows.map(([a, b]) => `<tr><th>${esc(a)}</th><td>${esc(b)}</td></tr>`).join('')}</tbody></table>`) : '',
    `<footer class="rpt-foot"><p>${L('本報告僅供健康教育與自我覺察，不是診斷或篩檢，無法預測個人是否會失智。如有健康疑慮，請帶著這份報告與醫療人員討論。',
      'For health education and self-awareness only — not a diagnosis or screening test, and it cannot predict whether anyone will develop dementia. Bring this report to a healthcare professional if you have concerns.')}</p>`,
    url ? `<p>${esc(url)}</p>` : '', `</footer>`,
  ].join('');
}
