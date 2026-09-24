const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = { bootstrap: null, rules: [], planFile: null, rollbackRun: null };

const el = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
const prettyTime = value => value ? new Date(value).toLocaleString() : 'In progress';

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...(options.headers || {})}
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function notify(message, error = false) {
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
  document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(node => node.classList.toggle('active', node.dataset.view === name));
  el('page-title').textContent = ({overview:'Overview', rules:'Automation rules', changes:'Plan a change', history:'Plans & runs'})[name];
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
  const enabled = data.workspaces.filter(item => item.enabled);
  el('api-version').textContent = `ARM API ${data.api_version}`;
  el('metric-workspaces').textContent = enabled.length;
  el('metric-catalog').textContent = data.catalog.length;
  el('metric-runs').textContent = data.runs.filter(item => item.successful).length;
  el('metric-plans').textContent = data.plans.length;
  workspaceOptions(el('rule-target')); workspaceOptions(el('plan-target'));
  el('workspace-list').innerHTML = enabled.slice(0, 6).map(workspace => `<div class="workspace-row"><div class="row-main"><strong>${escapeHtml(workspace.display_name)}</strong><small>${escapeHtml(workspace.workspace_name)} · ${escapeHtml(workspace.resource_group)}</small></div>${workspace.tags.slice(0,1).map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join('')}</div>`).join('') || '<div class="empty">No enabled workspaces.</div>';
  el('recent-runs').innerHTML = data.runs.slice(0, 5).map(run => runMarkup(run, false)).join('') || '<div class="empty">No applied runs yet.</div>';
  el('plans-list').innerHTML = data.plans.map(plan => `<div class="history-row"><div class="row-main"><strong>${escapeHtml(plan.operation)}</strong><small>${prettyTime(plan.created_at)} · ${plan.targets} targets</small></div><span class="tag">${escapeHtml(plan.file)}</span></div>`).join('') || '<div class="empty">No plans generated yet.</div>';
  el('runs-list').innerHTML = data.runs.map(run => runMarkup(run, true)).join('') || '<div class="empty">No runs applied yet.</div>';
}

function runMarkup(run, controls) {
  return `<div class="${controls ? 'history-row' : 'timeline-row'}"><div class="row-main"><strong>${escapeHtml(run.operation || 'Azure operation')}</strong><small>${prettyTime(run.completed_at || run.started_at)} · ${run.results} results</small></div><div class="actions"><span class="tag ${run.successful ? 'success':'fail'}">${run.successful ? 'SUCCESS':'FAILED'}</span>${controls ? `<button class="secondary mini-danger rollback" data-run="${escapeHtml(run.run_id)}">Rollback</button>` : ''}</div></div>`;
}

async function refresh() {
  try { renderBootstrap(await api('/api/bootstrap')); }
  catch (error) { notify(error.message, true); }
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
    const result = await api('/api/rules', {method:'POST', body:JSON.stringify({targets:el('rule-target').value})});
    state.rules = result.rules; renderRules();
    if (result.failures.length) notify(`${result.rules.length} rules loaded; ${result.failures.length} workspace(s) could not be read.`, true);
    else notify(`${result.rules.length} live rules loaded from ${result.workspace_count} workspace(s).`);
  } catch (error) { notify(error.message, true); }
  finally { setBusy(button, false); }
}

function updatePlanFields() {
  const operation = el('operation').value;
  const dynamic = el('dynamic-fields');
  const catalog = operation === 'deploy-catalog';
  el('selector-fields').hidden = catalog; el('skip-row').hidden = catalog;
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

function renderPlan(result) {
  const plan = result.plan; state.planFile = result.plan_file;
  el('preview-empty').hidden = true; el('plan-preview').hidden = false;
  el('preview-operation').textContent = plan.operation.replaceAll('-', ' ');
  el('preview-integrity').textContent = `SHA ${plan.integrity.slice(0,12)}…`;
  el('preview-summary').innerHTML = plan.targets.map(target => `<div class="plan-row"><div><strong>${escapeHtml(target.display_name || target.rule_id || 'Missing rule')}</strong><small>${escapeHtml(target.workspace.key)} · ${escapeHtml(target.summary)}</small></div><span class="plan-status ${escapeHtml(target.status)}">${escapeHtml(target.status.replaceAll('_',' '))}</span></div>`).join('');
  el('apply-confirmation').value = '';
}

async function createPlan(event) {
  event.preventDefault(); const button = event.submitter; setBusy(button, true, 'Reading Azure…');
  try { const result = await api('/api/plans', {method:'POST', body:JSON.stringify(formPayload(event.currentTarget))}); renderPlan(result); notify('Plan created. Review every target before applying.'); await refresh(); }
  catch (error) { notify(error.message, true); }
  finally { setBusy(button, false); }
}

async function applyPlan() {
  const button = el('apply-plan'); setBusy(button, true, 'Applying…');
  try {
    const result = await api('/api/apply', {method:'POST', body:JSON.stringify({plan_file:state.planFile, confirmation:el('apply-confirmation').value})});
    const failed = result.report.results.filter(item => item.status === 'failed').length;
    notify(failed ? `Run ${result.run_id} completed with ${failed} failure(s).` : `Run ${result.run_id} completed and verified.`, !!failed);
    el('apply-confirmation').value = ''; await refresh(); showView('history');
  } catch (error) { notify(error.message, true); }
  finally { setBusy(button, false); }
}

async function rollback() {
  const button = el('confirm-rollback'); setBusy(button, true, 'Rolling back…');
  try {
    const result = await api('/api/rollback', {method:'POST', body:JSON.stringify({run_id:state.rollbackRun, confirmation:el('rollback-confirmation').value, force:el('rollback-force').checked})});
    const failed = result.report.results.filter(item => item.status === 'failed').length;
    notify(failed ? `Rollback completed with ${failed} failure(s).` : 'Rollback completed and verified.', !!failed);
    el('rollback-dialog').close(); await refresh();
  } catch (error) { notify(error.message, true); }
  finally { setBusy(button, false); }
}

document.addEventListener('click', event => {
  const nav = event.target.closest('[data-view],[data-go]'); if (nav) showView(nav.dataset.view || nav.dataset.go);
  const rollbackButton = event.target.closest('.rollback');
  if (rollbackButton) { state.rollbackRun = rollbackButton.dataset.run; el('rollback-confirmation').value=''; el('rollback-force').checked=false; el('rollback-dialog').showModal(); }
});
document.querySelectorAll('.nav-item').forEach(node => node.addEventListener('click', () => showView(node.dataset.view)));
el('refresh').addEventListener('click', refresh); el('load-rules').addEventListener('click', loadRules); el('rule-search').addEventListener('input', renderRules);
el('operation').addEventListener('change', updatePlanFields); el('selector-type').addEventListener('change', event => { const input = document.querySelector('[name="selector_value"]'); const byName = event.target.value === 'display_name'; el('selector-value-label').childNodes[0].textContent = byName ? 'Rule display name' : 'Rule ID'; input.placeholder = byName ? 'Close Known Benign Incidents' : '00000000-0000-0000-0000-000000000000'; });
el('plan-form').addEventListener('submit', createPlan); el('apply-plan').addEventListener('click', applyPlan); el('confirm-rollback').addEventListener('click', rollback);

refresh().then(updatePlanFields);
