const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = { bootstrap: null, rules: [], planFile: null, rollbackRun: null, view:'overview', reviewSource:'changes' };

const el = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
const prettyTime = value => value ? new Date(value).toLocaleString() : 'In progress';

function operationName(operation) {
  return ({'watchlists-add':'Add watchlist rows', 'watchlists-update':'Update watchlist rows', 'watchlists-delete':'Delete watchlist rows', 'watchlists-import':'Import watchlist rows', 'add-title':'Add incident title', 'remove-title':'Remove incident title', 'set-enabled':'Change rule state', 'add-condition':'Add condition', 'remove-condition':'Remove condition', 'deploy-catalog':'Deploy catalog rules', deploy:'Deploy rule'})[operation] || 'Azure operation';
}
function displayValue(value) {
  if (value === null || value === undefined) return '<em>Not present</em>';
  if (Array.isArray(value)) return value.length ? `<ul>${value.map(item => `<li>${displayValue(item)}</li>`).join('')}</ul>` : '<em>Empty</em>';
  if (typeof value === 'object') return `<dl>${Object.entries(value).map(([key,item]) => `<dt>${escapeHtml(key)}</dt><dd>${displayValue(item)}</dd>`).join('')}</dl>`;
  return escapeHtml(value);
}
function errorMarkup(info, workspace = '', name = '') {
  const fields = [['Code',info.code],['HTTP status',info.http_status],['Workspace',workspace || info.workspace],['Resource',name || info.target],['Request ID',info.request_id]];
  return `<article class="error-card"><p class="error-message">${escapeHtml(info.message || 'The operation failed.')}</p><dl>${fields.filter(([,value]) => value).map(([label,value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join('')}${(info.details || []).map(detail => `<dt>${escapeHtml(detail.label)}</dt><dd>${escapeHtml(detail.value)}</dd>`).join('')}</dl></article>`;
}
function showErrors(errors) {
  el('error-details').innerHTML = errors.map(info => errorMarkup(info)).join('');
  if (!el('error-dialog').open) el('error-dialog').showModal();
}
el('error-close').addEventListener('click', () => el('error-dialog').close());

