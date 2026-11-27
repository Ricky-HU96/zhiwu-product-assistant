const $ = (id) => document.getElementById(id);
let token = '', busy = false, lastData = null, timer = null, startedAt = 0, currentJob = null;
let toastTimer;
let selectedScriptIndex = 0, scriptDrafts = [], scriptEdits = [];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3200);
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path.startsWith('/api/jobs/') ? 10000 : 35000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', 'X-App-Token': token, ...options.headers } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败，请重试。');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('本地服务响应超时，已结束等待。请刷新页面检查任务状态。');
    throw error;
  } finally { clearTimeout(timeout); }
}

async function checkHealth() {
  try {
    const data = await request('/api/health');
    token = data.token;
    $('ai-status').textContent = data.ai.ready ? 'AI 已配置' : 'AI 待配置';
    $('ai-status').title = data.ai.message;
    $('ai-detail').textContent = data.ai.ready ? '由本地 .env 提供' : '请填写 .env';
    $('connection-icon').className = `connection-icon ${data.ai.ready ? 'ready' : 'error'}`;
  } catch {
    $('ai-status').textContent = '本地服务未连接';
    $('ai-detail').textContent = '请检查服务是否运行';
    $('connection-icon').className = 'connection-icon error';
  }
}

function setBusy(value) {
  busy = value;
  $('analyze-button').disabled = value;
  $('demo-button').disabled = value;
  $('analyze-label').textContent = value ? '正在分析' : '分析商品';
  $('progress-panel').hidden = !value;
  clearInterval(timer);
  if (value) {
    startedAt = Date.now();
    $('elapsed').textContent = '0 秒';
    timer = setInterval(() => { $('elapsed').textContent = `${Math.floor((Date.now() - startedAt) / 1000)} 秒`; }, 1000);
  }
}

function progress(status, message) {
  const browserActive = ['browser_starting', 'browser_loading', 'waiting_verification', 'waiting_page'].includes(status);
  $('progress-message').textContent = message;
  $('step-fetch').className = status === 'fetching' || browserActive ? 'current' : 'complete';
  $('step-analyze').className = status === 'generating' ? 'current' : status === 'repairing' ? 'complete' : '';
  $('step-script').className = status === 'repairing' ? 'current' : '';
  $('browser-panel').hidden = !browserActive;
  $('progress-panel').classList.toggle('needs-action', status === 'waiting_verification');
  $('browser-timeout-label').textContent = status === 'waiting_verification' ? '等待人工验证，最长 5 分钟' : '商品字段读取最长 30 秒';
  $('browser-title').textContent = status === 'waiting_verification' ? '需要你在商品窗口中完成验证' : status === 'waiting_page' ? '等待商品页面就绪' : '正在通过浏览器读取商品';
  $('browser-message').textContent = status === 'waiting_verification' ? '请切换到专用商品窗口，手动完成页面验证。你可以保持本页打开，工具会自动检查并继续。' : message;
  $('browser-check').disabled = status === 'browser_starting';
  $('browser-focus').disabled = status === 'browser_starting';
}

