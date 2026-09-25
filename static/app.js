/* Front-end for the campaign planner. Plain JS, no build step.
   One POST to /api/plan, then render each section from the JSON. */

const $ = (sel) => document.querySelector(sel);
const state = { catalog: { publishers: [], personas: [] }, result: null };

/* tiny element helper: h('div', {class:'x', onclick: fn}, 'text', childEl, [more]) */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const chip = (text, cls = '') => h('span', { class: `chip ${cls}` }, text);
const chips = (items, cls = '') => h('div', { class: 'chips' }, (items || []).map((t) => chip(t.replace(/_/g, ' '), cls)));
const money = (x) => (x == null ? '—' : '$' + Math.round(x).toLocaleString());
const num = (x) => (x == null ? '—' : Math.round(x).toLocaleString());
const personaName = (id) => (state.catalog.personas.find((p) => p.id === id) || {}).name || id;
const publisherName = (id) => (state.catalog.publishers.find((p) => p.id === id) || {}).name || id;

async function init() {
  const [examples, catalog] = await Promise.all([
    fetch('/api/examples').then((r) => r.json()),
    fetch('/api/catalog').then((r) => r.json()),
  ]);
  state.catalog = catalog;
  const wrap = $('#examples');
  examples.forEach((e) => wrap.append(h('button', {
    class: 'chip clickable', title: e.text,
    onclick: () => { $('#description').value = e.text; $('#description').focus(); },
  }, `#${e.n} ${e.text.slice(0, 42)}${e.text.length > 42 ? '…' : ''}`)));

  $('#run').addEventListener('click', plan);
  $('#description').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) plan(); });
  const params = new URLSearchParams(location.search);          // ?example=3 or ?q=... auto-runs
  const preset = params.get('q') || (examples.find((e) => String(e.n) === params.get('example')) || {}).text;
  if (preset) { $('#description').value = preset; plan(); }

  $('#copy').addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    navigator.clipboard.writeText($('#config-json').textContent);
    $('#copy').textContent = 'Copied'; setTimeout(() => ($('#copy').textContent = 'Copy'), 1200);
  });
}

async function plan() {
  const description = $('#description').value.trim();
  if (!description) { $('#status').textContent = 'Type a description first.'; return; }
  const body = { description, budget_usd: parseFloat($('#budget').value) || null };
  $('#run').disabled = true;
  const t0 = Date.now();
  const timer = setInterval(() => {
    $('#status').textContent = `Planning… ${Math.round((Date.now() - t0) / 1000)}s (three LLM calls)`;
  }, 250);
  try {
    const res = await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail) || res.statusText);
    state.result = data;
    render(data);
    $('#status').textContent = `Done in ${((Date.now() - t0) / 1000).toFixed(1)}s`;
  } catch (err) {
    $('#status').textContent = `Error: ${err.message}`;
  } finally {
    clearInterval(timer);
    $('#run').disabled = false;
  }
}

/* ---------------------------------------------------------------- render */