function chooseChangeKind(kind) {
  state.changeKind = kind;
  el('plan-form').hidden = kind !== 'rule';
  el('watchlist-planner').hidden = kind !== 'watchlist';
  el('choose-rule-change').setAttribute('aria-pressed', String(kind === 'rule'));
  el('choose-watchlist-change').setAttribute('aria-pressed', String(kind === 'watchlist'));
}
function renderSourceWorkspaces() {
  const select = el('wl-source-workspace'); const current = select.value;
  select.innerHTML = '<option value="">Select workspace</option>' + (state.bootstrap?.workspaces || []).filter(w => w.enabled).map(w => `<option value="${escapeHtml(w.key)}">${escapeHtml(w.display_name)}</option>`).join('');
  if ([...select.options].some(option => option.value === current)) select.value = current;
}
function syncSourceSelection(workspace, alias) {
  el('wl-source-workspace').value = workspace;
  const select = el('wl-source-alias');
  if (![...select.options].some(option => option.value === alias)) select.innerHTML = `<option value="${escapeHtml(alias)}">${escapeHtml(alias)}</option>`;
  select.value = alias; select.disabled = false;
}
function openWatchlistPlanner() {
  chooseChangeKind('watchlist'); showView('changes');
  el('page-title').scrollIntoView({block:'start'});
}
let sourceRequest = 0;
async function loadSourceWatchlists() {
  const sequence = ++sourceRequest;
  const workspace = el('wl-source-workspace').value;
  const select = el('wl-source-alias');
  wl.alias = null; wl.selected = null; el('wl-form').hidden = true;
  select.innerHTML = '<option value="">Select watchlist</option>'; select.disabled = true;
  if (!workspace) return;
  const button = el('wl-source-reload'); setBusy(button, true, 'Loading…');
  try {
    const result = await wlRequest({request:'list', targets:workspace});
    if (sequence !== sourceRequest) return;
    if (result.failures.length) { showErrors(result.failures.map(f => ({...f.error_info,message:f.error,workspace:f.workspace}))); return; }
    select.innerHTML += result.watchlists.map(w => `<option value="${escapeHtml(w.alias)}">${escapeHtml(w.display_name || w.alias)} (${escapeHtml(w.alias)})</option>`).join('');
    select.disabled = false;
    el('wl-source-status').textContent = result.watchlists.length ? '' : 'No watchlists found';
    el('wl-source-status').hidden = result.watchlists.length > 0;
  } catch (error) { if (sequence === sourceRequest) notify(error, true); }
  finally { if (sequence === sourceRequest) setBusy(button, false); }
}
el('wl-source-workspace').addEventListener('change', loadSourceWatchlists);
el('wl-source-reload').addEventListener('click', loadSourceWatchlists);
el('wl-source-alias').addEventListener('change', async () => {
  const workspace = el('wl-source-workspace').value; const alias = el('wl-source-alias').value;
  el('wl-form').hidden = true; wl.alias = null;
  if (!alias) return;
  await wlBusy(el('wl-source-reload'), () => wlLoadRows(workspace, alias, false, true));
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...(options.headers || {})}
  });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (${response.status})`);
    error.info = body.error_info || {code:'RequestFailed', message:error.message, http_status:response.status};
    throw error;
  }
  return body;
}

function notify(message, error = false) {
  if (error) { showErrors([message instanceof Error ? (message.info || {code:message.name, message:message.message}) : {code:'OperationFailed', message:String(message)}]); return; }
  const notice = el('notice');
  notice.textContent = message;
  notice.classList.toggle('error', error);
  notice.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => notice.hidden = true, 6500);
}

function setBusy(button, busy, label = 'Working…') {
  if (busy) { button.dataset.label = button.innerHTML; button.textContent = label; button.disabled = true; }
  else { button.innerHTML = button.dataset.label || button.innerHTML; button.disabled = false; }
}

function showView(name) {
  state.view = name;
  document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(node => node.classList.toggle('active', node.dataset.view === name));
  el('page-title').textContent = ({overview:'Overview', rules:'Automation rules', watchlists:'Watchlists', changes:'Plan a change', review:'Review changes', history:'Plans & runs'})[name];
}

function workspaceOptions(select) {
  const current = select.value;
  select.innerHTML = '<option value="all">All enabled workspaces</option>' + state.bootstrap.workspaces
    .filter(item => item.enabled)
    .map(item => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.display_name)}</option>`).join('');
  if ([...select.options].some(o => o.value === current)) select.value = current;
}

function renderBootstrap(data) {
  state.bootstrap = data;
  el('setup-auth').value = data.auth_mode || 'interactive';
  el('setup-tenant').value = data.managing_tenant_id || '';
  el('setup-title').textContent = data.needs_setup ? 'Set up Microsoft Sentinel' : 'Connect to Microsoft Azure';

  el('setup-submit').textContent = data.needs_setup ? 'Sign in and discover workspaces' : 'Sign in to Azure';
  el('onboarding').hidden = data.authenticated;
  el('app-shell').hidden = !data.authenticated;
  if (!data.authenticated) return;
  const enabled = data.workspaces.filter(item => item.enabled);

  el('metric-workspaces').textContent = enabled.length;
  el('metric-catalog').textContent = data.catalog.length;
  el('metric-runs').textContent = data.runs.filter(item => item.successful).length;
  el('metric-plans').textContent = data.plans.length;
  workspaceOptions(el('rule-target')); workspaceOptions(el('plan-target'));
  workspaceOptions(el('wl-target'));
  renderWlTargets();
  renderSourceWorkspaces();
  el('workspace-list').innerHTML = enabled.slice(0, 6).map(workspace => `<div class="workspace-row"><div class="row-main"><strong>${escapeHtml(workspace.display_name)}</strong><small>${escapeHtml(workspace.workspace_name)} · ${escapeHtml(workspace.resource_group)}</small></div>${workspace.tags.slice(0,1).map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join('')}</div>`).join('') || '<div class="empty">No enabled workspaces.</div>';
  el('recent-runs').innerHTML = data.runs.slice(0, 5).map(run => runMarkup(run, false)).join('') || '<div class="empty">No applied runs yet.</div>';
  el('plans-list').innerHTML = data.plans.map(plan => `<div class="history-row"><div class="row-main"><strong>${escapeHtml(operationName(plan.operation))}</strong><small>${prettyTime(plan.created_at)} · ${plan.targets} targets</small></div></div>`).join('') || '<div class="empty">No plans generated yet.</div>';
  el('runs-list').innerHTML = data.runs.map(run => runMarkup(run, true)).join('') || '<div class="empty">No runs applied yet.</div>';
  [...el('plans-list').children].forEach((row, index) => {
    if (!data.plans[index]) return;
    const button = document.createElement('button');
    button.className = 'secondary'; button.textContent = 'Review';
    button.addEventListener('click', async () => {
      try { renderPlan(await api('/api/plan', {method:'POST', body:JSON.stringify({plan_file:data.plans[index].file})}), 'history'); }
      catch (error) { notify(error, true); }
    });
    row.append(button);
  });
}