function showError(message) {
  $('error-message').textContent = message;
  $('error-panel').hidden = false;
  $('error-panel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function evidence(ids, prefix = '商品依据') {
  const row = element('div', 'evidence-row');
  row.append(element('span', '', prefix));
  ids.forEach(id => {
    const link = element('a', 'evidence-link', id);
    link.href = `#fact-${id}`;
    link.setAttribute('aria-label', `查看商品资料 ${id}`);
    link.addEventListener('click', () => { $('fact-evidence').open = true; });
    row.append(link);
  });
  return row;
}

function renderProduct(product) {
  $('results').hidden = false;
  $('empty-state').hidden = true;
  $('result-title').textContent = product.title;
  $('result-summary').textContent = '商品信息已整理，正在寻找适合短视频的内容角度。';
  const source = product.source_type;
  $('source-notice').className = `source-notice ${source}`;
  $('source-notice').textContent = source === 'demo' ? '虚构商品演示 · 资料为演示设定，分析与口播由配置的 AI 实时生成。' : source === 'manual' ? '用户提供资料 · 尚未通过商品页面核验，发布前请核对原始信息。' : source === 'browser' ? '浏览器页面资料 · 以下内容读取自专用商品窗口，价格及在售状态可能变化。' : '商品页面资料 · 以下内容取自本次获取的页面，价格及在售状态可能变化。';
  $('fact-count').textContent = `${product.facts.length} 条原始资料`;
  $('fact-list').replaceChildren();
  const range = product.price_range;
  $('price-range-label').textContent = range?.kind === 'from' ? '页面仅展示起价' : '已确认售价区间';
  $('price-range-value').textContent = range ? range.display : '暂未读取到可靠报价';
  $('price-range-scope').textContent = range ? range.scope : '不会根据其他商品、划线价或 SKU 名称推测售价。';
  const basicLabels = new Set(['产品名称', '品牌', '页面品类']);
  const priceLabels = new Set(['页面价格', '页面报价范围', '已读取报价区间', '已确认售价区间', '仅获取起价']);
  const basics = product.facts.filter(fact => basicLabels.has(fact.label));
  const features = product.facts.filter(fact => /功能描述|功能要点/.test(fact.label));
  const specs = product.facts.filter(fact => !basicLabels.has(fact.label) && !priceLabels.has(fact.label) && fact.label !== 'SKU 选项' && !features.includes(fact));
  function properties(target, facts) {
    $(target).replaceChildren(...facts.map(fact => {
      const row = element('div');
      row.append(element('dt', '', fact.label === '页面品类' ? '品类' : fact.label), element('dd', '', fact.value));
      return row;
    }));
  }
  properties('product-basics', basics);
  properties('product-specs', specs);
  $('specs-block').hidden = !specs.length;
  $('product-features').replaceChildren(...features.map(fact => element('li', '', fact.value)));
  $('features-block').hidden = !features.length;
  const variants = product.variants || [];
  $('variant-count').textContent = variants.length ? `${variants.length} 个已读取选项` : '未读取到选项';
  $('variants-overview').replaceChildren();
  const groups = new Map();
  variants.forEach(variant => {
    if (!groups.has(variant.dimension)) groups.set(variant.dimension, []);
    groups.get(variant.dimension).push(variant);
  });
  groups.forEach((options, dimension) => {
    const group = element('section', 'variant-group');
    const heading = element('div', 'variant-group-heading');
    heading.append(element('h4', '', dimension), element('span', '', `${options.length} 个选项`));
    const grid = element('div', 'variant-grid');
    options.forEach(variant => {
      const card = element('article', `variant-card${variant.selected ? ' selected' : ''}`);
      const name = element('div', 'variant-name');
      name.append(element('strong', '', variant.label));
      if (variant.selected) name.append(element('span', 'variant-badge', '当前选择'));
      const price = variant.price;
      card.append(name, element('p', `variant-price${price ? '' : ' unknown'}`, price?.display || '价格未展示'));
      const notes = [];
      if (price?.kind === 'from') notes.push('选项起价，具体组合待确认');
      else if (price?.context) notes.push(price.context);
      else if (price) notes.push('页面售价');
      if (variant.available === false) notes.push('当前不可选');
      if (notes.length) card.append(element('p', 'variant-price-note', notes.join(' · ')));
      grid.append(card);
    });
    group.append(heading, grid);
    $('variants-overview').append(group);
  });
  $('variant-notice').textContent = product.variant_notice || '本次未读取到 SKU 选项。';
  $('fact-evidence').open = false;
  product.facts.forEach(fact => {
    const item = element('article', 'fact-item');
    item.id = `fact-${fact.id}`;
    const label = element('div', 'fact-label');
    label.append(element('span', 'fact-id', fact.id), element('span', '', fact.label));
    item.append(label, element('p', 'fact-value', fact.value));
    if (fact.evidence !== fact.value) {
      const details = element('details');
      details.append(element('summary', '', '查看原文依据'), element('blockquote', '', fact.evidence));
      item.append(details);
    }
    $('fact-list').append(item);
  });
  $('missing-facts').hidden = !product.missing.length;
  $('missing-facts').textContent = `未获取：${product.missing.join('、')}。不会自动补写这些信息。`;
  $('source-link').replaceChildren();
  if (product.url) {
    const link = element('a', '', '查看 Amazon 原商品页面');
    link.href = product.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    $('source-link').append(link);
  }
  const time = new Date(product.fetched_at).toLocaleString('zh-CN', { hour12: false });
  $('source-link').append(element('span', '', `${['page', 'browser'].includes(source) ? '获取' : '整理'}时间：${time}`));
}

function renderAnalysis(result) {
  $('angles-section').hidden = false;
  $('script-section').hidden = false;
  $('result-summary').textContent = result.summary;
  $('dimension-list').replaceChildren();
  result.dimensions.forEach(item => {
    const card = element('article', 'insight-card');
    card.append(element('h3', '', item.dimension), element('p', '', item.insight));
    if (item.evidence_ids.length) card.append(evidence(item.evidence_ids));
    $('dimension-list').append(card);
  });
  $('sku-recommendations').replaceChildren();
  $('sku-recommendations-section').hidden = !result.sku_recommendations.length;
  result.sku_recommendations.forEach(item => {
    const card = element('article', 'sku-recommendation');
    card.append(element('div', 'sku-choice', variantNames(item.variant_ids)), element('h4', '', item.audience),
      element('p', '', `需求：${item.need}`), element('p', '', `选择理由：${item.selection_reason}`),
      element('p', 'sku-caveat', `适用边界：${item.caveat}`), evidence(item.evidence_ids));
    $('sku-recommendations').append(card);
  });
  $('angle-list').replaceChildren();
  result.angles.forEach((angle, index) => {
    const card = element('article', 'angle');
    const heading = element('h3', 'angle-heading');
    heading.append(element('span', 'angle-badge', index === 0 ? '推荐主角度' : '备选角度'), element('span', '', angle.audience));
    const grid = element('div', 'angle-grid');
    [['使用场景 · 推断', 'scenario'], ['用户困扰 · 推断', 'pain'], ['相关功能 · 需核对原文', 'feature'], ['消费者收益 · 推断', 'benefit']].forEach(([label, key]) => {
      const cell = element('div', 'angle-cell');
      cell.append(element('span', '', label), element('p', '', angle[key]));
      grid.append(cell);
    });
    card.append(heading, grid, element('p', 'angle-reason', `为什么值得讲：${angle.reason}`), evidence(angle.evidence_ids));
    if (angle.variant_ids.length) card.append(element('p', 'angle-sku', `对应选项：${variantNames(angle.variant_ids)}`));
    $('angle-list').append(card);
  });
  $('limitations-list').replaceChildren(...result.limitations.map(text => element('li', '', text)));
  scriptDrafts = result.scripts.map(script => script.script);
  scriptEdits = result.scripts.map(() => false);
  $('script-tabs').replaceChildren();
  result.scripts.forEach((script, index) => {
    const button = element('button', 'script-tab');
    button.type = 'button';
    button.setAttribute('role', 'tab');
    button.id = `script-tab-${index}`;
    button.setAttribute('aria-controls', 'script-version-panel');
    button.append(element('strong', '', script.title), element('span', '', script.audience));
    button.addEventListener('click', () => renderScript(index));
    $('script-tabs').append(button);
  });
  renderScript(0);
  $('download-button').disabled = false;
}

function variantNames(ids) {
  const variants = lastData?.product?.variants || [];
  return ids.map(id => variants.find(variant => variant.id === id)?.label || id).join(' / ');
}

function renderScript(index) {
  selectedScriptIndex = index;
  const script = lastData.result.scripts[index];
  $('script-tabs').querySelectorAll('button').forEach((button, i) => {
    button.classList.toggle('selected', i === index);
    button.setAttribute('aria-selected', String(i === index));
  });
  $('script-version-panel').setAttribute('aria-labelledby', `script-tab-${index}`);
  $('script-target').textContent = `面向：${script.audience} · 场景：${script.scenario}${script.variant_ids.length ? ` · 选项：${variantNames(script.variant_ids)}` : ' · 通用场景版本'}`;
  $('hook-text').textContent = script.hook;
  $('script-text').value = scriptDrafts[index];
  $('script-edit-note').textContent = scriptEdits[index] ? '本版口播已修改，请重新核对字数、依据和镜头对应关系。' : '本版已通过字数与依据编号检查，发布前请核对商品描述。';
  updateCount();
  $('script-evidence').replaceChildren(evidence(script.script_evidence_ids, '本版使用的商品依据'));
  $('shots-list').replaceChildren();
  script.shots.forEach((shot, index) => {
    const card = element('article', 'shot');
    card.append(element('span', 'shot-label', `镜头 ${index + 1}`), element('p', 'shot-visual', shot.visual), element('p', 'shot-voiceover', `口播：${shot.voiceover}`));
    if (shot.evidence_ids.length) card.append(evidence(shot.evidence_ids, '依据'));
    $('shots-list').append(card);
  });
}

async function pollJob(identifier) {
  let rendered = false;
  let failedPolls = 0;
  while (currentJob === identifier) {
    let job;
    try {
      job = await request(`/api/jobs/${identifier}`);
      failedPolls = 0;
    } catch (error) {
      failedPolls += 1;
      if (failedPolls > 4) throw error;
      await new Promise(resolve => setTimeout(resolve, 1800));
      continue;
    }
    progress(job.status, job.message);
    $('direct-error').hidden = !job.direct_error;
    $('direct-error').textContent = job.direct_error ? `自动请求未成功：${job.direct_error}` : '';
    if (job.product && !rendered) { renderProduct(job.product); rendered = true; }
    if (['error', 'cancelled'].includes(job.status)) throw new Error(job.error);
    if (job.status === 'done') {
      lastData = job;
      renderAnalysis(job.result);
      sessionStorage.removeItem('zhiwu-job');
      toast('内容简报已完成');
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1400));
  }
}