function render(r) {
  $('#results').hidden = false;
  renderBanner(r);
  $('#profile-card').hidden = r.status === 'needs_input';   // nothing was understood; the banner says what we need
  renderProfile(r.profile);
  $('#publishers-card').hidden = r.status === 'needs_input';
  $('#personas-card').hidden = r.status !== 'ok';
  $('#config-card').hidden = r.status !== 'ok';
  if (r.status !== 'needs_input') renderPublishers(r);
  if (r.status === 'ok') { renderPersonas(r); renderConfig(r.config); }
  renderTrace(r.trace);
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderBanner(r) {
  const p = r.profile, box = $('#banner');
  box.replaceChildren();
  const list = (items) => h('ul', {}, (items || []).map((x) => h('li', {}, x)));
  if (r.status === 'needs_input') {
    box.append(h('div', { class: 'banner bad' },
      h('h3', {}, 'Not enough to work with'),
      h('p', {}, 'The description carries no usable signal, so we have not guessed a business for you. Tell us:'),
      list(p.clarifying_questions.length ? p.clarifying_questions : ['What do you sell, and roughly what does it cost?'])));
  } else if (r.status === 'no_fit') {
    box.append(h('div', { class: 'banner bad' },
      h('h3', {}, 'No publisher in this catalog fits'),
      h('p', {}, r.publishers[0].exclusion_reason),
      p.clarifying_questions.length ? list(p.clarifying_questions) : null));
  } else if (p.clarity === 'low' || p.clarity === 'medium') {
    box.append(h('div', { class: `banner ${p.clarity === 'low' ? 'warn' : 'info'}` },
      h('h3', {}, p.clarity === 'low' ? 'Low-signal description. We proceeded on assumptions:' : 'Some details were inferred:'),
      list(p.assumptions),
      p.clarifying_questions.length ? h('p', {}, h('b', {}, 'Answering these would sharpen the plan: '), p.clarifying_questions.join(' ')) : null));
  }
}

function renderProfile(p) {
  const clarityCls = { high: 'good', medium: 'accent', low: 'warn', none: 'bad' }[p.clarity];
  const dl = h('dl', { class: 'kv' },
    h('dt', {}, 'Clarity'), h('dd', {}, chip(p.clarity, clarityCls), ' ', chip(p.is_consumer_commerce ? 'consumer commerce' : 'not consumer commerce', p.is_consumer_commerce ? 'good' : 'bad')),
    h('dt', {}, 'Product'), h('dd', {}, p.product),
    h('dt', {}, 'Summary'), h('dd', {}, p.summary),
    h('dt', {}, 'Catalog fit'), h('dd', {}, chips(p.catalog_categories, 'accent'), p.primary_subcategory ? h('div', { class: 'chips' }, chip(`sells: ${p.primary_subcategory.replace(/_/g, ' ')}`, 'good'), ...p.catalog_subcategories.filter((s) => s !== p.primary_subcategory).map((s) => chip(s.replace(/_/g, ' ')))) : chips(p.catalog_subcategories)),
    h('dt', {}, 'Persona affinities'), h('dd', {}, chips(p.persona_affinities)),
    h('dt', {}, 'Economics'), h('dd', {}, `${p.price_tier} tier, ~${money(p.estimated_price_usd)} per order, ${p.business_model.replace(/_/g, ' ')}`),
    h('dt', {}, 'Audience'), h('dd', {}, `${p.target_gender}, ages ${p.target_age_min}-${p.target_age_max}`),
    h('dt', {}, 'Values & tone'), h('dd', {}, chips(p.values), h('div', { class: 'hint' }, p.tone)),
  );
  $('#profile').replaceChildren(dl);
}

function featureRow(f) {
  const neg = f.contribution < 0;
  const sign = f.contribution > 0 ? '+' : '';
  return h('div', { class: 'feat' },
    h('span', { class: 'feat-name' }, f.name.replace(/_/g, ' ')),
    h('div', { class: 'bar' }, h('div', { class: `fill${neg ? ' neg' : ''}`, style: `width:${Math.round(Math.min(1, Math.abs(f.value)) * 100)}%` })),
    h('span', { class: 'feat-val' }, `${f.value.toFixed(2)} × ${f.weight} = ${sign}${f.contribution.toFixed(2)}`),
    h('span', { class: 'feat-note', title: f.note }, f.note));
}

function scoreDetails(s, label = 'Score breakdown') {
  return h('details', {},
    h('summary', {}, label),
    h('div', { class: 'ranks' },
      `Lexical ranker: #${s.lexical_rank} (score ${s.lexical_score.toFixed(2)}) · Semantic ranker: #${s.semantic_rank} (cosine ${s.semantic_score.toFixed(2)}) · RRF fused: #${s.fused_rank}`),
    s.lexical_features.map(featureRow));
}

function renderPublishers(r) {
  const cfgRows = (r.config && r.config.publishers) || [];
  const alloc = Object.fromEntries(cfgRows.map((row) => [row.publisher_id, row.allocation_pct]));
  const rec = r.publishers.filter((s) => s.status === 'recommended');
  const con = r.publishers.filter((s) => s.status === 'considered');
  const exc = r.publishers.filter((s) => s.status === 'excluded');
  const cat = (id) => ((state.catalog.publishers.find((p) => p.id === id) || {}).category || '').replace(/_/g, ' ');

  $('#placement-note').textContent = r.placement_note || '';
  $('#recommended').replaceChildren(...rec.map((s, i) => h('div', { class: 'pub rec' },
    h('div', { class: 'pub-head' },
      chip(`#${i + 1}`, 'accent'), h('span', { class: 'name' }, s.name), h('span', { class: 'cat' }, cat(s.publisher_id)),
      h('div', { class: 'right' },
        alloc[s.publisher_id] != null ? h('span', { class: 'alloc' }, `${alloc[s.publisher_id]}% of budget`) : null,
        s.fit_score < ((r.thresholds || {}).recommend_floor || 65) ? chip('weak fit, kept for diversification', 'warn') : null,
        h('span', { class: 'fit' }, s.fit_score, h('small', {}, '/100 fit')))),
    h('p', { class: 'rationale' }, s.rationale),
    scoreDetails(s))));

  $('#considered-count').textContent = `(${con.length})`;
  $('#considered').replaceChildren(...con.map((s) => h('div', { class: 'compact' },
    h('span', { class: 'name' }, s.name, s.fit_score != null ? h('span', { class: 'hint' }, ` · ${s.fit_score}/100`) : null),
    h('span', {}, s.rationale, scoreDetails(s, 'details')))));
  $('#considered-wrap').hidden = con.length === 0;

  $('#excluded-count').textContent = `(${exc.length})`;
  $('#excluded').replaceChildren(...exc.map((s) => h('div', { class: 'compact' }, h('span', { class: 'name' }, s.name), h('span', {}, s.exclusion_reason))));
  $('#excluded-wrap').hidden = exc.length === 0;
  if (r.status === 'no_fit') $('#excluded-wrap').open = true;
}

function renderPersonas(r) {
  const creatives = Object.fromEntries((r.creatives || []).map((c) => [c.persona_id, c]));
  const selected = r.personas.filter((s) => s.selected);
  const rest = r.personas.filter((s) => !s.selected);
  const personaObj = (id) => state.catalog.personas.find((p) => p.id === id) || {};

  $('#warnings').replaceChildren(...(r.creative_warnings || []).map((w) =>
    h('div', { class: 'warnbox' }, `${personaName(w.persona_id)}: ${w.warning}`)));

  $('#personas').replaceChildren(...selected.map((s) => {
    const p = personaObj(s.persona_id), c = creatives[s.persona_id];
    const where = (r.placements && r.placements[s.persona_id]) || [];
    return h('div', { class: 'persona' },
      h('div', {},
        h('h3', {}, s.name, ' ', chip(`fused #${s.fused_rank}`, 'accent')),
        h('div', { class: 'hint' }, p.description),
        h('div', { class: 'why' }, h('b', {}, 'Why selected: '), s.why),
        h('div', { class: 'where' }, h('b', {}, 'Runs on: '), where.length ? where.map(publisherName).join(', ') : 'no recommended publisher'),
        scoreDetails(s, 'persona score breakdown')),
      c ? h('div', { class: 'ad' },
        h('p', { class: 'headline' }, c.headline),
        h('p', { class: 'body' }, c.body),
        h('span', { class: 'cta' }, c.cta),
        h('p', { class: 'angle' }, c.angle),
        h('div', { class: 'chips' }, ...c.leaned_into.map((t) => chip(`↑ ${t}`, 'good')), ...c.avoided.map((t) => chip(`avoided: ${t}`))))
        : h('div', { class: 'ad' }, h('p', { class: 'hint' }, 'Creative generation skipped (deterministic mode).')));
  }));

  $('#unselected').replaceChildren(...rest.map((s) => h('div', { class: 'compact' },
    h('span', { class: 'name' }, s.name, h('span', { class: 'hint' }, ` · fused #${s.fused_rank}`)),
    h('span', {}, s.why, scoreDetails(s, 'details')))));
}

function renderConfig(cfg) {
  const c = cfg.campaign, b = cfg.budget, t = cfg.targeting, bid = cfg.bid_strategy || {};
  const th = (...cols) => h('tr', {}, cols.map(([txt, cls]) => h('th', { class: cls }, txt)));
  const table = h('table', {},
    th(['Publisher', ''], ['Alloc', 'num'], ['Budget', 'num'], ['Est. CPM', 'num'], ['Est. impressions', 'num'], ['Est. orders', 'num'], ['Est. CPA', 'num'], ['Creatives', '']),
    cfg.publishers.map((row) => h('tr', {},
      h('td', {}, h('b', {}, row.name), row.capped_by_reach ? h('span', { class: 'hint' }, ' · capped by reach') : null),
      h('td', { class: 'num' }, `${row.allocation_pct}%`),
      h('td', { class: 'num' }, money(row.budget_usd)),
      h('td', { class: 'num' }, `$${row.est_cpm_usd}`),
      h('td', { class: 'num' }, num(row.est_impressions)),
      h('td', { class: 'num' }, row.est_orders),
      h('td', { class: 'num' }, money(row.est_cpa_usd)),
      h('td', {}, (row.creative_persona_ids || []).map(personaName).join(', ') || '—'))));

  const top = h('dl', { class: 'kv' },
    h('dt', {}, 'Campaign'), h('dd', {}, `${c.name} · objective: ${c.objective.replace(/_/g, ' ')} · ${c.status}`),
    h('dt', {}, 'Flight'), h('dd', {}, `${c.flight.start} → ${c.flight.end} (${c.flight.days} days)`),
    h('dt', {}, 'Budget'), h('dd', {}, `${money(b.total_usd)} total · ${money(b.daily_usd)}/day · ${b.pacing} pacing · ${b.source}`,
      b.unallocated_usd ? h('div', { class: 'hint' }, `${money(b.unallocated_usd)} unallocated: ${b.note}`) : null),
    h('dt', {}, 'Frequency cap'), h('dd', {}, `${cfg.frequency_cap.impressions} impressions per ${cfg.frequency_cap.per}`));

  const bidBox = h('div', { class: 'box' }, h('h4', {}, 'Bid strategy'),
    h('div', {}, h('b', {}, `${bid.pricing_model} · ${(bid.strategy || '').replace(/_/g, ' ')}`)),
    bid.target_cpa_usd ? h('div', {}, `Target CPA: ${money(bid.target_cpa_usd)}`) : null,
    bid.starting_bid ? h('div', {}, `Starting bid: $${bid.starting_bid.low} – $${bid.starting_bid.high} ${bid.starting_bid.unit}`) : null,
    h('p', { class: 'hint' }, bid.rationale));

  const targetBox = h('div', { class: 'box' }, h('h4', {}, 'Targeting'),
    h('div', {}, h('b', {}, 'Personas: '), t.personas.map((p) => p.name).join(', ')),
    h('div', {}, h('b', {}, 'Demographics: '), `${t.gender}, ages ${t.age_range}, income ${t.income_tiers.join('/')}`),
    h('div', {}, h('b', {}, 'Geos: '), t.geos.join(', ')),
    h('div', {}, h('b', {}, 'Contextual: '), t.contextual_categories.map((x) => x.replace(/_/g, ' ')).join(', ')),
    chips(t.interests),
    t.excluded_publishers.length ? h('div', { class: 'hint' }, `${t.excluded_publishers.length} publisher(s) excluded by hard rules`) : null);

  const assumptions = h('details', {}, h('summary', {}, `Assumptions behind these numbers (${cfg.assumptions.length})`),
    h('ul', {}, cfg.assumptions.map((a) => h('li', {}, a))));

  $('#config').replaceChildren(top, table, h('div', { class: 'grid2' }, bidBox, targetBox), assumptions);
  $('#config-json').textContent = JSON.stringify(cfg, null, 2);
}

function renderTrace(trace) {
  $('#trace').replaceChildren(...trace.map((t) => h('div', {},
    h('div', { class: 'trace-step' },
      h('span', { class: 'ms' }, `${t.ms} ms`),
      h('b', {}, t.step.replace(/_/g, ' ')),
      t.skipped ? chip('skipped') : null,
      t.prompt ? h('details', {}, h('summary', {}, 'prompt'), h('pre', {}, t.prompt)) : null))));
}

init();