async function setup(event) {
  event.preventDefault();
  const button = el('setup-submit');
  setBusy(button, true, 'Connecting...');
  try {
    const tenantId = el('setup-tenant').value.trim();
    const result = await api('/api/setup', {
      method: 'POST',
      body: JSON.stringify({auth: el('setup-auth').value, tenant_id: tenantId || null})
    });
    renderBootstrap(result);
    updatePlanFields();
    const issueCount = result.discovery_issues?.length || 0;
    notify(issueCount
      ? `Connected. ${issueCount} workspace lookup(s) could not be completed.`
      : `Connected to ${result.workspaces.length} Sentinel workspace(s).`, issueCount > 0);
  } catch (error) {
    notify(error, true);
  } finally {
    setBusy(button, false);
  }
}

function runMarkup(run, controls) {
  const failures = controls && run.failures?.length ? `<details><summary>View failures</summary>${run.failures.map(failure => errorMarkup(failure.error_info || {message:failure.error}, failure.workspace, failure.name)).join('')}</details>` : '';
  return `<div class="${controls ? 'history-row' : 'timeline-row'}"><div class="row-main"><strong>${escapeHtml(operationName(run.operation))}</strong><small>${prettyTime(run.completed_at || run.started_at)} · ${run.results} results</small>${failures}</div><div class="actions"><span class="tag ${run.successful ? 'success':'fail'}">${run.successful ? 'SUCCESS':'FAILED'}</span>${controls ? `<button class="secondary mini-danger rollback" data-run="${escapeHtml(run.run_id)}">Rollback</button>` : ''}</div></div>`;
}

async function refresh() {
  try { renderBootstrap(await api('/api/bootstrap')); }
  catch (error) { notify(error, true); }
}

async function refreshCurrentPage() {
  const button = el('refresh');
  setBusy(button, true, 'Refreshing…');
  const view = state.view;
  try {
    renderBootstrap(await api('/api/bootstrap'));
    if (!state.bootstrap.authenticated) return;
    if (view === 'rules') await fetchRules();
    else if (view === 'watchlists') {
      if (!el('wl-content').hidden && wl.alias) await wlLoadRows(wl.workspace, wl.alias, true);
      else await wlLoadLists();
    } else if (view === 'review' && state.planFile) {
      const result = await api('/api/plan', {method:'POST', body:JSON.stringify({plan_file:state.planFile})});
      if (state.view === view) renderPlan(result, state.reviewSource);
    }
    if (view !== 'rules') notify(view === 'review' ? 'Saved preview reloaded. Live Azure data is checked when you apply.' : view === 'changes' ? 'Workspace options and history refreshed. Your draft is preserved.' : 'Page data refreshed.');
  } catch (error) {
    notify(error, true);
    if (view === 'watchlists') wlStatus(error.message, true);
  } finally { setBusy(button, false); }
}

function renderRules() {
  const query = el('rule-search').value.trim().toLowerCase();
  const rows = state.rules.filter(rule => [rule.display_name, rule.rule_id, rule.workspace].some(value => String(value).toLowerCase().includes(query)));
  el('rule-count').textContent = `${rows.length} of ${state.rules.length} rules`;
  el('rules-body').innerHTML = rows.map(rule => `<tr><td><strong>${escapeHtml(rule.display_name)}</strong><small>${escapeHtml(rule.rule_id)}</small></td><td>${escapeHtml(rule.workspace)}</td><td><span class="state ${rule.enabled ? 'on':'off'}">${rule.enabled ? 'ENABLED':'DISABLED'}</span></td><td>${escapeHtml(rule.triggers_on || '—')} / ${escapeHtml(rule.triggers_when || '—')}</td><td>${escapeHtml(rule.order ?? '—')}</td></tr>`).join('') || '<tr><td colspan="5" class="empty">No matching rules found.</td></tr>';
}