async function startAnalysis(demo = false) {
  if (busy) return;
  const payload = { url: $('product-url').value.trim(), audience: $('audience').value.trim(), manual_text: $('manual-text').value.trim(), fetch_mode: $('fetch-mode').value, demo };
  if (!demo && !payload.url && !payload.manual_text) {
    showError('请粘贴 Amazon 商品链接，或补充商品信息。');
    $('product-url').focus();
    return;
  }
  setBusy(true);
  $('error-panel').hidden = true;
  $('empty-state').hidden = true;
  $('results').hidden = true;
  $('angles-section').hidden = true;
  $('script-section').hidden = true;
  $('download-button').disabled = true;
  lastData = null;
  $('direct-error').hidden = true;
  progress('fetching', demo ? '正在整理演示资料' : payload.manual_text ? '正在整理补充的商品资料' : '正在获取 Amazon 商品信息');
  try {
    await checkHealth();
    const { id } = await request('/api/analyze', { method: 'POST', body: JSON.stringify(payload) });
    currentJob = id;
    sessionStorage.setItem('zhiwu-job', id);
    await pollJob(id);
  } catch (error) {
    sessionStorage.removeItem('zhiwu-job');
    showError(error.message);
    if ($('results').hidden) $('empty-state').hidden = false;
    else $('result-summary').textContent = '商品资料已保留，本次内容生成未完成。';
  } finally {
    currentJob = null;
    setBusy(false);
  }
}

