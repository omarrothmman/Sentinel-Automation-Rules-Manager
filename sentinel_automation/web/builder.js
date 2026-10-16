// Local rule creation is separate from the reviewed Azure deployment step.
const builder = {saved:null, targets:new Set()};
const builderOptions = values => values.map(v => `<option value="${v}">${v.replace(/([a-z])([A-Z])/g,'$1 $2')}</option>`).join('');
function builderCondition() {
  const row = document.createElement('fieldset'); row.className = 'builder-row';
  row.innerHTML = `<legend>Condition</legend><div class="form-grid"><label>Property<select data-field="property">${builderOptions(['IncidentTitle','IncidentDescription','IncidentSeverity','IncidentStatus','IncidentRelatedAnalyticRuleIds','IncidentProviderName','IncidentTactics','AlertAnalyticRuleIds'])}</select></label><label>Operator<select data-field="operator">${builderOptions(['Equals','NotEquals','Contains','NotContains','StartsWith','NotStartsWith','EndsWith','NotEndsWith'])}</select></label><label>Values, one per line<textarea data-field="values" rows="2" required></textarea></label></div><button class="text-button" type="button" data-builder-remove>Remove condition</button>`;
  el('builder-conditions').append(row);
  row.querySelector('select').value = el('builder-trigger').value.startsWith('Alerts') ? 'AlertAnalyticRuleIds' : 'IncidentTitle';
  builderSyncTrigger();
}
function builderActionFields(row) {
  const kind = row.querySelector('[data-field="kind"]').value;
  const fields = {
    RunPlaybook:'<label>Logic App resource ID<input data-field="resource" required placeholder="/subscriptions/.../resourceGroups/.../providers/Microsoft.Logic/workflows/..."></label><label>Playbook tenant ID<input data-field="tenant" required placeholder="Tenant UUID"></label>',
    Severity:`<label>Severity<select data-field="value">${builderOptions(['High','Medium','Low','Informational'])}</select></label>`,
    Status:`<label>Status<select data-field="value">${builderOptions(['New','Active','Closed'])}</select></label><label>Classification when closing<select data-field="classification">${builderOptions(['Undetermined','TruePositive','BenignPositive','FalsePositive'])}</select></label>`,
    Tags:'<label>Tags, one per line<textarea data-field="value" rows="2" required></textarea></label>',
    AddIncidentTask:'<label>Task title<input data-field="title" required></label><label>Description<textarea data-field="description" rows="2"></textarea></label>'
  };
  row.querySelector('.builder-action-fields').innerHTML = fields[kind];
}
function builderAction() {
  const row = document.createElement('fieldset'); row.className = 'builder-row';
  row.innerHTML = `<legend>Action</legend><label>Action type<select data-field="kind"><option value="RunPlaybook">Run playbook</option><option value="Severity">Change severity</option><option value="Status">Change status</option><option value="Tags">Add tags</option><option value="AddIncidentTask">Add incident task</option></select></label><div class="builder-action-fields form-grid"></div><div class="actions"><button type="button" class="text-button" data-builder-up>Move up</button><button type="button" class="text-button" data-builder-down>Move down</button><button type="button" class="text-button" data-builder-remove>Remove action</button></div>`;
  el('builder-actions').append(row); builderActionFields(row); builderNumberActions();
  row.querySelector('select').addEventListener('change', () => builderActionFields(row));
  builderSyncTrigger();
}
function builderSyncTrigger() {
  const alert = el('builder-trigger').value.startsWith('Alerts');
  el('builder-conditions').querySelectorAll('[data-field="property"]').forEach(select => {
    [...select.options].forEach(option => option.disabled = alert ? option.value !== 'AlertAnalyticRuleIds' : option.value === 'AlertAnalyticRuleIds');
    select.setCustomValidity(select.selectedOptions[0]?.disabled ? 'Choose a condition compatible with the selected trigger.' : '');
    select.onchange = builderSyncTrigger;
  });
  el('builder-actions').querySelectorAll('[data-field="kind"]').forEach(select => {
    [...select.options].forEach(option => option.disabled = alert && option.value !== 'RunPlaybook');
    select.setCustomValidity(select.selectedOptions[0]?.disabled ? 'Alert triggers support Run playbook actions only.' : '');
    select.onchange = builderSyncTrigger;
  });
}
el('builder-trigger').addEventListener('change', builderSyncTrigger);
function builderNumberActions() {
  [...el('builder-actions').children].forEach((row,index,rows) => {
    row.querySelector('legend').textContent = `Action ${index + 1}`;
    row.querySelector('[data-builder-up]').disabled = index === 0;
    row.querySelector('[data-builder-down]').disabled = index === rows.length - 1;
  });
}
el('builder-add-condition').addEventListener('click', builderCondition);
el('builder-add-action').addEventListener('click', builderAction);
el('builder-definition').addEventListener('click', event => {
  const button = event.target.closest('[data-builder-remove],[data-builder-up],[data-builder-down]');
  if (!button) return;
  const row = button.closest('.builder-row'); const parent = row.parentElement;
  if (button.hasAttribute('data-builder-remove')) row.remove();
  else if (button.hasAttribute('data-builder-up') && row.previousElementSibling) parent.insertBefore(row,row.previousElementSibling);
  else if (row.nextElementSibling) parent.insertBefore(row.nextElementSibling,row);
  builderNumberActions();
});
function builderProperties() {
  const read = (row,key) => row.querySelector(`[data-field="${key}"]`)?.value.trim() || '';
  const lines = value => value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
  const [triggersOn,triggersWhen] = el('builder-trigger').value.split(':');
  const conditions = [...el('builder-conditions').children].map(row=>({conditionType:'Property',conditionProperties:{propertyName:read(row,'property'),operator:read(row,'operator'),propertyValues:lines(read(row,'values'))}}));
  const actions = [...el('builder-actions').children].map((row,index)=>{
    const kind=read(row,'kind'); let actionType='ModifyProperties'; let config;
    if(kind==='RunPlaybook') { actionType=kind; config={logicAppResourceId:read(row,'resource'),tenantId:read(row,'tenant')}; }
    else if(kind==='AddIncidentTask') { actionType=kind; config={title:read(row,'title'),description:read(row,'description')}; }
    else if(kind==='Severity') config={severity:read(row,'value')};
    else if(kind==='Status') {config={status:read(row,'value')};if(config.status==='Closed')config.classification=read(row,'classification');}
    else config={labels:lines(read(row,'value')).map(labelName=>({labelName,labelType:'User'}))};
    return {order:index+1,actionType,actionConfiguration:config};
  });
  return {displayName:el('builder-name').value.trim(),order:Number(el('builder-order').value),triggeringLogic:{triggersOn,triggersWhen,isEnabled:el('builder-enabled').value==='true',conditions},actions};
}
function builderRenderTargets() {
  const workspaces=(state.bootstrap?.workspaces||[]).filter(w=>w.enabled);
  builder.targets=new Set([...builder.targets].filter(key=>workspaces.some(w=>w.key===key)));
  const query=el('builder-workspace-search').value.toLowerCase();
  el('builder-workspaces').innerHTML=workspaces.filter(w=>`${w.display_name} ${w.key}`.toLowerCase().includes(query)).map(w=>`<label class="check workspace-choice"><input type="checkbox" data-builder-target="${escapeHtml(w.key)}" ${builder.targets.has(w.key)?'checked':''}><span><strong>${escapeHtml(w.display_name)}</strong><small>${escapeHtml(w.key)}</small></span></label>`).join('') || '<p class="empty">No matching workspaces.</p>';
  el('builder-selection-count').textContent=`${builder.targets.size} workspaces selected`;
  el('builder-preview').disabled=!builder.saved || !builder.targets.size;
}
el('builder-workspace-search').addEventListener('input',builderRenderTargets);
el('builder-workspaces').addEventListener('change',event=>{
  const key=event.target.dataset.builderTarget;
  if(!key)return;
  if(event.target.checked)builder.targets.add(key);else builder.targets.delete(key);
  builderRenderTargets();
});
el('builder-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=el('builder-save');setBusy(button,true,'Saving…');el('builder-error').hidden=true;
  try {
    const result=await api('/api/builder/save',{method:'POST',body:JSON.stringify({name:el('builder-catalog').value,properties:builderProperties()})});
    builder.saved=result;el('builder-definition').disabled=true;el('builder-save').hidden=true;
    el('builder-assignment').hidden=false;el('builder-saved').textContent=`${result.properties.displayName} saved as ${result.name}. Rule ID: ${result.rule_id}. No Azure changes made.`;
    await refresh();builderRenderTargets();el('builder-assignment').scrollIntoView({block:'start'});
  } catch(error) {el('builder-error').textContent=error.message;el('builder-error').hidden=false;}
  finally {setBusy(button,false);}
});
el('builder-preview').addEventListener('click',async()=>{
  const button=el('builder-preview');setBusy(button,true,'Planning…');
  try {renderPlan(await api('/api/builder/plan',{method:'POST',body:JSON.stringify({name:builder.saved.name,workspaces:[...builder.targets]})}),'builder');}
  catch(error){notify(error,true);}finally{setBusy(button,false);builderRenderTargets();}
});
el('builder-new').addEventListener('click',()=>{
  el('builder-form').reset();el('builder-definition').disabled=false;el('builder-save').hidden=false;el('builder-assignment').hidden=true;el('builder-error').hidden=true;
  el('builder-conditions').replaceChildren();el('builder-actions').replaceChildren();builder.saved=null;builder.targets.clear();builderAction();el('builder-name').focus();
});
builderAction();
builderRenderTargets();