async function loadRules() {
  const button = el('load-rules'); setBusy(button, true, 'Loading…');
  try {
    await fetchRules();
  } catch (error) { notify(error, true); }
  finally { setBusy(button, false); }
}

async function fetchRules() {
  const result = await api('/api/rules', {method:'POST', body:JSON.stringify({targets:el('rule-target').value})});
  state.rules = result.rules; renderRules();
  if (result.failures.length) showErrors(result.failures.map(f => ({...f.error_info, message:f.error, workspace:f.workspace})));
  else notify(`${result.rules.length} live rules loaded from ${result.workspace_count} workspace(s).`);
}

function updatePlanFields() {
  const operation = el('operation').value;
  const dynamic = el('dynamic-fields');
  const catalog = operation === 'deploy-catalog';
  el('selector-fields').hidden = catalog; el('skip-row').hidden = catalog;
  el('selector-fields').querySelectorAll('input,select').forEach(input => input.disabled = catalog);
  const fields = {
    'add-title':'<label class="full">Incident title<input name="title" required placeholder="Known Benign Security Test"></label><label>Condition index (optional)<input name="condition_index" type="number" min="1" placeholder="1"></label>',
    'remove-title':'<label class="full">Incident title<input name="title" required placeholder="Known Benign Security Test"></label><label>Condition index (optional)<input name="condition_index" type="number" min="1" placeholder="1"></label>',
    'set-enabled':'<label>Desired state<select name="enabled"><option value="true">Enabled</option><option value="false">Disabled</option></select></label>',
    'add-condition':'<label>Property<input name="property" required placeholder="IncidentSeverity"></label><label>Operator<input name="operator" required placeholder="Equals"></label><label class="full">Values (one per line)<textarea name="values" rows="3" required></textarea></label>',
    'remove-condition':'<label>Property<input name="property" required placeholder="IncidentSeverity"></label><label>Operator (optional)<input name="operator" placeholder="Equals"></label><label>Condition index (optional)<input name="condition_index" type="number" min="1"></label>',
    'deploy-catalog':`<label class="full">Catalog rules<select name="catalog_rules"><option value="all">All catalog rules</option>${(state.bootstrap?.catalog || []).map(rule => `<option value="${escapeHtml(rule.logical_name)}">${escapeHtml(rule.display_name)}</option>`).join('')}</select></label><label>When rule exists<select name="if_exists"><option value="fail">Stop on difference</option><option value="skip">Skip existing</option><option value="update">Update existing</option></select></label>`
  };
  dynamic.innerHTML = fields[operation];
}

function formPayload(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  const payload = {operation:data.operation, targets:data.targets, skip_missing:!!form.elements.skip_missing?.checked};
  if (data.operation !== 'deploy-catalog') payload[el('selector-type').value] = data.selector_value;
  if (data.title) payload.title = data.title;
  if (data.condition_index) payload.condition_index = Number(data.condition_index);
  if (data.enabled) payload.enabled = data.enabled === 'true';
  if (data.property) payload.property = data.property;
  if (data.operator) payload.operator = data.operator;
  if (data.values) payload.values = data.values.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
  if (data.catalog_rules) payload.catalog_rules = data.catalog_rules;
  if (data.if_exists) payload.if_exists = data.if_exists;
  return payload;
}