function updateCount() {
  const count = Array.from($('script-text').value).length;
  $('char-count').textContent = `${count} / 150 字`;
  $('char-count').className = count > 150 ? 'over' : '';
}

$('product-form').addEventListener('submit', event => { event.preventDefault(); startAnalysis(); });
$('demo-button').addEventListener('click', () => startAnalysis(true));
$('open-manual').addEventListener('click', () => { $('manual-details').open = true; $('manual-text').focus(); });
async function browserAction(action) {
  if (!currentJob) return;
  const button = $(`browser-${action}`);
  button.disabled = true;
  try {
    await request(`/api/jobs/${currentJob}/${action}`, { method: 'POST', body: '{}' });
    if (action === 'cancel') {
      $('manual-details').open = true;
      $('manual-text').focus();
      toast('正在结束浏览器读取，可在上方粘贴商品资料');
    } else if (action === 'check') toast('已请求重新检查商品页面');
    else toast('已请求显示专用商品窗口');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
}
['check', 'focus', 'cancel'].forEach(action => $(`browser-${action}`).addEventListener('click', () => browserAction(action)));
$('script-text').addEventListener('input', () => {
  scriptDrafts[selectedScriptIndex] = $('script-text').value;
  scriptEdits[selectedScriptIndex] = $('script-text').value !== lastData?.result.scripts[selectedScriptIndex].script;
  updateCount();
  $('script-edit-note').textContent = '口播已手动修改，请重新核对字数、依据和镜头对应关系。';
});
$('copy-button').addEventListener('click', async () => {
  const text = $('script-text').value;
  try {
    await navigator.clipboard.writeText(text);
    toast(Array.from(text).length > 150 ? '已复制；当前口播超过 150 字，请精简。' : '口播已复制');
  } catch {
    $('script-text').focus();
    $('script-text').select();
    toast('请按 Ctrl+C 复制已选中的口播');
  }
});
$('download-button').addEventListener('click', () => {
  if (!lastData) return;
  const edited = scriptEdits.some(Boolean);
  const scripts = lastData.result.scripts.map((script, index) => ({ ...script, script: scriptDrafts[index], char_count: Array.from(scriptDrafts[index]).length, edited: scriptEdits[index] }));
  const data = { ...lastData, result: { ...lastData.result, scripts }, selected_script_index: selectedScriptIndex, edited,
    editorial_note: edited ? '口播经用户修改，开场、依据引用与原分镜未重新校验。' : 'AI 分析为推断，发布前请核对商品信息。' };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' }));
  const link = element('a');
  link.href = url;
  link.download = `知物-商品内容简报-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('内容简报已导出');
});

async function initialize() {
  await checkHealth();
  const saved = sessionStorage.getItem('zhiwu-job');
  if (!saved) return;
  setBusy(true);
  $('empty-state').hidden = true;
  currentJob = saved;
  try { await pollJob(saved); }
  catch (error) { sessionStorage.removeItem('zhiwu-job'); showError(error.message); if ($('results').hidden) $('empty-state').hidden = false; }
  finally { currentJob = null; setBusy(false); }
}
initialize();
