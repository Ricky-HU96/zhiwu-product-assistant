/* One main search owns its SKU records. No server credentials are stored. */
class SkuSessionCache {
  constructor(storage = null) {
    this.storage = storage;
    this.records = new Map();
    this.context = {};
    this.activeKey = '';
    try {
      const saved = JSON.parse(storage?.getItem('zhiwu-sku-session') || 'null');
      if (saved?.version === 1 && Array.isArray(saved.records)) {
        this.records = new Map(saved.records);
        this.context = saved.context || {};
        this.activeKey = saved.activeKey || '';
      }
    } catch { /* Storage may be disabled, full, or contain an older format. */ }
  }
  key(url) {
    try {
      const parsed = new URL(url);
      const asin = parsed.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})(?:\/|$)/i)?.[1];
      return asin ? `${parsed.hostname.toLowerCase()}:${asin.toUpperCase()}` : '';
    } catch { return ''; }
  }
  save() {
    try { this.storage?.setItem('zhiwu-sku-session', JSON.stringify({version: 1, context: this.context, activeKey: this.activeKey, records: [...this.records]})); }
    catch { /* Keep the complete in-memory cache even if persistence is full. */ }
  }
  reset(context = {}) {
    try { this.storage?.removeItem?.('zhiwu-sku-session'); } catch { /* Memory reset still applies. */ }
    this.records.clear();
    this.context = structuredClone(context);
    this.activeKey = '';
    this.save();
  }
  remember(job, state = {}, context = this.context) {
    const key = this.key(job?.product?.url);
    if (!key) return;
    const previous = this.records.get(key);
    const signature = JSON.stringify(context);
    if (!job.result && previous?.job.result && previous.signature === signature) {
      this.activeKey = key;
      this.save();
      return;
    }
    this.records.set(key, structuredClone({job, state, signature}));
    this.context = structuredClone(context);
    this.activeKey = key;
    this.save();
  }
  get(url, context = this.context) {
    const record = this.records.get(this.key(url));
    if (!record) return null;
    const copy = structuredClone(record);
    if (copy.signature !== JSON.stringify(context)) {
      copy.job.result = null;
      copy.job.product.visual_check_enabled = !!context.visualCheck;
      copy.job.product.vision_image_ids = [];
      copy.state = {};
    }
    return copy;
  }
  activate(url) { this.activeKey = this.key(url); this.save(); }
  active() { return structuredClone(this.records.get(this.activeKey) || null); }
}
if (typeof module !== 'undefined') module.exports = {SkuSessionCache};