function renderPlan(result, source = 'changes') {
  const plan = result.plan; state.planFile = result.plan_file;
  state.reviewSource = source;
  const isWatchlist = plan.resource_type === 'watchlist-items';
  const changes = plan.targets.filter(target => ['modify','create','delete'].includes(target.status)).length;
  el('preview-empty').hidden = true; el('plan-preview').hidden = false;
  el('preview-operation').textContent = isWatchlist ? 'Watchlist row changes' : 'Automation rule changes';
  el('preview-integrity').textContent = `${changes} changes · ${new Set(plan.targets.map(t => t.workspace.key)).size} workspaces`;

  el('review-back').textContent = source === 'history' ? 'Back to plans & runs' : 'Back to editor';
  el('preview-summary').innerHTML = plan.targets.map(target => `<div class="plan-row"><div><strong>${escapeHtml(target.display_name || target.rule_id || 'Missing rule')}</strong><small>${escapeHtml(target.workspace.key)} · ${escapeHtml(target.summary)}</small></div><span class="plan-status ${escapeHtml(target.status)}">${escapeHtml(target.status.replaceAll('_',' '))}</span></div>`).join('');
  el('apply-confirmation').value = '';
  el('apply-plan').disabled = changes === 0;
  {
    [...el('preview-summary').children].forEach((row, index) => {
      const details = document.createElement('details');
      const heading = document.createElement('summary'); heading.textContent = 'Before / after';
      const content = document.createElement('div'); content.className = 'table-wrap';
      const target = plan.targets[index];
      const before = target.before?.itemsKeyValue || {}; const after = target.after?.itemsKeyValue || {};
      const columns = [...new Set([...Object.keys(before), ...Object.keys(after)])];
      const changed = isWatchlist ? columns.filter(key => before[key] !== after[key]).map(key => ({path:key,before:before[key],after:after[key]})) : (target.changes || []);
      content.innerHTML = changed.length ? `<table class="change-values"><thead><tr><th>${isWatchlist ? 'Column' : 'Field'}</th><th>Before</th><th>After</th></tr></thead><tbody>${changed.map(change => `<tr><td>${escapeHtml(change.path)}</td><td>${displayValue(change.before)}</td><td>${displayValue(change.after)}</td></tr>`).join('')}</tbody></table>` : '<p>No changes</p>';
      details.open = true;
      details.append(heading, content); row.append(details);
    });
  }
  showView('review');
  el('page-title').scrollIntoView({block:'start'});
}

async function createPlan(event) {
  event.preventDefault(); const button = event.submitter; setBusy(button, true, 'Reading Azure…');
  try { const result = await api('/api/plans', {method:'POST', body:JSON.stringify(formPayload(event.currentTarget))}); renderPlan(result); notify('Plan created. Review every target before applying.'); await refresh(); }
  catch (error) { notify(error, true); }
  finally { setBusy(button, false); }
}

async function applyPlan() {
  const button = el('apply-plan'); setBusy(button, true, 'Applying…');
  try {
    const result = await api('/api/apply', {method:'POST', body:JSON.stringify({plan_file:state.planFile, confirmation:el('apply-confirmation').value})});
    const failed = result.report.results.filter(item => item.status === 'failed').length;
    if (failed) showErrors(result.report.results.filter(item => item.error).map(item => ({...item.error_info,message:item.error,workspace:item.workspace.key,target:item.display_name})));
    else notify('Changes applied and verified.');
    el('apply-confirmation').value = ''; await refresh(); showView('history');
  } catch (error) { notify(error, true); }
  finally { setBusy(button, false); }
}

async function rollback() {
  const button = el('confirm-rollback'); setBusy(button, true, 'Rolling back…');
  try {
    const result = await api('/api/rollback', {method:'POST', body:JSON.stringify({run_id:state.rollbackRun, confirmation:el('rollback-confirmation').value, force:el('rollback-force').checked})});
    const failed = result.report.results.filter(item => item.status === 'failed').length;
    if (failed) showErrors(result.report.results.filter(item => item.error).map(item => ({...item.error_info,message:item.error,workspace:item.workspace.key,target:item.display_name})));
    else notify('Rollback completed and verified.');
    el('rollback-dialog').close(); await refresh();
  } catch (error) { notify(error, true); }
  finally { setBusy(button, false); }
}

