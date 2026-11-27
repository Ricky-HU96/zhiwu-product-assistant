const test = require('node:test');
const assert = require('node:assert/strict');
const {SkuSessionCache} = require('../web/sku-session.js');
const url = 'https://www.amazon.com/dp/B09JP62T59';
const other = 'https://www.amazon.com/dp/B09MKPDJRT';
const context = {audience: '办公用户', visualCheck: false};
const job = {product: {url, selected_sku: {label: '黑银 · 42英寸'}}, result: {scripts: [{script: '原文'}]}};
test('same search restores exact SKU, edited script, selected tab and gallery', () => {
  const cache = new SkuSessionCache();
  cache.reset(context);
  cache.remember(job, {drafts: ['修改后的口播'], selected: 2, image: 3});
  cache.remember({product: {url: other}, result: null});
  const restored = cache.get(url + '?th=1&tracking=ignored');
  assert.equal(restored.job.product.selected_sku.label, '黑银 · 42英寸');
  assert.deepEqual(restored.state, {drafts: ['修改后的口播'], selected: 2, image: 3});
  restored.job.result.scripts[0].script = '外部修改';
  assert.equal(cache.get(url).job.result.scripts[0].script, '原文');
  assert.equal(cache.get(url.replace('.com', '.de')), null);
});
test('a main search, even for the same URL, clears all old SKU records', () => {
  const cache = new SkuSessionCache();
  cache.reset(context); cache.remember(job);
  cache.remember({product: {url: other}, result: null});
  cache.reset(context);
  assert.equal(cache.get(url), null);
  assert.equal(cache.get(other), null);
  assert.equal(cache.active(), null);
});
test('changed audience or review option retains facts but never reuses the old generation', () => {
  const cache = new SkuSessionCache(); cache.reset(context); cache.remember(job);
  for (const changed of [{...context, visualCheck: true}, {...context, audience: '游戏玩家'}]) {
    assert.equal(cache.get(url, changed).job.result, null);
    assert.equal(cache.get(url, changed).job.product.url, url);
  }
  cache.remember({product: {url}, result: null});
  assert.ok(cache.get(url).job.result);
});
test('refresh restores this tab search; unavailable storage still permits memory caching', () => {
  const values = new Map();
  const storage = {getItem: k => values.get(k), setItem: (k,v) => values.set(k,v)};
  const first = new SkuSessionCache(storage); first.reset(context); first.remember(job);
  assert.deepEqual(new SkuSessionCache(storage).active().job, job);
  first.reset(context);
  assert.equal(new SkuSessionCache(storage).active(), null);
  const blocked = new SkuSessionCache({getItem() {throw Error();}, setItem() {throw Error();}});
  blocked.remember(job); assert.ok(blocked.get(url).job.result);
});
test('new search cannot resurrect old persisted records when a later storage write fails', () => {
  const values = new Map();
  let failWrites = false;
  const storage = {getItem: k => values.get(k), removeItem: k => values.delete(k), setItem: (k,v) => {if (failWrites) throw Error('quota'); values.set(k,v);}};
  const cache = new SkuSessionCache(storage); cache.remember(job);
  failWrites = true; cache.reset(context);
  assert.equal(new SkuSessionCache(storage).get(url), null);
});
