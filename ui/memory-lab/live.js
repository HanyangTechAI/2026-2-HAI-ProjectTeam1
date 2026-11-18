const $ = id => document.getElementById(id);
const comparisonStrategies = {sliding_window: 'Sliding Window', vector_top_k: 'Vector Top-k', utility_per_token: 'Utility-per-Token', proposed: 'Proposed'};
const ablations = {
  full: 'Full Proposed', no_constraint_preservation: 'Constraint 보존 제거',
  no_temporal: 'Temporal 제거', no_state_mandatory: 'State mandatory 제거',
  no_budget_aware_packing: 'Packing 제거'
};
const presets = {...comparisonStrategies, ...ablations};
const resultCache = 'memory-lab-results-v1';
const escape = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
let mode = 'compare';
let scenarios = [];
let settings;
let selectedScenario;
const current = () => selectedScenario;
const topicNames = {constraint:'제약 준수', temporal:'시점 판단', state:'필수 상태', flexible:'추가 근거'};
const taskNames = {temporal_update:'파일 조회', approval:'이메일 발송'};
function caseLabel(s) {
  return '사례 '+String(s.sample).padStart(3,'0')+' · seed '+s.seed;
}
function selectCase(s) {
  selectedScenario = s;
  $('catalog-path').textContent = [topicNames[s.topic],taskNames[s.task],caseLabel(s)].join(' / ');
  $('catalog').open = false;
  reset();
}
function buildCatalog(topic, task, focusFirst = false) {
  const root = $('scenario-folders');
  root.replaceChildren();
  function addButton(label, click) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.onclick = click;
    root.append(button);
    return button;
  }
  const cases = scenarios.filter(s => !topic || s.topic === topic);
  if (topic) {
    const path = task ? topicNames[topic]+' / '+taskNames[task] : topicNames[topic];
    addButton('‹ 이전　·　'+path, () => buildCatalog(task ? topic : null, null, true)).className = 'catalog-back';
  }
  if (task) {
    for (const s of cases.filter(s => s.task === task)) {
      const button = addButton(caseLabel(s), () => {
        selectCase(s);
        $('catalog-path').focus();
      });
      button.setAttribute('aria-pressed', String(current()?.id === s.id));
      const query = document.createElement('span');
      query.textContent = s.query;
      button.append(query);
    }
  } else {
    const field = topic ? 'task' : 'topic';
    for (const [id, name] of Object.entries(topic ? taskNames : topicNames)) {
      const count = cases.filter(s => s[field] === id).length;
      if (count) addButton(name+' ('+count+')　›', () => buildCatalog(topic || id, topic ? id : null, true));
    }
  }
  root.scrollTop = 0;
  if (focusFirst) root.querySelector('button:not(.catalog-back)')?.focus();
}
$('catalog').ontoggle = () => { if ($('catalog').open) buildCatalog(); };
$('catalog').onkeydown = event => {
  if (event.key === 'Escape') {
    $('catalog').open = false;
    $('catalog-path').focus();
  }
};
document.addEventListener('pointerdown', event => {
  if (!$('catalog').contains(event.target)) $('catalog').open = false;
});
const variantsForMode = () => Object.keys(mode === 'compare' ? comparisonStrategies : ablations);
function cacheKey() {
  const params = new URLSearchParams({scenario:current().id, budget:$('budget').value, planner:$('planner').value, mode});
  params.set('main', settings.configId);
  params.set('horizon', $('horizon').valueAsNumber);
  return '/memory-lab-results/v1?' + params;
}
function displayRecord(record, baseline) {
  if (record === baseline && record.scenario) showScenario(record.scenario);
  showOverview(record);
  $('results').insertAdjacentHTML('beforeend', resultCard(record, baseline));
}
async function restoreResults() {
  const key = cacheKey();
  try {
    const cache = await caches.open(resultCache);
    const response = await cache.match(key);
    if (!response) return false;
    const records = await response.json();
    const variants = variantsForMode();
    if (records.length !== variants.length || !variants.every((id,i) => records[i]?.preset === id)) return false;
    prepareOverview();
    for (const record of records) displayRecord(record, records[0]);
    $('run-details').hidden = false;
    $('progress').textContent = '저장된 결과 · '+records.length+'개 조건';
    return true;
  } catch {
    return false;
  }
}
const descriptions = {
  sliding_window: '최근 기억 순서', vector_top_k: '요청과의 유사도',
  utility_per_token: '토큰당 효용', proposed: '제약과 필수 상태 우선',
  full: '모든 구성 요소 사용', no_constraint_preservation: '제약 우선 보존 해제',
  no_temporal: '시점별 버전 판별 해제', no_state_mandatory: '필수 상태 우선 배치 해제',
  no_budget_aware_packing: '토큰 비용 기반 선택 해제'
};
function prepareOverview() {
  $('run-details').open = false;
  $('run-details').hidden = true;
  $('results').replaceChildren();
  const variants = mode === 'compare' ? comparisonStrategies : ablations;
  $('overview').innerHTML = Object.entries(variants).map(([id, label]) =>
    `<tr id="row-${id}"><th scope="row">${label}<small>${descriptions[id]}</small></th>
    <td>—</td><td>—</td><td class="wait">실행 대기</td></tr>`).join('');
}
function showOverview(record) {
  const row = $('row-'+record.preset);
  const first = record.steps[0];
  row.classList.remove('running');
  row.cells[1].textContent = first.input_tokens == null ? '입력 구성 중단' : first.selected.length+' / '+record.diagnostics.pool_size+'개';
  row.cells[2].innerHTML = '<code>'+escape(actionName(record.calls[0]))+'</code><small>'+escape(fileName(record.calls[0]))+'</small>';
  row.cells[3].className = record.success ? 'mint' : 'amber';
  row.cells[3].textContent = record.success ? '과제 성공' : '과제 실패';
  const reason = budgetStop(record);
  if (reason) {
    const note = document.createElement('small');
    note.textContent = reason;
    row.cells[3].append(note);
  }
}
function budgetStop(record) {
  const step = record.steps.at(-1);
  if (step.input_tokens != null) return '';
  if (step.fixed_tokens > step.limit)
    return step.number+'단계 · 고정 입력 '+step.fixed_tokens+' > 유효 예산 '+step.limit+' tokens';
  if (record.stop !== 'context_budget') return '';
  const available = step.limit - step.fixed_tokens;
  const mandatory = step.mandatory.reduce((sum,item) => sum + item.tokens, 0);
  if (mandatory > available)
    return step.number+'단계 · 필수 기억 '+mandatory+' > 기억 예산 '+available+' tokens (유효 예산 '+step.limit+' − 고정 입력 '+step.fixed_tokens+')';
  return step.number+'단계 · 입력 형식까지 포함한 요청이 유효 예산 '+step.limit+' tokens 초과';
}
function reset() {
  $('comparison-board').hidden = true;
  prepareOverview();
  $('progress').textContent = '';
  const scenario = current();
  if (!scenario) return;
  showScenario(scenario);
  if ($('horizon').valueAsNumber !== scenario.horizon)
    $('history').textContent = '실행하면 입력한 Horizon에 맞춰 사건별 턴을 표시합니다.';
}
function showScenario(scenario) {
  $('query').textContent = scenario.query;
  $('history').innerHTML = scenario.history.map(event =>
    '<p><b>'+event.turn+'턴</b>'+escape(event.text)+'</p>').join('');
}
function actionName(call) {
  return call ? call.tool_name+'.'+call.action : '도구 호출 없음';
}
function fileName(call) {
  return call?.arguments?.attachment || call?.arguments?.filename || '';
}
const criteria = {
  pipeline_ok: '파이프라인 정상 실행', goal_completed: '작업 목표 완료',
  expected_calls: '기대 도구 호출과 인자 충족', forbidden_calls_absent: '금지된 호출 없음',
  constraints_respected: '적용 제약 준수', required_states_correct: '요청 시점의 상태·버전 사용',
  answer_matches: '기준 답변 일치', approval_correct: '승인 절차 준수',
  within_budget: '입력 토큰 예산 준수', fixed_budget_valid: '고정 입력이 예산 이내'
};
function verdictDetails(record) {
  const entries = Object.entries(record.checks);
  const failed = entries.filter(([,value]) => value === false).map(([key]) => criteria[key]);
  const rows = entries.map(([key,value]) => '<li>'+criteria[key]+'　'+
    (value === null ? '해당 없음' : value ? '충족' : '미충족')+'</li>').join('');
  return (failed.length ? '<p class="amber">미충족 조건　'+failed.join(' · ')+'</p>' : '')+
    '<details><summary>성공 판정 근거</summary><ul>'+rows+'</ul><b>시나리오 기대 호출</b><pre>'+
    escape(JSON.stringify(record.expected_calls,null,2))+'</pre><b>실제 호출 순서</b><pre>'+
    escape(JSON.stringify(record.calls,null,2))+'</pre><small>실험 계획 §4.1 · benchmark/evaluator.py · task_success</small></details>';
}
function resultCard(record, baseline) {
  const d = record.diagnostics;
  const input = record.steps[0];
  const mandatoryTokens = input.mandatory.reduce((sum,item)=>sum+item.tokens,0);
  const selection = input.input_tokens == null ? '입력 구성 중단 (고정 '+input.fixed_tokens+
    ' + 필수 기억 항목 '+mandatoryTokens+' tokens, 사용 가능 '+input.limit+' tokens)' :
    '기억 '+input.selected.length+'개 선택 → 전체 요청 '+input.input_tokens+' / '+d.budget+' tokens';
  const checks = '<p>전체 기억 '+d.pool_size+'개 ('+d.pool_tokens+' tokens) → '+selection+'</p><p>기대 도구 호출 '+(d.expected?'확인':'누락')+' · 목표 완료 '+(d.goal?'예':'아니요')+
    (d.peak == null?'':' · 실제 최대 입력 '+d.peak+' / '+d.budget+' tokens')+'</p>'+
    verdictDetails(record);
  const first = record.steps[0];
  const short = ids => ids.map(id=>id.replace(current().id+'_','')).join(', ') || '없음';
  const extra = first.selected.filter(x=>!baseline.steps[0].selected.includes(x));
  const missing = baseline.steps[0].selected.filter(x=>!first.selected.includes(x));
  const comparison = record.preset===baseline.preset ? '' :
    '<p class="delta">'+escape(presets[baseline.preset])+' 대비 첫 Context · 추가 '+escape(short(extra))+' / 제외 '+escape(short(missing))+'</p>';
  const traces = record.steps.map(step => '<div class="trace"><small>Agent step '+step.number+'</small><b>검색된 기억</b><code>'+escape(short(step.retrieved))+'</code><b>최종 Context에 남은 기억</b><code>'+escape(short(step.selected))+'</code><details><summary>전달된 Context 원문</summary><pre>'+escape(step.context || 'Context 생성 전 종료')+'</pre></details><b>행동과 도구 응답</b><pre>'+escape(JSON.stringify(step.action || '행동 전 종료',null,2))+'</pre></div>').join('');
  return '<article class="outcome"><header><h3>'+presets[record.preset]+'</h3><b class="'+(record.success?'mint':'amber')+'">'+(record.success?'과제 성공':'과제 실패')+'</b></header>'+checks+'<p>첫 도구 호출　<code>'+escape(actionName(record.calls[0]))+'</code>　<span class="file">'+escape(fileName(record.calls[0]))+'</span></p>'+(record.response ? '<p><b>최종 답변</b>　'+escape(record.response)+'</p>' : '')+comparison+'<details><summary>판단 과정 보기 · '+record.steps.length+'단계</summary>'+traces+'<p>종료 사유　'+escape(record.stop)+'</p></details></article>';
}
document.querySelector('.chapters').onclick = event => {
  if (!event.target.dataset.mode) return;
  mode = event.target.dataset.mode;
  for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-current',button.dataset.mode===mode?'step':'false');
  $('title').textContent = mode==='compare'?'네 가지 기억 전략 비교':'어떤 구성 요소가 결과를 바꾸는가?';
  $('intro').textContent = mode==='compare'?'Sliding Window, Vector Top-k, Utility-per-Token, Proposed에 같은 시나리오와 예산을 적용합니다.':'Full Proposed를 기준으로 Constraint 보존, Temporal, State mandatory, Packing을 하나씩 끄고 다섯 조건을 실행합니다.';
  reset();
};
$('horizon').oninput = reset;
$('budget').onchange = reset;
$('planner').onchange = () => {
  reset();
  $('planner-info').textContent = $('planner').value === 'gemini' ? 'Gemini API · Gemini 토큰 계산 · 각 조건에서 최대 8단계 생성 호출, API 사용량 발생' : '규칙 기반 Planner · 추정 토큰 수';
};
document.querySelector('form').onsubmit = async event => {
  event.preventDefault();
  if ($('run').disabled) return;
  $('cache-status').textContent = '';
  const variants = variantsForMode();
  const controls = document.querySelectorAll('select,input,button');
  controls.forEach(control=>control.disabled=true);
  $('catalog').inert = true;
  $('comparison-board').hidden = false;
  const records = [];
  try {
    if (await restoreResults()) return;
    prepareOverview();
    for (const [i,preset] of variants.entries()) {
      const row = $('row-'+preset);
      row.classList.add('running');
      row.cells[3].textContent = '실행 중';
      $('progress').textContent = (i+1)+' / '+variants.length+'　'+presets[preset]+' 실행 중…';
      const response = await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario:current().id,budget:Number($('budget').value),horizon:$('horizon').valueAsNumber,preset,planner:$('planner').value})});
      const record = await response.json();
      if (!response.ok) throw Error(record.error);
      records.push(record);
      displayRecord(record, records[0]);
    }
    $('progress').textContent = variants.length+'개 조건 실행 완료';
    try {
      const cache = await caches.open(resultCache);
      await cache.put(cacheKey(), new Response(JSON.stringify(records)));
    } catch {
      $('progress').textContent += ' · 이 브라우저에 저장할 수 없음';
    }
  } catch(error) {
    const row = $('overview').querySelector('.running');
    if (row) {
      row.classList.remove('running');
      row.cells[3].textContent = '실행 오류';
    }
    $('progress').textContent='실험을 완료하지 못했습니다. '+error.message;
  }
  finally {
    $('run-details').hidden = !$('results').childElementCount;
    controls.forEach(control=>control.disabled=false);
    $('catalog').inert = false;
  }
};
async function start() {
  $('connection').hidden=true;
  $('scenario-folders').replaceChildren();
  selectedScenario = undefined;
  $('run').disabled=true;
  $('query').textContent='시나리오 불러오는 중…';
  try {
    [scenarios, settings] = await Promise.all(['/api/scenarios','/api/settings'].map(async path => {
      const response = await fetch(path, {cache:'no-store'});
      if (!response.ok) throw Error();
      return response.json();
    }));
    if (!Array.isArray(scenarios) || !scenarios.length) throw Error();
    $('budget').replaceChildren(...settings.budgets.map(value => new Option(value, value)));
    $('budget').value = settings.defaultBudget;
    $('horizon-presets').replaceChildren(...settings.horizons.map(value => new Option(value, value)));
    $('horizon').value = settings.defaultHorizon;
    selectCase(scenarios[0]);
    $('run').disabled=false;
  } catch {
    scenarios=[];
    $('query').textContent='시나리오 목록을 불러올 수 없습니다';
    $('connection-message').textContent='실험 서버에서 화면을 열면 시나리오를 선택하고 실행할 수 있습니다.';
    $('connection').hidden=false;
  }
}
$('retry').onclick=start;
$('clear-cache').onclick = async () => {
  $('clear-cache').disabled = true;
  $('run').disabled = true;
  try {
    const deleted = await caches.delete(resultCache);
    reset();
    $('cache-status').textContent = deleted ? '저장된 결과를 삭제했어요.' : '저장된 결과가 없어요.';
  } catch {
    $('cache-status').textContent = '캐시를 삭제하지 못했어요.';
  } finally {
    $('clear-cache').disabled = false;
    $('run').disabled = !current();
  }
};
start();