document.addEventListener('click', event => {
  const nav = event.target.closest('[data-view],[data-go]'); if (nav) showView(nav.dataset.view || nav.dataset.go);
  const rollbackButton = event.target.closest('.rollback');
  if (rollbackButton) { state.rollbackRun = rollbackButton.dataset.run; el('rollback-confirmation').value=''; el('rollback-force').checked=false; el('rollback-dialog').showModal(); }
});
el('review-back').addEventListener('click', () => showView(state.reviewSource));
el('choose-rule-change').addEventListener('click', () => chooseChangeKind('rule'));
el('choose-watchlist-change').addEventListener('click', () => chooseChangeKind('watchlist'));
el('refresh').addEventListener('click', refreshCurrentPage); el('load-rules').addEventListener('click', loadRules); el('rule-search').addEventListener('input', renderRules);
el('operation').addEventListener('change', updatePlanFields); el('selector-type').addEventListener('change', event => { const input = document.querySelector('[name="selector_value"]'); const byName = event.target.value === 'display_name'; el('selector-value-label').childNodes[0].textContent = byName ? 'Rule display name' : 'Rule ID'; input.placeholder = byName ? 'Close Known Benign Incidents' : '00000000-0000-0000-0000-000000000000'; });
el('plan-form').addEventListener('submit', createPlan); el('apply-plan').addEventListener('click', applyPlan); el('confirm-rollback').addEventListener('click', rollback);
el('setup-form').addEventListener('submit', setup);

refresh().then(updatePlanFields);

const wl = {lists:[], items:[], workspace:null, alias:null, searchKey:null, selected:null, targets:new Set()};
function wlAvailableTargets() {
  return (state.bootstrap?.workspaces || []).filter(workspace => workspace.enabled);
}
function renderWlTargets() {
  const focusedKey = document.activeElement?.dataset?.workspaceKey;
  const available = wlAvailableTargets();
  const keys = new Set(available.map(workspace => workspace.key));
  wl.targets = new Set([...wl.targets].filter(key => keys.has(key)));
  const query = el('wl-target-search').value.trim().toLowerCase();
  const shown = available.filter(workspace => [workspace.display_name, workspace.workspace_name, workspace.key].some(value => String(value || '').toLowerCase().includes(query)));
  el('wl-target-options').innerHTML = shown.map(workspace => `<label class="check workspace-choice"><input type="checkbox" data-workspace-key="${escapeHtml(workspace.key)}" ${wl.targets.has(workspace.key) ? 'checked' : ''}><span><strong>${escapeHtml(workspace.display_name || workspace.workspace_name)}</strong><small>${escapeHtml(workspace.key)}</small></span></label>`).join('') || '<p class="muted">No matching workspaces.</p>';
  const count = wl.targets.size;
  const all = count > 0 && count === available.length;
  el('wl-target-all').checked = all;
  el('wl-target-all').indeterminate = count > 0 && !all;
  el('wl-target-all').disabled = available.length === 0;
  el('wl-target-summary').textContent = all ? `All workspaces (${count})` : count === 1 ? (available.find(workspace => wl.targets.has(workspace.key)).display_name || [...wl.targets][0]) : count ? `${count} workspaces selected` : 'Choose workspaces';
  el('wl-target-count').textContent = `${count} of ${available.length} workspaces selected`;
  if (focusedKey) [...el('wl-target-options').querySelectorAll('input')].find(input => input.dataset.workspaceKey === focusedKey)?.focus({preventScroll:true});
}
function wlTargetExpression() {
  if (!wl.targets.size) throw new Error('Select at least one target workspace.');
  return [...wl.targets].join(',');
}
el('wl-target-search').addEventListener('input', renderWlTargets);
el('wl-target-options').addEventListener('change', event => {
  const key = event.target.dataset.workspaceKey;
  if (!key) return;
  if (event.target.checked) wl.targets.add(key); else wl.targets.delete(key);
  renderWlTargets();
});
el('wl-target-all').addEventListener('change', event => {
  wl.targets = new Set(event.target.checked ? wlAvailableTargets().map(workspace => workspace.key) : []);
  renderWlTargets();
});
el('wl-target-clear').addEventListener('click', () => { wl.targets.clear(); renderWlTargets(); });
const wlRequest = payload => api('/api/watchlists', {method:'POST', body:JSON.stringify(payload)});
function wlStatus(message, error = false) {
  const status = el('wl-status');
  status.textContent = message; status.hidden = !message;
  status.classList.toggle('wl-error', error);
}
async function wlBusy(button, action) {
  setBusy(button, true);
  try { await action(); } catch (error) {
    wlStatus(error.message, true); notify(error, true);
    if (state.view === 'watchlists') el('wl-status').scrollIntoView({block:'nearest'});
  }
  finally { setBusy(button, false); }
}

async function wlLoadLists() {
  wlStatus('Loading watchlists…');
  const result = await wlRequest({request:'list', targets:el('wl-target').value});
  wl.lists = result.watchlists;
  el('wl-lists').innerHTML = wl.lists.map((row, index) => `<tr><td><strong>${escapeHtml(row.display_name)}</strong><small>${escapeHtml(row.alias)}</small></td><td>${escapeHtml(row.workspace)}</td><td>${escapeHtml(row.search_key)}</td><td><button class="secondary" data-wl-open="${index}">Open</button></td></tr>`).join('') || '<tr><td colspan="4" class="empty">No watchlists found.</td></tr>';
  el('wl-browser').hidden = false; el('wl-content').hidden = true;
  wlStatus(result.failures.length ? result.failures.map(f => `${f.workspace}: ${f.error}`).join('; ') : `${wl.lists.length} watchlists loaded. Choose Open to view rows.`, result.failures.length > 0);
}
el('wl-load').addEventListener('click', event => wlBusy(event.currentTarget, wlLoadLists));

async function wlLoadRows(workspace = wl.workspace, alias = wl.alias, preserveEditor = false, fromPlanner = false) {
  wlStatus(`Loading rows from ${alias}…`);
  const result = await wlRequest({request:'items', targets:workspace, alias});
  if (fromPlanner && (el('wl-source-workspace').value !== workspace || el('wl-source-alias').value !== alias)) return;
  wl.workspace = workspace; wl.alias = alias; wl.items = result.items; wl.searchKey = result.search_key;
  el('wl-content').hidden = false;
  el('wl-title').textContent = `${alias} · ${workspace}`;
  if (!preserveEditor) {
    wl.targets = new Set([workspace]);
    el('wl-target-search').value = '';
    el('wl-target-picker').open = false;
    renderWlTargets();
    el('wl-key').value = result.search_key;
    el('wl-search').value = '';
    wlEdit('add');
  }
  wlRenderRows();
  el('wl-form').hidden = false;
  syncSourceSelection(workspace, alias);
  el('wl-browser').hidden = true;
  wlStatus(`${wl.items.length} rows loaded from ${alias}.`);
  if (!preserveEditor && state.view === 'watchlists') el('wl-content').scrollIntoView({block:'start'});
}
el('wl-back').addEventListener('click', () => {
  el('wl-content').hidden = true; el('wl-browser').hidden = false;
  wlStatus('Choose Open to view a watchlist.');
  el('wl-browser').scrollIntoView({block:'start'});
});
el('wl-lists').addEventListener('click', event => {
  const button = event.target.closest('[data-wl-open]');
  if (button) wlBusy(button, () => { const row = wl.lists[Number(button.dataset.wlOpen)]; return wlLoadRows(row.workspace, row.alias); });
});
el('wl-reload').addEventListener('click', event => wlBusy(event.currentTarget, () => wlLoadRows(wl.workspace, wl.alias, true)));

function wlColumns() { return [...new Set([wl.searchKey, ...wl.items.flatMap(row => Object.keys(row.values))].filter(Boolean))]; }
function wlRenderRows() {
  const columns = wlColumns(); const query = el('wl-search').value.toLowerCase();
  el('wl-head').innerHTML = `<tr>${columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}<th>Actions</th></tr>`;
  const rows = wl.items.map((row, index) => ({row,index})).filter(({row}) => Object.values(row.values).some(v => v.toLowerCase().includes(query)));
  el('wl-count').textContent = `${rows.length} of ${wl.items.length} rows`;
  el('wl-rows').innerHTML = rows.map(({row,index}) => `<tr>${columns.map(c => `<td>${escapeHtml(row.values[c] ?? '')}</td>`).join('')}<td><button class="secondary" data-wl-edit="${index}">Edit</button> <button class="secondary mini-danger" data-wl-delete="${index}">Delete</button></td></tr>`).join('') || `<tr><td colspan="${columns.length + 1}" class="empty">No matching rows.</td></tr>`;
}
el('wl-search').addEventListener('input', wlRenderRows);

function wlAddField(key = '', value = '') {
  const pair = document.createElement('div'); pair.className = 'wl-field';
  const nameLabel = document.createElement('label'); nameLabel.textContent = 'Column';
  const name = document.createElement('input'); name.value = key; name.required = true; name.className = 'wl-column-name';
  const valueLabel = document.createElement('label'); valueLabel.textContent = 'Value';
  const input = document.createElement('input'); input.value = value; input.className = 'wl-cell-value';
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = 'Remove';
  remove.addEventListener('click', () => pair.remove());
  nameLabel.append(name); valueLabel.append(input); pair.append(nameLabel, valueLabel, remove); el('wl-values').append(pair);
}
function wlEdit(action, row = null) {
  wl.selected = row ? {id:row.item_id, column:el('wl-key').value, value:row.values[el('wl-key').value] ?? ''} : null;
  el('wl-action').value = action; el('wl-values').replaceChildren();
  el('wl-match').value = row ? row.values[el('wl-key').value] ?? '' : '';
  const values = row ? row.values : Object.fromEntries(wlColumns().map(c => [c,'']));
  Object.entries(values).forEach(([key,value]) => wlAddField(key,value));
  wlFields();
}
function wlFields() {
  const action = el('wl-action').value; const edit = ['add','update'].includes(action);
  el('wl-values').hidden = !edit; el('wl-column').hidden = !edit;
  el('wl-values').querySelectorAll('input').forEach(input => input.disabled = !edit);
  el('wl-import').hidden = action !== 'import'; el('wl-file').required = action === 'import';
  el('wl-match-label').hidden = !['update','delete'].includes(action);
  el('wl-match').required = ['update','delete'].includes(action);
}
el('wl-rows').addEventListener('click', event => {
  const button = event.target.closest('[data-wl-edit],[data-wl-delete]'); if (!button) return;
  const deleting = button.dataset.wlDelete !== undefined;
  wlEdit(deleting ? 'delete' : 'update', wl.items[Number(deleting ? button.dataset.wlDelete : button.dataset.wlEdit)]);
  openWatchlistPlanner();
});
el('wl-new').addEventListener('click', () => { wlEdit('add'); openWatchlistPlanner(); });
el('wl-column').addEventListener('click', () => wlAddField());
el('wl-action').addEventListener('change', wlFields);
el('wl-form').addEventListener('submit', event => {
  event.preventDefault();
  wlBusy(event.submitter, async () => {
    const action = el('wl-action').value;
    if (!wl.alias || el('wl-form').hidden) throw new Error('Select a watchlist first.');
    const payload = {request:'plan', action, targets:wlTargetExpression(), alias:wl.alias,
      key_column:el('wl-key').value, key_value:el('wl-match').value};
    if (['update','delete'].includes(action) && wl.selected && payload.targets.trim() === wl.workspace &&
        payload.key_column === wl.selected.column && payload.key_value === wl.selected.value) {
      payload.item_id = wl.selected.id;
    }
    if (['add','update'].includes(action)) {
      const pairs = [...el('wl-values').children].map(pair => [pair.querySelector('.wl-column-name').value, pair.querySelector('.wl-cell-value').value]);
      if (new Set(pairs.map(([key]) => key)).size !== pairs.length) throw new Error('Column names must be unique');
      payload.values = Object.fromEntries(pairs);
    }
    if (action === 'import') {
      const file = el('wl-file').files[0]; if (!file) throw new Error('Choose a CSV file');
      if (file.size > 750000) throw new Error('Use the CLI to import CSV files larger than 750 KB');
      payload.csv = await file.text(); payload.mode = el('wl-mode').value;
    }
    const result = await wlRequest(payload); renderPlan(result, 'changes'); await refresh();
    notify('Review the before and after values, then apply the saved plan.');
  });
});
el('wl-export').addEventListener('click', event => wlBusy(event.currentTarget, async () => {
  const result = await wlRequest({request:'export', targets:wl.workspace, alias:wl.alias});
  const url = URL.createObjectURL(new Blob(['\ufeff', result.csv], {type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = `${wl.alias}.csv`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}));
