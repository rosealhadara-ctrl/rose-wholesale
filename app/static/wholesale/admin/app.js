// Rose wholesale admin (روز جملة): suppliers, sheet import, products, pricing, tiers, subscription.
(function () {
  'use strict';

  const API = '/api/admin/wholesale';
  const UNITS = [['piece', 'قطعة'], ['dozen', 'دستة'], ['carton', 'كرتونة']];
  const RULES = {
    tier_fixed: 'سعر ثابت للفئة',
    product_fixed: 'سعر ثابت للصنف',
    product_margin: 'هامش الصنف',
    tier_margin: 'هامش الفئة',
    global_margin: 'الهامش العام',
  };
  const state = {
    view: 'orders',
    counts: {},
    orders: { status: 'submitted', q: '', page: 1 },
    traders: { status: 'pending', q: '' },
    shortSupplier: '',
    summary: null,
    products: { q: '', supplier_id: '', section_id: '', status: '', tier_id: '', page: 1, selected: new Set(), data: null },
    imp: null,
    subStatus: 'pending',
  };

  const view = document.getElementById('view');
  const drawer = document.getElementById('drawer');
  const drawerPanel = document.getElementById('drawer-panel');

  // ------------------------------------------------------------- helpers
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (v) => (v == null || v === '' ? '—' : Number(v).toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const pct = (v) => (v == null || v === '' ? '' : `${Number(v).toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 2 })}٪`);
  const dateText = (v) => (v ? new Date(v).toLocaleString('ar-EG-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' }) : '—');
  const numOrNull = (v) => (v === '' || v == null ? null : Number(String(v).replace(/[٠-٩]/g, (d) => '٠١٢٣٤٥٦٧٨٩'.indexOf(d)).replace('٫', '.')));

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      location.href = '/staff-login/?next=/admin/wholesale/';
      throw new Error('سجّل دخول الأول');
    }
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      let msg = 'حصلت مشكلة. جرّب تاني.';
      if (data && typeof data.detail === 'string') msg = data.detail;
      else if (data && Array.isArray(data.detail)) msg = 'فيه خانة مكتوبة بشكل غلط.';
      throw new Error(msg);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, error) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = 'ws-toast' + (error ? ' error' : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 3000);
  }

  async function run(button, fn) {
    if (button) button.disabled = true;
    try { return await fn(); } catch (err) { toast(err.message, true); return undefined; } finally { if (button) button.disabled = false; }
  }

  const qs = (params) => Object.entries(params).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  const options = (list, value, empty) => (empty != null ? `<option value="">${esc(empty)}</option>` : '') + list.map((o) => `<option value="${esc(o.value)}"${String(o.value) === String(value) ? ' selected' : ''}>${esc(o.label)}</option>`).join('');

  async function loadSummary() {
    state.summary = await api('GET', `${API}/summary`);
    const badge = document.getElementById('pending-badge');
    const n = state.summary.pending_subscriptions;
    badge.hidden = !n;
    badge.textContent = n;
    const c = await api('GET', `${API}/counts`);
    state.counts = c;
    [['chats-badge', c.chat_reports], ['plus-badge', c.plus_requests], ['returns-badge', c.returns_attention], ['feedback-badge', c.feedback_open], ['delivery-badge', c.delivery_attention], ['payments-badge', c.pending_payments], ['orders-badge', c.orders_to_review], ['shortages-badge', c.waiting_shortages], ['traders-badge', c.pending_traders]].forEach(([id, v]) => {
      const el = document.getElementById(id);
      el.hidden = !v;
      el.textContent = v;
    });
    return state.summary;
  }
  const suppliersList = () => (state.summary ? state.summary.suppliers : []);
  const tiersList = () => (state.summary ? state.summary.tiers : []);
  const sectionsList = () => (state.summary ? state.summary.sections : []);

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll('.ws-tabs button').forEach((b) => b.addEventListener('click', () => go(b.dataset.view)));
  function go(name) {
    state.view = name;
    document.querySelectorAll('.ws-tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.view === name)));
    if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
    render();
  }
  async function render() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    try {
      await loadSummary();
      ({ orders: renderOrders, points: renderPoints, chats: renderChatReports, plus: renderPlus, reports: renderReports, returns: renderReturns, ratings: renderRatings, feedback: renderFeedback, delivery: renderDelivery, employees: renderEmployees, payments: renderPayments, credit: renderCredit, settings: renderSettings, shortages: renderShortages, traders: renderTraders, products: renderProducts, import: renderImport, suppliers: renderSuppliers, pricing: renderPricing, subscription: renderSubscription })[state.view]();
    } catch (err) {
      view.innerHTML = `<div class="alert bad">${esc(err.message)}</div>`;
    }
  }

  // ------------------------------------------------------------ products
  async function renderProducts() {
    const p = state.products;
    const s = state.summary;
    view.innerHTML = `
      <div class="ws-section-head">
        <div><h2>الأصناف والأسعار</h2><p>الأسعار المعروضة هي اللي التاجر في الفئة المختارة هيشوفها. المورد مش بيظهر للتاجر.</p></div>
        <div class="row"><button class="btn" id="go-import">رفع شيت مورد</button><button class="btn primary" id="add-product">إضافة صنف يدوي</button></div>
      </div>
      <div class="card stack">
        <div class="row">
          <input type="search" id="p-q" placeholder="دوّر بالاسم أو الكود" value="${esc(p.q)}" style="flex:2;min-width:200px">
          <select id="p-supplier" style="flex:1;min-width:150px" aria-label="المورد">${options(s.suppliers.map((x) => ({ value: x.id, label: x.alias })), p.supplier_id, 'كل الموردين')}</select>
          <select id="p-section" style="flex:1;min-width:150px" aria-label="القسم">${options(s.sections.map((x) => ({ value: x.id, label: x.name })), p.section_id, 'كل الأقسام')}</select>
          <label class="row small muted" style="flex:1;min-width:200px">أسعار فئة
            <select id="p-tier" style="flex:1">${options(s.tiers.map((t) => ({ value: t.id, label: t.name + (t.is_default ? ' (افتراضي)' : '') })), p.tier_id || (s.tiers.find((t) => t.is_default) || {}).id)}</select>
          </label>
        </div>
        <div class="chips" id="p-status"></div>
      </div>
      <div id="p-table" style="margin-top:16px"></div>`;
    view.querySelector('#go-import').onclick = () => go('import');
    view.querySelector('#add-product').onclick = openManualProduct;
    let timer;
    view.querySelector('#p-q').addEventListener('input', (e) => { clearTimeout(timer); timer = setTimeout(() => { p.q = e.target.value.trim(); p.page = 1; loadProducts(); }, 300); });
    view.querySelector('#p-supplier').onchange = (e) => { p.supplier_id = e.target.value; p.page = 1; loadProducts(); };
    view.querySelector('#p-section').onchange = (e) => { p.section_id = e.target.value; p.page = 1; loadProducts(); };
    view.querySelector('#p-tier').onchange = (e) => { p.tier_id = e.target.value; loadProducts(); };
    await loadProducts();
  }

  async function loadProducts() {
    const p = state.products;
    const box = view.querySelector('#p-table');
    if (!box) return;
    box.classList.add('loading');
    try {
      p.data = await api('GET', `${API}/products?${qs({ q: p.q, supplier_id: p.supplier_id, section_id: p.section_id, status: p.status, tier_id: p.tier_id, page: p.page })}`);
    } catch (err) {
      box.innerHTML = `<div class="alert bad">${esc(err.message)}</div>`;
      return;
    } finally {
      box.classList.remove('loading');
    }
    const c = p.data.counts;
    const chips = [['', 'الكل', c.all], ['available', 'ظاهرة للتاجر', c.available], ['hidden', 'مخفية يدوي', c.hidden], ['no_supplier', 'مالهاش مورد', c.no_supplier], ['multi_supplier', 'أكتر من مورد', c.multi_supplier], ['warnings', 'فيها تحذير', c.warnings]];
    const st = view.querySelector('#p-status');
    st.innerHTML = chips.map(([k, l, n]) => `<button class="chip" data-k="${k}" aria-pressed="${p.status === k}">${l} <small>${n.toLocaleString('ar-EG-u-nu-latn')}</small></button>`).join('');
    st.querySelectorAll('.chip').forEach((b) => (b.onclick = () => { p.status = b.dataset.k; p.page = 1; loadProducts(); }));
    drawProductTable();
  }

  function priceCell(info, unit) {
    if (!info) return '<td class="price-cell muted">—</td>';
    let extra = '';
    if (unit !== 'piece') {
      extra = `<span class="per">${info.per_piece != null ? `القطعة ${money(info.per_piece)}` : 'عدد القطع مش معروف'}${info.saving_percent ? ` <span class="save">وفّر ${info.saving_percent.toLocaleString('ar-EG-u-nu-latn')}٪</span>` : ''}</span>`;
    }
    return `<td class="price-cell"><b class="num">${money(info.price)}</b>${extra}<span class="per">شراء ${money(info.cost)}</span></td>`;
  }

  function drawProductTable() {
    const p = state.products;
    const box = view.querySelector('#p-table');
    const data = p.data;
    if (!data.items.length) {
      box.innerHTML = `<div class="card empty">${data.counts.all ? 'مفيش أصناف بالفلتر ده.' : 'لسه مفيش أصناف. ابدأ بإضافة مورد ورفع الشيت بتاعه.'}</div>`;
      return;
    }
    const allSelected = data.items.every((i) => p.selected.has(i.id));
    box.innerHTML = `
      <div class="table-wrap"><table>
        <thead><tr>
          <th style="width:44px"><input type="checkbox" id="sel-all" aria-label="اختار الكل" ${allSelected ? 'checked' : ''}></th>
          <th class="hide-sm" style="width:56px"></th><th>الصنف</th><th>الموردين</th>
          <th>القطعة</th><th>الدستة</th><th>الكرتونة</th><th>الحالة</th>
        </tr></thead>
        <tbody>${data.items.map((i) => `
          <tr class="clickable${p.selected.has(i.id) ? ' selected' : ''}" data-id="${i.id}">
            <td><input type="checkbox" class="sel" data-id="${i.id}" aria-label="اختيار ${esc(i.name)}" ${p.selected.has(i.id) ? 'checked' : ''}></td>
            <td class="hide-sm">${i.image_url ? `<img class="thumb" src="${esc(i.image_url)}" alt="" loading="lazy">` : `<span class="thumb no-img">${esc(i.name.trim().charAt(0))}</span>`}</td>
            <td class="name-cell"><b>${esc(i.name)}</b><span class="meta">${[i.code && 'كود ' + esc(i.code), i.section_name && esc(i.section_name), i.has_custom_price && 'سعر خاص'].filter(Boolean).join(' · ')}</span></td>
            <td><div class="supplier-tags">${i.suppliers.length ? i.suppliers.map((s) => `<span class="badge gray">${esc(s.alias)}</span>`).join('') : '<span class="badge red">مفيش مورد</span>'}${i.source_mode === 'manual' ? '<span class="badge pink">يدوي</span>' : ''}</div></td>
            ${UNITS.map(([u]) => priceCell(i.units[u], u)).join('')}
            <td>${statusBadge(i)}${i.warnings.length ? ` <span class="badge amber" title="${esc(i.warnings.join('\n'))}">تحذير</span>` : ''}</td>
          </tr>`).join('')}
        </tbody></table></div>
      ${data.pages > 1 ? `<div class="pager"><button class="btn sm" id="pg-prev" ${data.page <= 1 ? 'disabled' : ''}>السابق</button><span class="muted">صفحة ${data.page.toLocaleString('ar-EG-u-nu-latn')} من ${data.pages.toLocaleString('ar-EG-u-nu-latn')} (${data.total.toLocaleString('ar-EG-u-nu-latn')} صنف)</span><button class="btn sm" id="pg-next" ${data.page >= data.pages ? 'disabled' : ''}>التالي</button></div>` : ''}
      <div id="bulk"></div>`;
    box.querySelectorAll('tr.clickable').forEach((tr) => tr.addEventListener('click', (e) => { if (!e.target.closest('input')) openProduct(Number(tr.dataset.id)); }));
    box.querySelectorAll('.sel').forEach((cb) => cb.addEventListener('change', () => { const id = Number(cb.dataset.id); if (cb.checked) p.selected.add(id); else p.selected.delete(id); drawProductTable(); }));
    box.querySelector('#sel-all').addEventListener('change', (e) => { data.items.forEach((i) => (e.target.checked ? p.selected.add(i.id) : p.selected.delete(i.id))); drawProductTable(); });
    const prev = box.querySelector('#pg-prev');
    if (prev) { prev.onclick = () => { p.page -= 1; loadProducts(); }; box.querySelector('#pg-next').onclick = () => { p.page += 1; loadProducts(); }; }
    drawBulkBar();
  }

  function statusBadge(i) {
    if (!i.is_visible) return '<span class="badge gray">مخفي</span>';
    if (!i.suppliers.length) return '<span class="badge red">مستخبي: مفيش مورد</span>';
    if (!i.available) return '<span class="badge amber">مش متاح</span>';
    return '<span class="badge green">ظاهر</span>';
  }

  function drawBulkBar() {
    const p = state.products;
    const box = view.querySelector('#bulk');
    if (!p.selected.size) { box.innerHTML = ''; return; }
    box.innerHTML = `
      <div class="bulkbar">
        <b>${p.selected.size.toLocaleString('ar-EG-u-nu-latn')} صنف مختار</b>
        <button class="btn sm" data-a="show">إظهار للتاجر</button>
        <button class="btn sm" data-a="hide">إخفاء</button>
        <button class="btn sm" data-a="cheapest">الأرخص تلقائي</button>
        <select id="bulk-section" aria-label="نقل لقسم">${options(sectionsList().map((s) => ({ value: s.name, label: s.name })), '', 'نقل لقسم…')}</select>
        <span class="spacer"></span>
        <button class="btn sm" data-a="clear">إلغاء الاختيار</button>
      </div>`;
    const send = (data, btn) => run(btn, async () => {
      const r = await api('POST', `${API}/products/bulk`, { product_ids: [...p.selected], ...data });
      toast(`اتعدّل ${r.updated.toLocaleString('ar-EG-u-nu-latn')} صنف`);
      p.selected.clear();
      loadProducts();
    });
    box.querySelectorAll('[data-a]').forEach((b) => (b.onclick = () => {
      const a = b.dataset.a;
      if (a === 'clear') { p.selected.clear(); drawProductTable(); }
      if (a === 'show') send({ is_visible: true }, b);
      if (a === 'hide') send({ is_visible: false }, b);
      if (a === 'cheapest') send({ source_mode: 'cheapest' }, b);
    }));
    box.querySelector('#bulk-section').onchange = (e) => { if (e.target.value) send({ section: e.target.value }, e.target); };
  }

  // ------------------------------------------------------- product drawer
  function openDrawer(html) {
    drawerPanel.innerHTML = html;
    drawer.hidden = false;
    document.body.style.overflow = 'hidden';
    const close = drawerPanel.querySelector('[data-close]');
    if (close) { close.onclick = closeDrawer; close.focus(); }
  }
  function closeDrawer() {
    drawer.hidden = true;
    document.body.style.overflow = '';
    if (state.view === 'products') loadProducts();
    else if (state.view === 'orders') { loadOrders(); loadSummary().catch(() => {}); }
  }
  drawer.addEventListener('click', (e) => { if (e.target === drawer) closeDrawer(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !drawer.hidden) closeDrawer(); });

  async function openProduct(id) {
    openDrawer('<div class="drawer-head"><button class="btn" data-close>رجوع</button><b>بيحمّل…</b></div>');
    const d = await run(null, () => api('GET', `${API}/products/${id}`));
    if (d) drawProduct(d);
  }

  function drawProduct(d) {
    const activeOffers = d.offers.filter((o) => o.is_active);
    const defaultPricing = d.pricing.find((x) => x.tier_id === (tiersList().find((t) => t.is_default) || {}).id) || d.pricing[0];
    const chosenOffer = (unit) => (defaultPricing.units[unit] || {}).supplier_id;
    const warnings = [...new Set(d.pricing.flatMap((p) => p.warnings))];
    openDrawer(`
      <div class="drawer-head">
        <button class="btn" data-close>رجوع</button>
        <div style="flex:1;min-width:0"><b style="font-size:17px">${esc(d.name)}</b><div class="small muted">${d.created_by === 'manual' ? 'اتضاف يدوي' : 'من شيت مورد'} · آخر تعديل ${dateText(d.updated_at)}</div></div>
        <label class="switch"><input type="checkbox" id="d-visible" ${d.is_visible ? 'checked' : ''}><span class="track"></span><span>ظاهر للتاجر</span></label>
      </div>
      <div class="drawer-body">
        ${warnings.length ? `<div class="alert warn"><b>تحذيرات</b><ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
        ${!activeOffers.length ? '<div class="alert bad">الصنف ده مستخبي من عند التاجر لأن مفيش مورد عنده دلوقتي. هيرجع لوحده أول ما يتوفر في شيت مورد.</div>' : ''}

        <section class="card">
          <h3>بيانات الصنف</h3>
          <div class="grid-2">
            <label class="field"><span>الاسم</span><input type="text" id="d-name" value="${esc(d.name)}" maxlength="200"></label>
            <label class="field"><span>الكود (اختياري)</span><input type="text" id="d-code" value="${esc(d.code)}" maxlength="80"></label>
            <label class="field"><span>القسم</span><input type="text" id="d-section" value="${esc(d.section_name || '')}" list="sections-list" maxlength="80"></label>
            <div class="row">
              <label class="field" style="flex:1"><span>قطع الدستة</span><input type="number" id="d-dozen" min="1" value="${esc(d.pieces_per_dozen || '')}" placeholder="12"></label>
              <label class="field" style="flex:1"><span>قطع الكرتونة</span><input type="number" id="d-carton" min="1" value="${esc(d.pieces_per_carton || '')}"></label>
            </div>
          </div>
          <datalist id="sections-list">${sectionsList().map((s) => `<option value="${esc(s.name)}">`).join('')}</datalist>
          <div class="row" style="margin-top:12px">
            <div class="row">${d.image_url ? `<img class="thumb" src="${esc(d.image_url)}" alt="">` : ''}<label class="btn sm">${d.image_url ? 'تغيير الصورة' : 'إضافة صورة'}<input type="file" id="d-image" accept="image/*" hidden></label></div>
            <span class="spacer"></span><button class="btn primary" id="d-save-info">حفظ البيانات</button>
          </div>
        </section>

        <section class="card">
          <h3>الموردين وأسعار الشراء</h3>
          <div class="row" style="margin-bottom:10px">
            <label class="check"><input type="radio" name="d-mode" value="cheapest" ${d.source_mode === 'cheapest' ? 'checked' : ''}> الأرخص تلقائي (لكل وحدة لوحدها)</label>
            <label class="check"><input type="radio" name="d-mode" value="manual" ${d.source_mode === 'manual' ? 'checked' : ''}> يدوي</label>
            <select id="d-manual" style="width:auto;min-width:160px" ${d.source_mode === 'manual' ? '' : 'hidden'}>${options(activeOffers.map((o) => ({ value: o.supplier_id, label: o.supplier_alias })), d.manual_supplier_id, 'اختار المورد')}</select>
          </div>
          <div class="table-wrap"><table>
            <thead><tr><th>المورد</th><th>الاسم عنده</th><th>قطعة</th><th>دستة</th><th>كرتونة</th><th></th></tr></thead>
            <tbody>${d.offers.map((o) => `
              <tr${o.is_active ? '' : ' class="muted"'}>
                <td><b>${esc(o.supplier_alias)}</b>${o.is_active ? '' : ' <span class="badge gray">مش في آخر شيت</span>'}</td>
                <td class="small">${esc(o.supplier_name)}${o.supplier_code ? `<br><span class="muted">كود ${esc(o.supplier_code)}</span>` : ''}</td>
                ${UNITS.map(([u]) => `<td class="num"><span class="${o.is_active && chosenOffer(u) === o.supplier_id ? 'chosen' : ''}" style="padding:2px 6px">${money(o['price_' + u])}</span></td>`).join('')}
                <td>${d.offers.length > 1 ? `<button class="btn sm ghost" data-split="${o.id}" title="لو اتربط غلط">فصل</button>` : ''}</td>
              </tr>`).join('')}</tbody>
          </table></div>
          <p class="small muted">السعر اللي عليه إطار أخضر هو اللي بيتحسب منه سعر البيع دلوقتي.</p>
          <details class="block" style="margin-top:8px"><summary>إضافة أو تعديل سعر مورد يدوي</summary><div class="stack">
            <div class="row">
              <select id="o-supplier" style="flex:1">${options(suppliersList().filter((s) => s.is_active).map((s) => ({ value: s.id, label: s.alias })), '', 'اختار المورد')}</select>
              ${UNITS.map(([u, l]) => `<input type="number" step="0.01" min="0" id="o-${u}" placeholder="شراء ${l}" style="flex:1;min-width:110px">`).join('')}
              <button class="btn" id="o-save">حفظ</button>
            </div>
          </div></details>
          <details class="block"><summary>الصنف ده هو نفس صنف تاني؟ اربطهم</summary><div class="stack">
            <p class="small muted">الصنف ده هيتشال، وموردينه هيتنقلوا للصنف التاني. التاجر هيشوف صنف واحد.</p>
            <div class="row"><input type="search" id="m-q" placeholder="دوّر على الصنف التاني" style="flex:1"><button class="btn" id="m-find">دوّر</button></div>
            <div id="m-results" class="choice-list"></div>
          </div></details>
        </section>

        <section class="card">
          <h3>التسعير</h3>
          <div class="grid-2">
            <label class="field"><span>هامش خاص للصنف ده ٪ (فاضي = هامش الفئة)</span><input type="number" step="0.1" id="d-margin" value="${d.margin_percent == null ? '' : esc(d.margin_percent)}" placeholder="هامش الفئة"></label>
            <div></div>
            ${UNITS.map(([u, l]) => `<label class="field"><span>سعر بيع ثابت لل${l} (اختياري)</span><input type="number" step="0.5" min="0" id="d-fixed-${u}" value="${d['fixed_price_' + u] == null ? '' : esc(d['fixed_price_' + u])}" placeholder="محسوب بالهامش"></label>`).join('')}
          </div>
          <div class="row" style="margin-top:10px"><span class="small muted">ترتيب الحساب: سعر الفئة الثابت، بعده سعر الصنف الثابت، بعده هامش الصنف، بعده هامش الفئة. وبعدها التقريب لفوق لأقرب نص جنيه.</span><span class="spacer"></span><button class="btn primary" id="d-save-price">حفظ التسعير</button></div>
        </section>

        <section class="card">
          <h3>السعر لكل فئة</h3>
          <div class="table-wrap"><table>
            <thead><tr><th>الفئة</th>${UNITS.map(([, l]) => `<th>${l}</th>`).join('')}<th></th></tr></thead>
            <tbody>${d.pricing.map((p) => `
              <tr data-tier="${p.tier_id}">
                <td><b>${esc(p.tier_name)}</b></td>
                ${UNITS.map(([u]) => { const i = p.units[u]; const fixed = ((d.tier_prices[String(p.tier_id)] || {})[u]); return `<td>${i ? `<b class="num${i.below_cost ? ' up' : ''}">${money(i.price)}</b><div class="rule">${RULES[i.rule] || ''}${i.per_piece != null && u !== 'piece' ? ` · القطعة ${money(i.per_piece)}` : ''}${i.saving_percent ? ` · <span class="save">وفّر ${i.saving_percent}٪</span>` : ''}</div>` : '<span class="muted">—</span>'}<input type="number" step="0.5" min="0" class="tp" data-unit="${u}" value="${fixed == null ? '' : esc(fixed)}" placeholder="سعر ثابت" style="margin-top:4px;min-height:36px" aria-label="سعر ثابت للفئة"></td>`; }).join('')}
                <td><button class="btn sm" data-tp="${p.tier_id}">حفظ</button></td>
              </tr>`).join('')}</tbody>
          </table></div>
          <p class="small muted">السعر الثابت للفئة بيكسب أي هامش. سيبه فاضي علشان يتحسب بالهامش.</p>
        </section>

        ${d.history.length ? `<section class="card"><h3>آخر تغييرات أسعار الشراء</h3>
          <div class="table-wrap"><table><thead><tr><th>التاريخ</th><th>المورد</th><th>الوحدة</th><th>قبل</th><th>بعد</th></tr></thead>
          <tbody>${d.history.map((h) => `<tr><td class="small">${dateText(h.changed_at)}</td><td>${esc(h.supplier_alias)}</td><td>${esc((UNITS.find(([u]) => u === h.unit) || [, ''])[1])}</td><td class="num old">${money(h.old_price)}</td><td class="num ${Number(h.new_price) > Number(h.old_price) ? 'up' : 'down'}">${money(h.new_price)}</td></tr>`).join('')}</tbody></table></div></section>` : ''}
      </div>`);

    const P = drawerPanel;
    const patch = (body, btn) => run(btn, async () => { const r = await api('PATCH', `${API}/products/${d.id}`, body); toast('اتحفظ'); drawProduct(r); });
    P.querySelector('#d-visible').onchange = (e) => patch({ is_visible: e.target.checked }, e.target);
    P.querySelector('#d-save-info').onclick = (e) => patch({
      name: P.querySelector('#d-name').value,
      code: P.querySelector('#d-code').value,
      section: P.querySelector('#d-section').value,
      pieces_per_dozen: numOrNull(P.querySelector('#d-dozen').value),
      pieces_per_carton: numOrNull(P.querySelector('#d-carton').value),
    }, e.target);
    P.querySelectorAll('[name=d-mode]').forEach((r) => (r.onchange = () => {
      const manual = r.value === 'manual';
      P.querySelector('#d-manual').hidden = !manual;
      if (!manual) patch({ source_mode: 'cheapest' }, r);
    }));
    P.querySelector('#d-manual').onchange = (e) => { if (e.target.value) patch({ source_mode: 'manual', manual_supplier_id: Number(e.target.value) }, e.target); };
    P.querySelector('#d-save-price').onclick = (e) => {
      const body = { margin_percent: numOrNull(P.querySelector('#d-margin').value) };
      UNITS.forEach(([u]) => { body['fixed_price_' + u] = numOrNull(P.querySelector('#d-fixed-' + u).value); });
      patch(body, e.target);
    };
    P.querySelectorAll('[data-tp]').forEach((b) => (b.onclick = () => run(b, async () => {
      const tr = b.closest('tr');
      const body = { tier_id: Number(b.dataset.tp) };
      tr.querySelectorAll('.tp').forEach((i) => { body[i.dataset.unit] = numOrNull(i.value); });
      const r = await api('PUT', `${API}/products/${d.id}/tier-prices`, body);
      toast('اتحفظ سعر الفئة');
      drawProduct(r);
    })));
    P.querySelectorAll('[data-split]').forEach((b) => (b.onclick = () => {
      if (!confirm('هيتعمل صنف جديد لسعر المورد ده لوحده. تكمّل؟')) return;
      run(b, async () => { await api('POST', `${API}/offers/${b.dataset.split}/split`); toast('اتفصل'); openProduct(d.id); });
    }));
    P.querySelector('#o-save').onclick = (e) => run(e.target, async () => {
      const supplier = P.querySelector('#o-supplier').value;
      if (!supplier) throw new Error('اختار المورد');
      const body = { supplier_id: Number(supplier) };
      UNITS.forEach(([u]) => { body['price_' + u] = numOrNull(P.querySelector('#o-' + u).value); });
      const r = await api('PUT', `${API}/products/${d.id}/offers`, body);
      toast('اتحفظ سعر المورد');
      drawProduct(r);
    });
    const find = () => run(P.querySelector('#m-find'), async () => {
      const q = P.querySelector('#m-q').value.trim();
      if (!q) return;
      const r = await api('GET', `${API}/products?${qs({ q })}`);
      const items = r.items.filter((i) => i.id !== d.id).slice(0, 8);
      P.querySelector('#m-results').innerHTML = items.length ? items.map((i) => `<div class="row" style="border-bottom:1px solid var(--line);padding:6px 0"><span style="flex:1">${esc(i.name)} <span class="small muted">${i.suppliers.map((s) => esc(s.alias)).join('، ')}</span></span><button class="btn sm" data-merge="${i.id}">اربط بيه</button></div>`).join('') : '<p class="muted small">مفيش نتايج.</p>';
      P.querySelectorAll('[data-merge]').forEach((b) => (b.onclick = () => {
        if (!confirm('الصنفين هيبقوا صنف واحد. تكمّل؟')) return;
        run(b, async () => { const m = await api('POST', `${API}/products/${d.id}/merge`, { into_product_id: Number(b.dataset.merge) }); toast('اتربطوا'); drawProduct(m); });
      }));
    });
    P.querySelector('#m-find').onclick = find;
    P.querySelector('#m-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') find(); });
    P.querySelector('#d-image').onchange = (e) => {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 4 * 1024 * 1024) { toast('الصورة أكبر من 4 ميجا', true); return; }
      const reader = new FileReader();
      reader.onload = () => run(null, async () => { const r = await api('POST', `${API}/products/${d.id}/image`, { image: reader.result }); toast('اتحفظت الصورة'); drawProduct(r); });
      reader.readAsDataURL(file);
    };
  }

  function openManualProduct() {
    const suppliers = suppliersList().filter((s) => s.is_active);
    openDrawer(`
      <div class="drawer-head"><button class="btn" data-close>رجوع</button><b style="font-size:17px">إضافة صنف يدوي</b></div>
      <div class="drawer-body">
        ${suppliers.length ? '' : '<div class="alert warn">لازم تضيف مورد الأول من صفحة الموردين. لو الصنف عندك في المخزن، اعمل مورد باسم زي «مخزن روز».</div>'}
        <section class="card stack">
          <label class="field"><span>اسم الصنف</span><input type="text" id="n-name" maxlength="200"></label>
          <div class="grid-2">
            <label class="field"><span>الكود (اختياري)</span><input type="text" id="n-code" maxlength="80"></label>
            <label class="field"><span>القسم</span><input type="text" id="n-section" list="n-sections" maxlength="80"></label>
          </div>
          <datalist id="n-sections">${sectionsList().map((s) => `<option value="${esc(s.name)}">`).join('')}</datalist>
          <label class="field"><span>المورد</span><select id="n-supplier">${options(suppliers.map((s) => ({ value: s.id, label: s.alias })), '', 'اختار المورد')}</select></label>
          <div class="grid-2">
            ${UNITS.map(([u, l]) => `<label class="field"><span>سعر شراء ال${l}</span><input type="number" step="0.01" min="0" id="n-${u}" data-u="${u}" class="n-price"></label>`).join('')}
            <label class="field"><span>قطع الدستة</span><input type="number" min="1" id="n-dozen" placeholder="12"></label>
            <label class="field"><span>قطع الكرتونة</span><input type="number" min="1" id="n-carton"></label>
            <label class="field"><span>هامش خاص ٪ (اختياري)</span><input type="number" step="0.1" id="n-margin" placeholder="هامش الفئة"></label>
          </div>
          <label class="switch"><input type="checkbox" id="n-visible" checked><span class="track"></span><span>يظهر للتاجر</span></label>
          <div class="alert info" id="n-preview">اكتب سعر الشراء علشان تشوف سعر البيع.</div>
          <div class="row"><span class="spacer"></span><button class="btn primary" id="n-save">إضافة الصنف</button></div>
        </section>
      </div>`);
    const P = drawerPanel;
    const defTier = tiersList().find((t) => t.is_default) || {};
    const margin = defTier.margin_percent != null ? Number(defTier.margin_percent) : Number(state.summary.settings.global_margin_percent || 0);
    const preview = () => {
      const m = P.querySelector('#n-margin').value === '' ? margin : Number(P.querySelector('#n-margin').value);
      const parts = UNITS.map(([u, l]) => {
        const cost = numOrNull(P.querySelector('#n-' + u).value);
        if (cost == null) return '';
        const price = Math.ceil(Math.round(cost * (1 + m / 100) * 2 * 10000) / 10000) / 2;
        const pieces = u === 'piece' ? 1 : u === 'dozen' ? (numOrNull(P.querySelector('#n-dozen').value) || 12) : numOrNull(P.querySelector('#n-carton').value);
        return `<div><b>${l}: ${money(price)} ج</b>${u !== 'piece' && pieces ? ` <span class="small">(القطعة ${money(price / pieces)})</span>` : ''}</div>`;
      }).filter(Boolean);
      P.querySelector('#n-preview').innerHTML = parts.length ? `<div class="small">شكل السعر عند التاجر في الفئة الافتراضية (هامش ${pct(m)}):</div>${parts.join('')}` : 'اكتب سعر الشراء علشان تشوف سعر البيع.';
    };
    P.querySelectorAll('input').forEach((i) => i.addEventListener('input', preview));
    P.querySelector('#n-save').onclick = (e) => run(e.target, async () => {
      const body = {
        name: P.querySelector('#n-name').value.trim(),
        code: P.querySelector('#n-code').value.trim(),
        section: P.querySelector('#n-section').value.trim(),
        supplier_id: Number(P.querySelector('#n-supplier').value || 0),
        pieces_per_dozen: numOrNull(P.querySelector('#n-dozen').value),
        pieces_per_carton: numOrNull(P.querySelector('#n-carton').value),
        margin_percent: numOrNull(P.querySelector('#n-margin').value),
        is_visible: P.querySelector('#n-visible').checked,
      };
      UNITS.forEach(([u]) => { body['price_' + u] = numOrNull(P.querySelector('#n-' + u).value); });
      if (!body.name) throw new Error('اكتب اسم الصنف');
      if (!body.supplier_id) throw new Error('اختار المورد');
      const r = await api('POST', `${API}/products`, body);
      toast('الصنف اتضاف');
      await loadSummary();
      drawProduct(r);
    });
  }

  // -------------------------------------------------------------- import
  function renderImport() {
    const suppliers = suppliersList().filter((s) => s.is_active);
    state.imp = null;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>رفع شيت مورد</h2><p>أسعار الشيت هي أسعار الشراء من المورد. سعر البيع بيتحسب بعدها بالهامش.</p></div></div>
      <div class="steps" id="steps"><span class="on">١. المورد والملف</span><span>٢. ربط الأعمدة</span><span>٣. المعاينة والحفظ</span></div>
      <div id="imp-body">
        ${suppliers.length ? `
        <div class="card stack" style="max-width:720px">
          <label class="field"><span>المورد</span><select id="i-supplier">${options(suppliers.map((s) => ({ value: s.id, label: s.alias + (s.has_template ? ' (عنده ربط محفوظ)' : '') })), '', 'اختار المورد')}</select></label>
          <label class="dropzone" id="drop"><b>اسحب الشيت هنا أو دوس لاختياره</b><span class="small muted">xlsx أو csv، لحد 10 ميجا</span><input type="file" id="i-file" accept=".xlsx,.xlsm,.csv" hidden></label>
        </div>` : '<div class="card empty">لازم تضيف مورد الأول. <button class="btn primary" id="to-suppliers">إضافة مورد</button></div>'}
      </div>
      <h3 style="margin:28px 0 10px">آخر الشيتات اللي اتحفظت</h3>
      <div id="imp-history"><p class="muted">بيحمّل…</p></div>`;
    const to = view.querySelector('#to-suppliers');
    if (to) to.onclick = () => go('suppliers');
    const file = view.querySelector('#i-file');
    if (file) {
      const drop = view.querySelector('#drop');
      file.onchange = () => file.files[0] && upload(file.files[0]);
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
      drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('drag'); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });
    }
    loadImportHistory();
  }

  async function loadImportHistory() {
    const box = view.querySelector('#imp-history');
    if (!box) return;
    const list = await run(null, () => api('GET', `${API}/imports`));
    if (!list) return;
    box.innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>التاريخ</th><th>المورد</th><th>الملف</th><th>جديد</th><th>اتغيّر سعره</th><th>اتشال</th><th>مشاكل</th></tr></thead><tbody>${list.map((i) => `<tr><td class="small">${dateText(i.applied_at)}</td><td>${esc(i.supplier_alias)}</td><td class="small">${esc(i.filename)}</td><td>${(i.summary.new || 0).toLocaleString('ar-EG-u-nu-latn')}</td><td>${(i.summary.changed || 0).toLocaleString('ar-EG-u-nu-latn')}</td><td>${(i.summary.removed || 0).toLocaleString('ar-EG-u-nu-latn')}</td><td>${(i.summary.problems || 0).toLocaleString('ar-EG-u-nu-latn')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">لسه مفيش.</p>';
  }

  function setStep(n) {
    view.querySelectorAll('#steps span').forEach((s, i) => { s.className = i + 1 < n ? 'done' : i + 1 === n ? 'on' : ''; });
  }

  function upload(file) {
    const supplier = view.querySelector('#i-supplier').value;
    if (!supplier) { toast('اختار المورد الأول', true); return; }
    if (file.size > 10 * 1024 * 1024) { toast('الملف أكبر من 10 ميجا', true); return; }
    const reader = new FileReader();
    reader.onload = () => run(null, async () => {
      view.querySelector('#imp-body').innerHTML = '<p class="empty">بيقرا الشيت…</p>';
      try {
        state.imp = await api('POST', `${API}/imports`, { supplier_id: Number(supplier), filename: file.name, content_base64: reader.result });
      } catch (err) { renderImport(); throw err; }
      drawMapping();
    });
    reader.readAsDataURL(file);
  }

  function drawMapping() {
    const m = state.imp;
    setStep(2);
    const colOptions = m.headers.map((h, i) => ({ value: i, label: `${h}` }));
    const mappedCols = new Set(Object.values(m.mapping).filter((v) => v != null).map(Number));
    const sampleFor = (idx) => (idx == null || idx === '' ? '' : m.sample.slice(0, 3).map((r) => r[idx]).filter(Boolean).join(' · '));
    view.querySelector('#imp-body').innerHTML = `
      <div class="card stack">
        <div class="row">
          <div style="flex:1"><b>${esc(m.supplier.alias)}</b> · <span class="muted">${esc(m.filename)}</span></div>
          ${m.from_template ? '<span class="badge green">اتطبّق الربط المحفوظ للمورد ده</span>' : '<span class="badge amber">أول مرة: راجع الربط</span>'}
        </div>
        <div class="row">
          ${m.sheets.length > 1 ? `<label class="field" style="flex:1"><span>الورقة</span><select id="m-sheet">${options(m.sheets.map((s) => ({ value: s.index, label: `${s.name} (${s.rows} صف)` })), m.sheet_index)}</select></label>` : ''}
          <label class="field" style="flex:1"><span>صف العناوين</span><select id="m-header">${options(m.top_rows.map((r, i) => ({ value: i + 1, label: `صف ${i + 1}: ${r.filter(Boolean).slice(0, 4).join(' | ').slice(0, 60)}` })), m.header_row)}</select></label>
          <div class="muted small" style="flex:1">${m.data_rows.toLocaleString('ar-EG-u-nu-latn')} صف بيانات تحت العناوين</div>
        </div>
      </div>
      <div class="card" style="margin-top:16px">
        <h3 style="margin-bottom:6px">اختار لكل خانة العمود اللي يقابلها</h3>
        <p class="small muted" style="margin:0 0 12px">الاسم وسعر واحد على الأقل لازمين. أي خانة مش في الشيت سيبها «مش موجود» وتقدر تكتبها يدوي بعدين.</p>
        <div class="map-grid">${m.fields.map((f) => `
          <label class="field"><span>${esc(f.label)}${f.key === 'name' ? ' *' : ''}</span>
            <select data-field="${f.key}">${options(colOptions, m.mapping[f.key], 'مش موجود')}</select>
            <span class="sample" data-sample="${f.key}">${esc(sampleFor(m.mapping[f.key]))}</span>
          </label>`).join('')}
        </div>
      </div>
      <div class="table-wrap sheet-preview" style="margin-top:16px"><table>
        <thead><tr><th class="rownum">#</th>${m.headers.map((h, i) => `<th class="${mappedCols.has(i) ? 'mapped' : ''}">${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${m.sample.map((r, ri) => `<tr><td class="rownum">${(m.header_row + ri + 1).toLocaleString('ar-EG-u-nu-latn')}</td>${m.headers.map((_, i) => `<td>${esc(r[i] || '')}</td>`).join('')}</tr>`).join('')}</tbody>
      </table></div>
      <div class="row" style="margin-top:16px"><button class="btn" id="m-cancel">إلغاء</button><span class="spacer"></span><button class="btn primary" id="m-preview">عاين التغييرات</button></div>`;
    const body = view.querySelector('#imp-body');
    body.querySelectorAll('[data-field]').forEach((s) => (s.onchange = () => {
      m.mapping[s.dataset.field] = s.value === '' ? null : Number(s.value);
      body.querySelector(`[data-sample="${s.dataset.field}"]`).textContent = sampleFor(m.mapping[s.dataset.field]);
      const used = new Set(Object.values(m.mapping).filter((v) => v != null));
      body.querySelectorAll('.sheet-preview th').forEach((th, i) => { if (i) th.className = used.has(i - 1) ? 'mapped' : ''; });
    }));
    const reload = () => run(null, async () => {
      const sheet = body.querySelector('#m-sheet');
      const r = await api('POST', `${API}/imports/${m.import_id}/columns`, { sheet_index: sheet ? Number(sheet.value) : m.sheet_index, header_row: Number(body.querySelector('#m-header').value) });
      Object.assign(m, r);
      drawMapping();
    });
    body.querySelector('#m-header').onchange = reload;
    if (body.querySelector('#m-sheet')) body.querySelector('#m-sheet').onchange = () => { body.querySelector('#m-header').value = '1'; reload(); };
    body.querySelector('#m-cancel').onclick = () => run(null, async () => { await api('POST', `${API}/imports/${m.import_id}/cancel`); renderImport(); });
    body.querySelector('#m-preview').onclick = (e) => run(e.target, async () => {
      const preview = await api('POST', `${API}/imports/${m.import_id}/preview`, { sheet_index: m.sheet_index, header_row: m.header_row, mapping: m.mapping });
      m.preview = preview;
      m.decisions = {};
      drawPreview();
    });
  }

  function drawPreview() {
    const m = state.imp;
    const p = m.preview;
    const s = p.summary;
    setStep(3);
    const confirmRows = p.new.filter((n) => n.suggestions.length && !n.auto_product_id);
    const autoRows = p.new.filter((n) => n.auto_product_id);
    const plainNew = p.new.filter((n) => !n.suggestions.length && !n.auto_product_id);
    const priceList = (n) => UNITS.map(([u, l]) => (n['price_' + u] != null ? `${l} ${money(n['price_' + u])}` : '')).filter(Boolean).join(' · ');
    const limit = (list, fn, n = 300) => list.slice(0, n).map(fn).join('') + (list.length > n ? `<p class="muted small">و${(list.length - n).toLocaleString('ar-EG-u-nu-latn')} كمان…</p>` : '');
    view.querySelector('#imp-body').innerHTML = `
      <div class="stats">
        <div class="stat"><b>${s.rows.toLocaleString('ar-EG-u-nu-latn')}</b><span>صف في الشيت</span></div>
        <div class="stat good"><b>${s.new.toLocaleString('ar-EG-u-nu-latn')}</b><span>صنف جديد</span></div>
        <div class="stat warn"><b>${s.changed.toLocaleString('ar-EG-u-nu-latn')}</b><span>سعره اتغيّر</span></div>
        <div class="stat"><b>${s.unchanged.toLocaleString('ar-EG-u-nu-latn')}</b><span>زي ما هو</span></div>
        <div class="stat bad"><b>${s.removed.toLocaleString('ar-EG-u-nu-latn')}</b><span>مش موجود في الشيت</span></div>
        <div class="stat bad"><b>${s.problems.toLocaleString('ar-EG-u-nu-latn')}</b><span>مشاكل (مش هتتحفظ)</span></div>
      </div>
      ${p.warnings.map((w) => `<div class="alert warn" style="margin-bottom:10px">${esc(w)}</div>`).join('')}

      ${confirmRows.length ? `<details class="block" open><summary>محتاج تأكيد: نفس الصنف عند مورد تاني؟ <span class="badge amber">${confirmRows.length.toLocaleString('ar-EG-u-nu-latn')}</span></summary><div>
        <p class="small muted">لو نفس الصنف، التاجر هيشوفه صنف واحد والسعر بيتحسب من المورد المناسب. لو مش متأكد، خليه صنف جديد وتقدر تربطهم بعدين.</p>
        <div class="table-wrap"><table><thead><tr><th>في الشيت</th><th>اختيارك</th></tr></thead><tbody>
        ${limit(confirmRows, (n) => `<tr><td><b>${esc(n.name)}</b><div class="small muted">صف ${n.row.toLocaleString('ar-EG-u-nu-latn')} · ${priceList(n)}</div></td><td class="choice-list">
          ${n.suggestions.map((sg) => `<label class="check"><input type="radio" name="dc-${n.row}" value="${sg.product_id}" ${n.default_choice === sg.product_id ? 'checked' : ''}> نفس «${esc(sg.name)}» <span class="small muted">(تشابه ${sg.score}٪)</span></label>`).join('')}
          <label class="check"><input type="radio" name="dc-${n.row}" value="0" ${n.default_choice ? '' : 'checked'}> صنف جديد</label></td></tr>`)}
        </tbody></table></div></div></details>` : ''}

      ${autoRows.length ? `<details class="block"><summary>هيتربطوا لوحدهم بالكود <span class="badge green">${autoRows.length.toLocaleString('ar-EG-u-nu-latn')}</span></summary><div>
        ${limit(autoRows, (n) => `<div class="small" style="padding:4px 0;border-bottom:1px solid var(--line)"><b>${esc(n.name)}</b> · كود ${esc(n.code)} · ${esc(n.auto_reason)} · ${priceList(n)}</div>`)}</div></details>` : ''}

      ${plainNew.length ? `<details class="block"><summary>أصناف جديدة <span class="badge green">${plainNew.length.toLocaleString('ar-EG-u-nu-latn')}</span></summary><div>
        ${limit(plainNew, (n) => `<div class="small" style="padding:4px 0;border-bottom:1px solid var(--line)"><b>${esc(n.name)}</b>${n.code ? ` · كود ${esc(n.code)}` : ''}${n.section ? ` · ${esc(n.section)}` : ''} · ${priceList(n)}</div>`)}</div></details>` : ''}

      ${p.changed.length ? `<details class="block"><summary>أسعار اتغيّرت <span class="badge amber">${p.changed.length.toLocaleString('ar-EG-u-nu-latn')}</span></summary><div>
        <div class="table-wrap"><table><thead><tr><th>الصنف</th><th>الوحدة</th><th>القديم</th><th>الجديد</th></tr></thead><tbody>
        ${limit(p.changed, (c) => c.changes.map((ch, i) => `<tr>${i === 0 ? `<td rowspan="${c.changes.length}"><b>${esc(c.name)}</b></td>` : ''}<td>${esc((UNITS.find(([u]) => u === ch.unit) || [, ''])[1])}</td><td class="num old">${money(ch.old)}</td><td class="num ${Number(ch.new) > Number(ch.old) ? 'up' : 'down'}">${money(ch.new)}</td></tr>`).join(''))}
        </tbody></table></div></div></details>` : ''}

      ${p.removed.length ? `<details class="block"><summary>مش موجودة في الشيت الجديد <span class="badge red">${p.removed.length.toLocaleString('ar-EG-u-nu-latn')}</span></summary><div>
        <p class="small muted">هتتشال من اختيارات المورد ده. اللي مالهاش مورد تاني هتستخبى من عند التاجر لحد ما ترجع.</p>
        ${limit(p.removed, (r) => `<div class="small" style="padding:4px 0;border-bottom:1px solid var(--line)"><b>${esc(r.name)}</b>${r.code ? ` · كود ${esc(r.code)}` : ''} ${r.hides_product ? '<span class="badge red">هيستخبى</span>' : '<span class="badge gray">ليه مورد تاني</span>'}</div>`)}</div></details>` : ''}

      ${p.problems.length ? `<details class="block"><summary>مشاكل <span class="badge red">${p.problems.length.toLocaleString('ar-EG-u-nu-latn')}</span></summary><div>
        <div class="table-wrap"><table><thead><tr><th>صف</th><th>الاسم</th><th>المشكلة</th></tr></thead><tbody>
        ${limit(p.problems, (r) => `<tr><td>${r.row.toLocaleString('ar-EG-u-nu-latn')}</td><td>${esc(r.name || '—')}</td><td>${esc(r.issue)}</td></tr>`)}</tbody></table></div></div></details>` : ''}

      <div class="card row" style="margin-top:16px">
        <label class="check"><input type="checkbox" id="p-template" checked> احفظ ربط الأعمدة ده للمورد «${esc(m.supplier.alias)}» علشان يتطبّق لوحده المرة الجاية</label>
        <span class="spacer"></span>
        <button class="btn" id="p-back">رجوع لربط الأعمدة</button>
        <button class="btn primary" id="p-apply">احفظ التغييرات</button>
      </div>`;
    const body = view.querySelector('#imp-body');
    body.querySelector('#p-back').onclick = drawMapping;
    body.querySelector('#p-apply').onclick = (e) => run(e.target, async () => {
      const decisions = {};
      confirmRows.forEach((n) => { const c = body.querySelector(`[name="dc-${n.row}"]:checked`); decisions[String(n.row)] = c ? Number(c.value) : 0; });
      const r = await api('POST', `${API}/imports/${m.import_id}/apply`, { decisions, save_template: body.querySelector('#p-template').checked });
      const a = r.summary.applied;
      body.innerHTML = `<div class="card stack"><div class="alert good"><b>الشيت اتحفظ.</b></div>
        <div class="stats">
          <div class="stat good"><b>${a.created_products.toLocaleString('ar-EG-u-nu-latn')}</b><span>صنف جديد</span></div>
          <div class="stat"><b>${a.linked.toLocaleString('ar-EG-u-nu-latn')}</b><span>اتربط بصنف موجود</span></div>
          <div class="stat warn"><b>${a.price_changes.toLocaleString('ar-EG-u-nu-latn')}</b><span>تغيير سعر</span></div>
          <div class="stat bad"><b>${a.deactivated.toLocaleString('ar-EG-u-nu-latn')}</b><span>اتشال من المورد</span></div>
          <div class="stat"><b>${a.skipped.toLocaleString('ar-EG-u-nu-latn')}</b><span>صف فيه مشكلة اتساب</span></div>
        </div>
        <div class="row"><button class="btn" id="again">رفع شيت تاني</button><button class="btn primary" id="see">شوف الأصناف</button></div></div>`;
      setStep(4);
      body.querySelector('#again').onclick = renderImport;
      body.querySelector('#see').onclick = () => { state.products.supplier_id = String(m.supplier.id); state.products.status = ''; go('products'); };
      loadImportHistory();
    });
  }

  // ----------------------------------------------------------- suppliers
  function renderSuppliers() {
    const list = suppliersList();
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>الموردين</h2><p>اكتب اسم مستعار، مش اسم المورد الحقيقي. المورد مش بيظهر للتاجر خالص.</p></div></div>
      <div class="card" style="margin-bottom:16px">
        <form id="s-form" class="row">
          <input type="text" id="s-alias" placeholder="اسم مستعار (مثلًا: مورد المنظفات)" maxlength="80" required style="flex:2;min-width:200px">
          <input type="tel" id="s-phone" placeholder="موبايل للإرسال (اختياري)" maxlength="30" style="flex:1;min-width:160px">
          <button class="btn primary">إضافة مورد</button>
        </form>
      </div>
      ${list.length ? `<div class="table-wrap"><table>
        <thead><tr><th>الاسم المستعار</th><th>الموبايل</th><th>أصناف حالية</th><th>آخر شيت</th><th>ربط الأعمدة</th><th>نشط</th><th></th></tr></thead>
        <tbody>${list.map((s) => `
          <tr data-id="${s.id}">
            <td><input type="text" class="s-alias" value="${esc(s.alias)}" maxlength="80" aria-label="الاسم المستعار"></td>
            <td><input type="tel" class="s-phone" value="${esc(s.phone)}" maxlength="30" aria-label="الموبايل"></td>
            <td><a href="#products" data-filter="${s.id}">${s.active_items.toLocaleString('ar-EG-u-nu-latn')}</a></td>
            <td class="small">${dateText(s.last_import_at)}</td>
            <td>${s.has_template ? '<span class="badge green">محفوظ</span> <button class="btn sm ghost" data-reset="1">مسح</button>' : '<span class="badge gray">مفيش</span>'}</td>
            <td><label class="switch"><input type="checkbox" class="s-active" ${s.is_active ? 'checked' : ''} aria-label="نشط"><span class="track"></span></label></td>
            <td><button class="btn sm" data-save="1">حفظ</button></td>
          </tr>`).join('')}</tbody></table></div>
        <p class="small muted">لو وقفت مورد، أسعاره بتتشال من الحساب لحد ما ترجّعه.</p>` : '<div class="card empty">لسه مفيش موردين.</div>'}`;
    view.querySelector('#s-form').onsubmit = (e) => {
      e.preventDefault();
      run(e.submitter, async () => {
        await api('POST', `${API}/suppliers`, { alias: view.querySelector('#s-alias').value, phone: view.querySelector('#s-phone').value });
        toast('المورد اتضاف');
        render();
      });
    };
    view.querySelectorAll('tbody tr').forEach((tr) => {
      const id = tr.dataset.id;
      const save = (btn) => run(btn, async () => {
        await api('PATCH', `${API}/suppliers/${id}`, { alias: tr.querySelector('.s-alias').value, phone: tr.querySelector('.s-phone').value, is_active: tr.querySelector('.s-active').checked });
        toast('اتحفظ');
        await loadSummary();
      });
      tr.querySelector('[data-save]').onclick = (e) => save(e.target);
      tr.querySelector('.s-active').onchange = (e) => save(e.target);
      const reset = tr.querySelector('[data-reset]');
      if (reset) reset.onclick = () => { if (confirm('ربط الأعمدة المحفوظ هيتمسح، والمرة الجاية هتربط من الأول. تكمّل؟')) run(reset, async () => { await api('DELETE', `${API}/suppliers/${id}/template`); render(); }); };
      tr.querySelector('[data-filter]').onclick = (e) => { e.preventDefault(); state.products.supplier_id = id; state.products.status = ''; go('products'); };
    });
  }

  // ------------------------------------------------------------- pricing
  async function renderPricing() {
    const s = state.summary;
    const settings = s.settings;
    const tiers = s.tiers;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>الفئات والتسعير</h2><p>كل تاجر بيشوف أسعار فئته بس، ومايعرفش إن فيه فئات تانية.</p></div></div>
      <div class="grid-2">
        <section class="card stack">
          <h3>الهامش العام</h3>
          <p class="small muted" style="margin:0">نسبة بتتحط على سعر الشراء لكل الأصناف، إلا لو الصنف أو الفئة ليهم هامش خاص.</p>
          <div class="row"><label class="field" style="flex:1"><span>الهامش ٪</span><input type="number" step="0.1" id="g-margin" value="${esc(settings.global_margin_percent)}"></label>
          <button class="btn primary" id="g-save" style="align-self:flex-end">حفظ</button></div>
          <div class="alert info small" id="g-example"></div>
        </section>
        <section class="card stack">
          <h3>اختيار المورد للأصناف الجديدة</h3>
          <label class="check"><input type="radio" name="g-mode" value="cheapest" ${settings.default_source_mode !== 'manual' ? 'checked' : ''}> الأرخص تلقائي</label>
          <label class="check"><input type="radio" name="g-mode" value="manual" ${settings.default_source_mode === 'manual' ? 'checked' : ''}> يدوي (تختار المورد لكل صنف)</label>
          <p class="small muted" style="margin:0">«الأرخص» بيتحسب لكل وحدة لوحدها، وبيتعاد حسابه لوحده مع كل شيت جديد.</p>
        </section>
      </div>

      <section class="card" style="margin-top:16px">
        <div class="row" style="margin-bottom:12px"><h3>فئات العملاء</h3><span class="spacer"></span></div>
        <div class="table-wrap"><table>
          <thead><tr><th>الفئة</th><th>الهامش ٪</th><th>افتراضية</th><th>فئة المشتركين</th><th>التجار</th><th></th></tr></thead>
          <tbody>${tiers.map((t) => `
            <tr data-id="${t.id}">
              <td><input type="text" class="t-name" value="${esc(t.name)}" maxlength="60" aria-label="اسم الفئة"></td>
              <td><input type="number" step="0.1" class="t-margin" value="${t.margin_percent == null ? '' : esc(t.margin_percent)}" placeholder="العام (${esc(settings.global_margin_percent)}٪)" aria-label="الهامش"></td>
              <td><input type="radio" name="t-default" class="t-default" ${t.is_default ? 'checked' : ''} aria-label="افتراضية"></td>
              <td><input type="checkbox" class="t-sub" ${t.is_subscriber ? 'checked' : ''} aria-label="فئة المشتركين"></td>
              <td><a href="#" data-traders="${t.id}">${t.traders.toLocaleString('ar-EG-u-nu-latn')}</a></td>
              <td><div class="row"><button class="btn sm" data-save="1">حفظ</button>${t.is_default ? '' : '<button class="btn sm danger" data-del="1">مسح</button>'}</div></td>
            </tr>`).join('')}</tbody>
        </table></div>
        <form id="t-form" class="row" style="margin-top:12px">
          <input type="text" id="t-new-name" placeholder="اسم فئة جديدة (مثلًا: تاجر كبير)" maxlength="60" required style="flex:2;min-width:200px">
          <input type="number" step="0.1" id="t-new-margin" placeholder="الهامش ٪ (فاضي = العام)" style="flex:1;min-width:150px">
          <button class="btn primary">إضافة فئة</button>
        </form>
        <p class="small muted">الفئة الافتراضية بتتطبّق على أي تاجر مش متحط في فئة. فئة المشتركين هي اللي التاجر بيتنقل لها لما طلب الاشتراك يتقبل؛ هامش صفر يعني بسعر الشيت بالظبط.</p>
      </section>
`;
    const example = () => {
      const m = Number(view.querySelector('#g-margin').value || 0);
      const cost = 102;
      const raw = cost * (1 + m / 100);
      const rounded = Math.ceil(Math.round(raw * 2 * 10000) / 10000) / 2;
      view.querySelector('#g-example').innerHTML = `مثال: سعر شراء ${money(cost)} + ${pct(m)} = ${money(raw)}، وبعد التقريب لفوق لأقرب نص جنيه: <b>${money(rounded)}</b>${rounded < cost ? ' <span class="badge red">أقل من سعر الشراء</span>' : ''}`;
    };
    view.querySelector('#g-margin').addEventListener('input', example);
    example();
    view.querySelector('#g-save').onclick = (e) => run(e.target, async () => {
      await api('PUT', `${API}/settings`, { global_margin_percent: numOrNull(view.querySelector('#g-margin').value) || 0 });
      toast('الهامش العام اتحفظ، والأسعار اتحسبت من جديد');
      render();
    });
    view.querySelectorAll('[name=g-mode]').forEach((r) => (r.onchange = () => run(r, async () => { await api('PUT', `${API}/settings`, { default_source_mode: r.value }); toast('اتحفظ'); })));
    view.querySelectorAll('tbody tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      tr.querySelector('[data-save]').onclick = (e) => run(e.target, async () => {
        await api('PATCH', `${API}/tiers/${id}`, { name: tr.querySelector('.t-name').value, margin_percent: numOrNull(tr.querySelector('.t-margin').value), is_subscriber: tr.querySelector('.t-sub').checked, is_default: tr.querySelector('.t-default').checked || undefined });
        toast('الفئة اتحفظت');
        render();
      });
      tr.querySelector('.t-default').onchange = () => tr.querySelector('[data-save]').click();
      const del = tr.querySelector('[data-del]');
      if (del) del.onclick = () => { if (confirm('تمسح الفئة دي؟')) run(del, async () => { await api('DELETE', `${API}/tiers/${id}`); toast('اتمسحت'); render(); }); };
      tr.querySelector('[data-traders]').onclick = (e) => { e.preventDefault(); state.traders.status = 'approved'; go('traders'); };
    });
    view.querySelector('#t-form').onsubmit = (e) => {
      e.preventDefault();
      run(e.submitter, async () => {
        await api('POST', `${API}/tiers`, { name: view.querySelector('#t-new-name').value, margin_percent: numOrNull(view.querySelector('#t-new-margin').value) });
        toast('الفئة اتضافت');
        render();
      });
    };
  }

  // ============================================================ phase 3
  const UNIT_NAME = Object.fromEntries(UNITS);
  const ORDER_TABS = [['submitted', 'محتاجة مراجعة'], ['reviewed', 'مستنية البضاعة'], ['received', 'مستنية التجهيز'], ['preparing', 'بتتجهز'], ['ready', 'جاهزة للتسليم'], ['out_for_delivery', 'مع المناديب'], ['open', 'كل الشغّالة'], ['delivered', 'اتسلّمت'], ['cancelled', 'اتلغت'], ['', 'الكل']];
  const PO_STATUS = { pending: ['gray', 'لسه ما اتبعتش'], sent: ['amber', 'اتبعت، مستني رد'], confirmed: ['green', 'المورد رد'], received: ['green', 'اتستلم'], cancelled: ['gray', 'اتلغى'] };
  const ORDER_BADGE = { submitted: 'amber', reviewed: 'pink', received: 'pink', preparing: 'amber', ready: 'green', out_for_delivery: 'amber', delivered: 'green', cancelled: 'gray' };

  // -------------------------------------------------------------- orders
  async function renderOrders() {
    const o = state.orders;
    const c = state.counts;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>طلبات الجملة</h2><p>كل طلب بيتقسم لطلب شراء لكل مورد. التاجر بيشوف فاتورة واحدة ومايعرفش الموردين.</p></div></div>
      <div class="stats">
        <div class="stat warn"><b>${(c.orders_to_review || 0).toLocaleString('ar-EG-u-nu-latn')}</b><span>محتاجة مراجعة</span></div>
        <div class="stat"><b>${(c.orders_receiving || 0).toLocaleString('ar-EG-u-nu-latn')}</b><span>مستنية البضاعة</span></div>
        <div class="stat"><b>${(c.waiting_shortages || 0).toLocaleString('ar-EG-u-nu-latn')}</b><span>صنف في النواقص</span></div>
        <div class="stat"><b>${(c.pending_traders || 0).toLocaleString('ar-EG-u-nu-latn')}</b><span>طلب انضمام</span></div>
      </div>
      <div class="card stack">
        <div class="row"><input type="search" id="o-q" placeholder="رقم الطلب أو اسم التاجر أو الموبايل" value="${esc(o.q)}" style="flex:1;min-width:220px"></div>
        <div class="chips">${ORDER_TABS.map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${o.status === k}">${l}</button>`).join('')}</div>
      </div>
      <div id="o-table" style="margin-top:16px"></div>`;
    view.querySelectorAll('.chips .chip').forEach((b) => (b.onclick = () => { o.status = b.dataset.k; o.page = 1; view.querySelectorAll('.chips .chip').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); loadOrders(); }));
    let t;
    view.querySelector('#o-q').addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { o.q = e.target.value.trim(); o.page = 1; loadOrders(); }, 300); });
    loadOrders();
  }

  async function loadOrders() {
    const box = view.querySelector('#o-table');
    if (!box) return;
    const o = state.orders;
    const data = await run(null, () => api('GET', `${API}/orders?${qs({ status: o.status, q: o.q, page: o.page })}`));
    if (!data) return;
    box.innerHTML = data.items.length ? `
      <div class="table-wrap"><table>
        <thead><tr><th>الطلب</th><th>التاجر</th><th>الأصناف</th><th>الموردين</th><th>الإجمالي</th><th>الدفع</th><th>الحالة</th><th>الوقت</th></tr></thead>
        <tbody>${data.items.map((r) => `
          <tr class="clickable" data-id="${r.id}">
            <td><b class="num">${r.number}</b>${r.is_plus ? ' <span class="badge plus">بلس</span>' : ''}</td>
            <td class="name-cell"><b>${esc(r.shop_name || r.customer_name)}</b><span class="meta">${esc(r.customer_name)} · <span class="num">${esc(r.phone)}</span></span></td>
            <td>${r.items.toLocaleString('ar-EG-u-nu-latn')}${r.notes ? ' <span class="badge pink">فيها ملاحظات</span>' : ''}</td>
            <td>${r.pos.toLocaleString('ar-EG-u-nu-latn')}</td>
            <td class="num"><b>${money(r.grand_total)}</b>${r.final_total !== r.items_total && r.status !== 'submitted' ? `<span class="per old">${money(r.items_total)}</span>` : ''}</td>
            <td><span class="badge ${PAY_BADGE[r.payment_status] || 'gray'}">${esc(r.payment_label)}</span></td>
            <td><span class="badge ${ORDER_BADGE[r.status] || 'gray'}">${esc(r.status_label)}</span></td>
            <td class="small">${dateText(r.created_at)}</td>
          </tr>`).join('')}</tbody></table></div>
      ${data.pages > 1 ? `<div class="pager"><button class="btn sm" id="op-prev" ${data.page <= 1 ? 'disabled' : ''}>السابق</button><span class="muted">${data.page} من ${data.pages}</span><button class="btn sm" id="op-next" ${data.page >= data.pages ? 'disabled' : ''}>التالي</button></div>` : ''}`
      : '<div class="card empty">مفيش طلبات هنا.</div>';
    box.querySelectorAll('tr.clickable').forEach((tr) => (tr.onclick = () => openOrder(Number(tr.dataset.id))));
    const prev = box.querySelector('#op-prev');
    if (prev) { prev.onclick = () => { state.orders.page -= 1; loadOrders(); }; box.querySelector('#op-next').onclick = () => { state.orders.page += 1; loadOrders(); }; }
  }

  async function openOrder(id) {
    openDrawer('<div class="drawer-head"><button class="btn" data-close>رجوع</button><b>بيحمّل…</b></div>');
    const d = await run(null, () => api('GET', `${API}/orders/${id}`));
    if (d) drawOrder(d);
  }

  function poPrint(po, d) {
    const w = window.open('', '_blank');
    if (!w) { toast('المتصفح منع فتح نافذة الطباعة', true); return; }
    w.document.write(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>طلب شراء ${esc(po.number)}</title>
      <style>body{font-family:Tahoma,Arial,sans-serif;padding:24px;color:#222}h1{font-size:20px;margin:0 0 4px}table{width:100%;border-collapse:collapse;margin-top:16px}th,td{border:1px solid #ccc;padding:8px;text-align:right;font-size:14px}th{background:#f4eeee}.muted{color:#666;font-size:13px}</style></head><body>
      <h1>طلب شراء ${esc(po.number)}</h1><div class="muted">روز · ${dateText(d.created_at)}</div>
      <table><thead><tr><th>#</th><th>الصنف</th><th>الكود</th><th>الكمية</th><th>الوحدة</th><th>ملاحظة</th><th>متوفر؟</th></tr></thead><tbody>
      ${po.items.map((i, n) => `<tr><td>${n + 1}</td><td>${esc(i.supplier_item_name || i.name)}</td><td>${esc(i.supplier_code)}</td><td>${i.qty}</td><td>${UNIT_NAME[i.unit]}</td><td>${esc(i.note)}</td><td></td></tr>`).join('')}
      </tbody></table><p class="muted">ياريت تأكد المتوفر والناقص.</p><script>window.onload=function(){window.print()}<\/script></body></html>`);
    w.document.close();
  }

  function drawOrder(d) {
    const reviewing = d.status === 'submitted';
    const receiving = d.status === 'reviewed';
    const decisionBtns = (i) => `
      <div class="row" style="gap:4px">
        <button class="btn sm${i.review_status === 'available' ? ' primary' : ''}" data-dec="available" data-item="${i.id}">متوفر</button>
        <button class="btn sm${i.review_status === 'partial' ? ' primary' : ''}" data-dec="partial" data-item="${i.id}" data-qty="${i.qty}">عدد أقل${i.review_status === 'partial' ? ` (${i.confirmed_qty})` : ''}</button>
        <button class="btn sm${i.review_status === 'unavailable' ? ' primary' : ''}" data-dec="unavailable" data-item="${i.id}">مش متوفر</button>
      </div>`;
    const itemState = (i) => {
      if (d.status === 'submitted') return '';
      const f = i.final_qty;
      const confirmed = i.confirmed_qty || 0;
      if (i.picked_qty != null && i.received_qty != null && i.picked_qty < i.received_qty) {
        return i.picked_qty ? `<span class="badge amber">اتجهز ${i.picked_qty} من ${i.received_qty}</span>` : '<span class="badge red">مش موجود وقت التجهيز</span>';
      }
      if (i.received_qty != null && i.received_qty < confirmed) {
        return i.received_qty ? `<span class="badge amber">وصل ${i.received_qty} من ${confirmed}</span>` : '<span class="badge red">ماوصلش</span>';
      }
      if (f >= i.qty) return '<span class="badge green">متوفر</span>';
      if (f > 0) return `<span class="badge amber">${f} من ${i.qty}</span>`;
      return '<span class="badge red">مش متوفر</span>';
    };
    openDrawer(`
      <div class="drawer-head">
        <button class="btn" data-close>رجوع</button>
        <div style="flex:1;min-width:0"><b style="font-size:17px" class="num">طلب ${d.number}</b> <span class="badge ${ORDER_BADGE[d.status] || 'gray'}">${esc(d.status_label)}</span>
          <div class="small muted">${esc(d.customer.shop_name || '')} · ${esc(d.customer.name)} · <span class="num">${esc(d.customer.phone)}</span>${d.customer.area ? ' · ' + esc(d.customer.area) : ''} · فئة «${esc(d.tier_name)}»</div></div>
      </div>
      <div class="drawer-body">
        ${d.customer_note ? `<div class="alert info"><b>ملاحظة التاجر على الطلب:</b> ${esc(d.customer_note)}</div>` : ''}
        ${d.status === 'cancelled' ? `<div class="alert bad">الطلب اتلغى: ${esc(d.cancel_reason)}</div>` : ''}
        ${reviewing ? '<div class="alert warn">ابعت كل طلب شراء لمورده، وسجّل رده لكل صنف. لما كل الأصناف ياخدوا قرار، أكّد المراجعة: الناقص هيتشال من فاتورة التاجر ويتسجل في النواقص، والتاجر هيوصله إشعار بالإجمالي النهائي.</div>' : ''}
        ${receiving ? '<div class="alert info">الموردين بيوصّلوا لعندك. استلم كل طلب شراء لوحده، وعدّل الكمية لو حاجة وصلت ناقصة. لما كله يتستلم، الطلب بيروح للتجهيز.</div>' : ''}
        ${d.purchase_orders.map((po) => `
          <section class="card" data-po="${po.id}">
            <div class="row" style="margin-bottom:10px">
              <div style="flex:1"><b class="num" style="font-size:16px">${esc(po.number)}</b> · المورد <b>${esc(po.alias)}</b>
                <div class="small muted">شراء ${money(po.cost_total)}${po.sent_at ? ` · اتبعت ${dateText(po.sent_at)}` : ''}${po.received_at ? ` · اتستلم ${dateText(po.received_at)}` : ''}</div></div>
              <span class="badge ${PO_STATUS[po.status][0]}">${PO_STATUS[po.status][1]}</span>
            </div>
            ${reviewing ? `<div class="row" style="margin-bottom:10px">
              <a class="btn sm" data-wa href="https://wa.me/${esc(po.whatsapp_phone)}?text=${encodeURIComponent(po.message)}" target="_blank" rel="noopener">ابعته واتساب${po.whatsapp_phone ? '' : ' (من غير رقم)'}</a>
              <button class="btn sm" data-copy>نسخ الرسالة</button>
              <button class="btn sm" data-print>طباعة / PDF</button>
              <span class="spacer"></span>
              <button class="btn sm" data-all>الكل متوفر</button></div>` : ''}
            <div class="table-wrap"><table>
              <thead><tr><th>الصنف</th><th>الكمية</th><th>شراء</th><th>بيع</th><th>${reviewing ? 'رد المورد' : receiving && po.status !== 'received' && po.status !== 'cancelled' ? 'وصل كام' : 'الحالة'}</th></tr></thead>
              <tbody>${po.items.map((i) => `
                <tr>
                  <td class="name-cell"><b>${esc(i.name)}</b><span class="meta">${[i.supplier_item_name && i.supplier_item_name !== i.name ? 'عند المورد: ' + esc(i.supplier_item_name) : '', i.supplier_code ? 'كود ' + esc(i.supplier_code) : ''].filter(Boolean).join(' · ')}</span>${i.note ? `<span class="badge pink" style="margin-top:4px">ملاحظة: ${esc(i.note)}</span>` : ''}</td>
                  <td class="num">${i.qty} ${UNIT_NAME[i.unit]}${i.pieces && i.unit !== 'piece' ? `<span class="per">${i.pieces} قطعة</span>` : ''}</td>
                  <td class="num">${money(i.unit_cost)}</td>
                  <td class="num">${money(i.unit_price)}</td>
                  <td>${reviewing ? decisionBtns(i) : receiving && po.status !== 'received' && po.status !== 'cancelled'
                    ? (i.confirmed_qty ? `<input type="number" class="recv" data-item="${i.id}" min="0" max="${i.confirmed_qty}" value="${i.confirmed_qty}" style="width:90px;min-height:38px"> <span class="small muted">من ${i.confirmed_qty}</span>` : '<span class="badge red">مش متوفر</span>')
                    : itemState(i)}</td>
                </tr>`).join('')}</tbody>
            </table></div>
            ${receiving && po.status !== 'received' && po.status !== 'cancelled' ? '<div class="row" style="margin-top:10px"><span class="spacer"></span><button class="btn primary" data-receive>استلمت طلب الشراء ده</button></div>' : ''}
          </section>`).join('')}
        ${pickingSection(d)}
        ${deliverySection(d)}
        ${d.status !== 'cancelled' || d.payment.refund_due ? paymentSection(d) : ''}
        <section class="card">
          <div class="row">
            <div style="flex:1">
              ${reviewing
                ? `<div>الإجمالي المبدئي: <b class="num">${money(d.items_total)}</b></div><div>بعد ردود الموردين: <b class="num">${money(d.preview_total)}</b></div><div class="small muted">${d.pending_decisions ? `فاضل ${d.pending_decisions} صنف من غير قرار` : 'كل الأصناف خدت قرار'}</div>`
                : `<div>إجمالي الأصناف: <b class="num" style="font-size:18px">${money(d.final_total)}</b>${d.final_total !== d.items_total ? ` <span class="old num">${money(d.items_total)}</span>` : ''}</div><div class="small muted">تكلفة الشراء ${money(d.cost_total)} · الربح ${money(d.final_total - d.cost_total)}</div>`}
            </div>
            ${reviewing ? `<button class="btn primary" id="confirm-review" ${d.pending_decisions ? 'disabled' : ''}>تأكيد المراجعة</button>` : ''}
            ${['submitted', 'reviewed', 'received'].includes(d.status) ? '<button class="btn danger" id="cancel-order">إلغاء الطلب</button>' : ''}
          </div>
        </section>
      </div>`);
    const P = drawerPanel;
    const refresh = (r) => { drawOrder(r); loadSummary().catch(() => {}); };
    P.querySelectorAll('[data-dec]').forEach((b) => (b.onclick = () => {
      let qty = null;
      if (b.dataset.dec === 'partial') {
        const v = prompt(`المتوفر كام؟ (من 1 لحد ${Number(b.dataset.qty) - 1})`);
        if (v == null) return;
        qty = numOrNull(v);
      }
      run(b, async () => refresh(await api('POST', `${API}/orders/${d.id}/items/${b.dataset.item}/decision`, { decision: b.dataset.dec, qty })));
    }));
    P.querySelectorAll('[data-po]').forEach((sec) => {
      const po = d.purchase_orders.find((x) => x.id === Number(sec.dataset.po));
      const wa = sec.querySelector('[data-wa]');
      if (wa) wa.addEventListener('click', () => { api('POST', `${API}/purchase-orders/${po.id}/sent`).then(refresh).catch(() => {}); });
      const copy = sec.querySelector('[data-copy]');
      if (copy) copy.onclick = () => run(copy, async () => {
        await navigator.clipboard.writeText(po.message);
        toast('الرسالة اتنسخت');
        refresh(await api('POST', `${API}/purchase-orders/${po.id}/sent`));
      });
      const print = sec.querySelector('[data-print]');
      if (print) print.onclick = () => { poPrint(po, d); api('POST', `${API}/purchase-orders/${po.id}/sent`).then(refresh).catch(() => {}); };
      const all = sec.querySelector('[data-all]');
      if (all) all.onclick = () => run(all, async () => refresh(await api('POST', `${API}/orders/${d.id}/purchase-orders/${po.id}/all-available`)));
      const recv = sec.querySelector('[data-receive]');
      if (recv) recv.onclick = () => run(recv, async () => {
        const received = {};
        sec.querySelectorAll('.recv').forEach((i) => { received[i.dataset.item] = Number(i.value || 0); });
        const r = await api('POST', `${API}/purchase-orders/${po.id}/receive`, { received });
        toast(r.status === 'received' ? 'كل البضاعة وصلت، والطلب جاهز للتجهيز' : 'اتسجّل الاستلام');
        refresh(r);
      });
    });
    const confirmBtn = P.querySelector('#confirm-review');
    if (confirmBtn) confirmBtn.onclick = () => run(confirmBtn, async () => {
      const r = await api('POST', `${API}/orders/${d.id}/confirm-review`);
      toast(r.status === 'cancelled' ? 'الطلب اتلغى لأن مفيش ولا صنف متوفر' : 'المراجعة اتأكدت، والتاجر اتبلّغ');
      refresh(r);
    });
    bindPaymentSection(d, refresh);
    const gd = P.querySelector('#go-delivery');
    if (gd) gd.onclick = () => { closeDrawer(); go('delivery'); };
    const rel = P.querySelector('#release-pick');
    if (rel) rel.onclick = () => { if (confirm('الطلب هيرجع لقايمة التجهيز، وأي علامات اتعملت هتتمسح. تكمّل؟')) run(rel, async () => { refresh(await api('POST', `${API}/orders/${d.id}/release-pick`)); toast('القفل اتفك'); }); };
    const cancel = P.querySelector('#cancel-order');
    if (cancel) cancel.onclick = () => {
      const reason = prompt('سبب الإلغاء (هيوصل للتاجر):', '');
      if (reason == null) return;
      run(cancel, async () => { refresh(await api('POST', `${API}/orders/${d.id}/cancel`, { reason })); toast('الطلب اتلغى'); });
    };
  }

  // ----------------------------------------------------------- shortages
  async function renderShortages() {
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>النواقص</h2><p>الأصناف اللي اتشالت من طلبات التجار، متجمّعة حسب الصنف والمورد، وطلبات التوفير.</p></div>
        <select id="sh-supplier" style="width:auto;min-width:200px" aria-label="المورد">${options(suppliersList().map((s) => ({ value: s.id, label: s.alias })), state.shortSupplier, 'كل الموردين')}</select></div>
      <div id="sh-body"><p class="empty">بيحمّل…</p></div>`;
    view.querySelector('#sh-supplier').onchange = (e) => { state.shortSupplier = e.target.value; loadShortages(); };
    loadShortages();
  }

  async function loadShortages() {
    const box = view.querySelector('#sh-body');
    const data = await run(null, () => api('GET', `${API}/shortages?${qs({ supplier_id: state.shortSupplier })}`));
    if (!data || !box) return;
    const bySupplier = Object.entries(data.by_supplier);
    box.innerHTML = `
      ${bySupplier.length ? `<div class="chips" style="margin-bottom:12px">${bySupplier.map(([a, n]) => `<span class="badge gray" style="padding:4px 12px">${esc(a)}: ${n} صنف</span>`).join('')}</div>` : ''}
      <section class="card">
        <h3 style="margin-bottom:10px">أصناف ناقصة</h3>
        ${data.products.length ? `<div class="table-wrap"><table>
          <thead><tr><th>الصنف</th><th>المطلوب</th><th>تجار مستنيين</th><th>الموردين</th><th>من</th><th></th></tr></thead>
          <tbody>${data.products.map((g) => `
            <tr data-pid="${g.product_id}">
              <td class="name-cell"><b>${esc(g.name)}</b>${g.is_visible ? '' : '<span class="meta">مخفي من التاجر</span>'}</td>
              <td>${esc(g.units_text)}</td>
              <td><a href="#" data-who>${g.customers}</a></td>
              <td><div class="supplier-tags">${g.suppliers.length ? g.suppliers.map((s) => `<span class="badge gray">${esc(s.alias)}</span>`).join('') : '<span class="badge red">مفيش مورد</span>'}</div></td>
              <td class="small">${dateText(g.first_at)}</td>
              <td><button class="btn sm primary" data-avail>اتوفر، بلّغهم</button></td>
            </tr>
            <tr class="who" data-who-for="${g.product_id}" hidden><td colspan="6"></td></tr>`).join('')}</tbody></table></div>`
          : '<p class="muted">مفيش أصناف ناقصة.</p>'}
      </section>
      <section class="card">
        <h3 style="margin-bottom:10px">طلبات توفير (أصناف مش عندنا)</h3>
        <p class="small muted" style="margin-top:0">دي بتعرّفك السوق عايز إيه. لو وفّرت الصنف، اربطه بالصنف عندك والتاجر هيتبلّغ.</p>
        ${data.requests.length ? `<div class="table-wrap"><table>
          <thead><tr><th>الصنف المطلوب</th><th>الكمية</th><th>التاجر</th><th>التاريخ</th><th></th></tr></thead>
          <tbody>${data.requests.map((r) => `
            <tr data-rid="${r.id}">
              <td class="name-cell"><b>${esc(r.name)}</b>${r.note ? `<span class="meta">${esc(r.note)}</span>` : ''}</td>
              <td>${r.qty} ${esc(r.unit_label)}</td>
              <td class="name-cell"><b>${esc(r.shop_name || r.customer_name)}</b><span class="meta num">${esc(r.phone)}</span></td>
              <td class="small">${dateText(r.created_at)}</td>
              <td><div class="row" style="gap:4px"><button class="btn sm" data-link>اتوفر، اربطه</button><button class="btn sm ghost" data-close-req>قفل</button></div></td>
            </tr>`).join('')}</tbody></table></div>`
          : '<p class="muted">مفيش طلبات توفير.</p>'}
      </section>`;
    box.querySelectorAll('tr[data-pid]').forEach((tr) => {
      const pid = tr.dataset.pid;
      tr.querySelector('[data-avail]').onclick = (e) => {
        if (!confirm('كل التجار المستنيين الصنف ده هيوصلهم إشعار إنه اتوفر. تكمّل؟')) return;
        run(e.target, async () => { const r = await api('POST', `${API}/shortages/products/${pid}/available`); toast(`اتبلّغ ${r.notified} تاجر`); await loadSummary(); loadShortages(); });
      };
      tr.querySelector('[data-who]').onclick = (e) => run(null, async () => {
        e.preventDefault();
        const row = box.querySelector(`[data-who-for="${pid}"]`);
        if (!row.hidden) { row.hidden = true; return; }
        const list = await api('GET', `${API}/shortages/products/${pid}`);
        row.firstElementChild.innerHTML = list.map((w) => `<div class="small" style="padding:3px 0">${esc(w.shop_name || w.name)} · <span class="num">${esc(w.phone)}</span> · ${w.qty} ${esc(w.unit_label)}${w.order_number ? ` · طلب ${w.order_number}` : ''} · ${w.source === 'receipt' ? 'ماوصلش من المورد' : 'المورد مكانش عنده'}</div>`).join('');
        row.hidden = false;
      });
    });
    box.querySelectorAll('tr[data-rid]').forEach((tr) => {
      const rid = tr.dataset.rid;
      tr.querySelector('[data-close-req]').onclick = (e) => {
        const message = prompt('رسالة للتاجر (اختياري، سيبها فاضية لو مش عايز تبعت):', '');
        if (message == null) return;
        run(e.target, async () => { await api('POST', `${API}/shortages/${rid}/close`, { message }); toast('اتقفل'); await loadSummary(); loadShortages(); });
      };
      tr.querySelector('[data-link]').onclick = (e) => {
        const q = prompt('اكتب اسم الصنف عندك علشان نربطه:', tr.querySelector('b').textContent);
        if (!q) return;
        run(e.target, async () => {
          const found = (await api('GET', `${API}/products?${qs({ q })}`)).items.filter((i) => i.available);
          if (!found.length) throw new Error('مفيش صنف متاح بالاسم ده. ضيفه الأول من الأصناف.');
          const pick = found.length === 1 ? found[0] : found[Number(prompt(found.slice(0, 9).map((f, n) => `${n + 1}. ${f.name}`).join('\n') + '\n\nاكتب رقم الصنف:', '1')) - 1];
          if (!pick) return;
          await api('POST', `${API}/shortages/${rid}/close`, { product_id: pick.id });
          toast(`اتربط بـ «${pick.name}» والتاجر اتبلّغ`);
          await loadSummary();
          loadShortages();
        });
      };
    });
  }

  // ------------------------------------------------------------- traders
  const TRADER_TABS = [['pending', 'طلبات الانضمام'], ['approved', 'التجار'], ['suspended', 'موقوفين'], ['rejected', 'مرفوضين']];
  async function renderTraders() {
    const t = state.traders;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>التجار</h2><p>التاجر بيسجّل بنفسه من شاشة الجملة برقمه وكلمة سر واسم محله، وطلبه بيوصل هنا. بعد الموافقة بيشوف أسعار فئته بس.</p></div></div>
      <div class="card stack">
        <div class="chips">${TRADER_TABS.map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${t.status === k}">${l}</button>`).join('')}</div>
        <div class="row"><input type="search" id="tr-q" placeholder="دوّر بالاسم أو المحل أو الموبايل" value="${esc(t.q)}" style="flex:1;min-width:220px"></div>
      </div>
      <div id="tr-list" style="margin-top:16px"></div>`;
    view.querySelectorAll('.chips .chip').forEach((b) => (b.onclick = () => { t.status = b.dataset.k; renderTraders(); }));
    let timer;
    view.querySelector('#tr-q').addEventListener('input', (e) => { clearTimeout(timer); timer = setTimeout(() => { t.q = e.target.value.trim(); loadTraderAccounts(); }, 300); });
    loadTraderAccounts();
  }

  async function loadTraderAccounts() {
    const box = view.querySelector('#tr-list');
    const t = state.traders;
    if (!state.zonesList) state.zonesList = await run(null, () => api('GET', `${API}/zones`)) || [];
    const list = await run(null, () => api('GET', `${API}/trader-accounts?${qs({ status: t.status, q: t.q })}`));
    if (!list || !box) return;
    const tiers = tiersList();
    const subscriber = tiers.find((x) => x.is_subscriber);
    const label = { none: ['gray', 'مش تاجر'], pending: ['amber', 'مستني'], approved: ['green', 'تاجر'], suspended: ['red', 'موقوف'], rejected: ['gray', 'مرفوض'] };
    if (!list.length) {
      box.innerHTML = `<div class="card empty">${t.status === 'search' ? (t.q ? 'مفيش عملاء بالبحث ده.' : 'اكتب اسم العميل أو موبايله.') : 'مفيش حد هنا.'}</div>`;
      return;
    }
    box.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>التاجر</th><th>المحل والعنوان</th><th>الحالة</th><th>الفئة</th><th>منطقة التوصيل</th>${t.status === 'approved' ? '<th>طلبات</th>' : ''}<th></th></tr></thead>
      <tbody>${list.map((c) => `
        <tr data-cid="${c.customer_id}">
          <td class="name-cell"><b>${esc(c.name)}</b><span class="meta num">${esc(c.phone)}</span></td>
          <td class="name-cell"><b>${esc(c.shop_name || '—')}</b><span class="meta">${esc(c.area || '')}${c.address ? ' · ' + esc(c.address) : ''}${c.note ? ' · ' + esc(c.note) : ''}</span></td>
          <td><span class="badge ${label[c.status][0]}">${label[c.status][1]}</span>${c.created_at && c.status === 'pending' ? `<span class="per">${dateText(c.created_at)}</span>` : ''}</td>
          <td><select class="tr-tier" style="max-width:200px">${options(tiers.map((x) => ({ value: x.id, label: x.name + (c.is_default_tier && x.id === c.tier_id ? ' (افتراضي)' : '') })), c.tier_id)}</select></td>
          <td>${c.status !== 'none' ? `<select class="tr-zone" style="max-width:180px">${options((state.zonesList || []).map((z) => ({ value: z.id, label: z.name })), c.zone_id || '', 'من غير منطقة')}</select>` : ''}</td>
          ${t.status === 'approved' ? `<td>${(c.orders || 0).toLocaleString('ar-EG-u-nu-latn')}</td>` : ''}
          <td><div class="row" style="gap:4px">
            ${c.status !== 'approved' ? `<button class="btn sm primary" data-st="approved">${c.status === 'suspended' ? 'رجّعه' : 'موافقة'}</button>` : '<button class="btn sm danger" data-st="suspended">إيقاف</button>'}
            ${c.status === 'pending' ? '<button class="btn sm" data-st="rejected">رفض</button>' : ''}
            ${c.status === 'approved' ? '<button class="btn sm" data-points>النقاط</button>' : ''}
            <button class="btn sm ghost" data-reset>كلمة سر مؤقتة</button>
          </div></td>
        </tr>`).join('')}</tbody></table></div>
      ${subscriber ? '' : ''}`;
    box.querySelectorAll('tr[data-cid]').forEach((tr) => {
      const cid = tr.dataset.cid;
      const c = list.find((x) => String(x.customer_id) === cid);
      const zoneSel = tr.querySelector('.tr-zone');
      if (zoneSel) zoneSel.onchange = (e) => run(e.target, async () => { await api('PUT', `${API}/trader-accounts/${cid}/delivery`, { zone_id: Number(e.target.value || 0) }); toast('المنطقة اتحفظت'); });
      tr.querySelector('.tr-tier').onchange = (e) => run(e.target, async () => {
        const r = await api('PUT', `${API}/traders/${cid}/tier`, { tier_id: Number(e.target.value) });
        toast(`اتنقل لفئة «${r.tier_name}»`);
      });
      const pts = tr.querySelector('[data-points]');
      if (pts) pts.onclick = () => traderPointsDrawer(c);
      tr.querySelector('[data-reset]').onclick = (e) => {
        if (!confirm(`تعمل كلمة سر مؤقتة لـ «${c.name}»؟ كلمة السر القديمة هتبطل.`)) return;
        run(e.target, async () => {
          const r = await api('POST', `${API}/trader-accounts/${cid}/password-reset`);
          const msg = `أهلًا ${c.name}، كلمة السر المؤقتة لروز جملة: ${r.temporary_password}\nادخل بيها وغيّرها من «حسابي».`;
          prompt('ابعت كلمة السر دي للتاجر (هتظهر مرة واحدة):', msg);
        });
      };
      tr.querySelectorAll('[data-st]').forEach((b) => (b.onclick = () => {
        if (b.dataset.st === 'suspended' && !confirm(`توقف «${c.name}» عن طلب الجملة؟`)) return;
        run(b, async () => {
          await api('POST', `${API}/trader-accounts/${cid}`, { status: b.dataset.st, tier_id: Number(tr.querySelector('.tr-tier').value) });
          toast({ approved: 'اتقبل، والتاجر اتبلّغ', rejected: 'اترفض', suspended: 'اتوقف' }[b.dataset.st]);
          await loadSummary();
          loadTraderAccounts();
        });
      }));
    });
  }

  // ============================================================ phase 4
  const PAY_BADGE = { paid: 'green', pending: 'amber', unpaid: 'red', cod: 'gray', credit: 'pink' };
  const egp = (v) => `${money(v)} ج`;

  function paymentSection(d) {
    const p = d.payment;
    const pend = p.pending_payment;
    return `
      <section class="card">
        <div class="row" style="margin-bottom:8px"><h3 style="flex:1">الدفع</h3><span class="badge ${PAY_BADGE[p.status] || 'gray'}">${esc(p.status_label)}</span></div>
        <div class="grid-2">
          <div class="stack" style="gap:4px">
            <div class="row"><span class="muted" style="flex:1">طريقة الدفع</span><b>${esc(p.method_label)}</b></div>
            <div class="row"><span class="muted" style="flex:1">الأصناف</span><span class="num">${egp(p.products)}</span></div>
            <div class="row"><span class="muted" style="flex:1">التوصيل</span><span class="num">${egp(p.delivery)}</span></div>
            ${p.fee ? `<div class="row"><span class="muted" style="flex:1">العمولة (${p.fee_payer === 'customer' ? 'على التاجر' : 'علينا'})</span><span class="num">${egp(p.fee)}</span></div>` : ''}
            <div class="row"><b style="flex:1">المطلوب من التاجر</b><b class="num">${egp(p.grand)}</b></div>
            ${p.paid ? `<div class="row"><span class="muted" style="flex:1">اتدفع</span><span class="num">${egp(p.paid)}</span></div>` : ''}
            ${p.points_awarded ? `<div class="row"><span class="muted" style="flex:1">نقاط اتضافت</span><span class="num">${p.points_awarded}</span></div>` : ''}
          </div>
          <div class="stack">
            ${pend ? `<div class="alert warn">التاجر بعت صورة تحويل بـ ${egp(pend.amount)}.
              <div class="row" style="margin-top:8px"><a class="btn sm" href="${API}/payments/${pend.id}/proof" target="_blank" rel="noopener">شوف الصورة</a>
              <button class="btn sm primary" data-pay-ok="${pend.id}">التحويل وصل</button><button class="btn sm danger" data-pay-no="${pend.id}">رفض</button></div></div>` : ''}
            ${p.refund_due ? `<div class="alert bad">لازم ترجّع للتاجر ${egp(p.refund_due)} كاش. <button class="btn sm" id="refunded" style="margin-top:6px">رجّعتهم</button></div>` : ''}
            ${['instapay', 'wallet'].includes(p.method) && p.status === 'unpaid' && d.status !== 'submitted' && d.status !== 'cancelled' ? '<div class="alert info">مستنيين التاجر يحوّل ويبعت الصورة.</div>' : ''}
            ${['received', 'preparing', 'ready', 'out_for_delivery'].includes(d.status) ? `<button class="btn" id="delivered">اتسلّم من غير مندوب${p.method === 'cash' ? ' (الكاش معانا)' : ''}</button><p class="small muted" style="margin:0">لو التاجر استلم من المحل أو المندوب ماقدرش يقفل المشوار. الطبيعي إن المندوب يقفله بكود الاستلام.</p>` : ''}
          </div>
        </div>
      </section>`;
  }

  function bindPaymentSection(d, refresh) {
    const P = drawerPanel;
    const ok = P.querySelector('[data-pay-ok]');
    if (ok) ok.onclick = () => run(ok, async () => { await api('POST', `${API}/payments/${ok.dataset.payOk}/decide`, { approve: true }); toast('التحويل اتأكد'); refresh(await api('GET', `${API}/orders/${d.id}`)); });
    const no = P.querySelector('[data-pay-no]');
    if (no) no.onclick = () => {
      const reason = prompt('سبب الرفض (هيوصل للتاجر):', 'المبلغ ماوصلش');
      if (!reason) return;
      run(no, async () => { await api('POST', `${API}/payments/${no.dataset.payNo}/decide`, { approve: false, reason }); toast('اترفض، والتاجر اتبلّغ'); refresh(await api('GET', `${API}/orders/${d.id}`)); });
    };
    const del = P.querySelector('#delivered');
    if (del) del.onclick = () => {
      if (!confirm('تأكيد إن الطلب اتسلّم للتاجر؟')) return;
      run(del, async () => { const r = await api('POST', `${API}/orders/${d.id}/delivered`); toast(r.points_awarded ? `اتسلّم، واتضاف ${r.points_awarded} نقطة للتاجر` : 'اتسلّم'); refresh(r); });
    };
    const ref = P.querySelector('#refunded');
    if (ref) ref.onclick = () => {
      const v = prompt('المبلغ اللي رجّعته:', String(d.payment.refund_due));
      if (v == null) return;
      run(ref, async () => { refresh(await api('POST', `${API}/orders/${d.id}/refunded`, { amount: numOrNull(v) })); toast('اتسجّل'); });
    };
  }

  // ------------------------------------------------------------ payments
  async function renderPayments() {
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>المدفوعات</h2><p>تحويلات التجار اللي محتاجة تأكيد، وطرق الدفع وعمولاتها.</p></div></div>
      <section class="card">
        <div class="row" style="margin-bottom:12px"><h3 style="flex:1">التحويلات</h3>
          <div class="chips" id="pay-status">${[['pending', 'مستنية تأكيد'], ['confirmed', 'اتأكدت'], ['rejected', 'اترفضت']].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${(state.payStatus || 'pending') === k}">${l}</button>`).join('')}</div></div>
        <div id="pay-list"><p class="muted">بيحمّل…</p></div>
      </section>
      <section class="card" style="margin-top:16px">
        <h3 style="margin-bottom:4px">طرق الدفع</h3>
        <p class="small muted" style="margin:0 0 12px">شغّل أو وقّف كل طريقة. العمولة بتتحسب على (الأصناف + التوصيل). الطريقة اللي العمولة فيها «على التاجر» بتتضاف لفاتورته.</p>
        <div id="methods" class="grid-2"></div>
      </section>
      <section class="card" style="margin-top:16px">
        <h3 style="margin-bottom:8px">حاسبة تجريبية</h3>
        <div class="row"><label class="field" style="max-width:220px"><span>قيمة الأصناف</span><input type="number" id="calc" value="1000" min="0" step="1"></label><div id="calc-out" class="small" style="flex:1"></div></div>
      </section>`;
    view.querySelectorAll('#pay-status .chip').forEach((b) => (b.onclick = () => { state.payStatus = b.dataset.k; view.querySelectorAll('#pay-status .chip').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); loadPayments(); }));
    loadPayments();
    const methods = await run(null, () => api('GET', `${API}/payment-methods`));
    if (!methods) return;
    state.methods = methods;
    drawMethods();
  }

  async function loadPayments() {
    const box = view.querySelector('#pay-list');
    const list = await run(null, () => api('GET', `${API}/payments?status=${state.payStatus || 'pending'}`));
    if (!list || !box) return;
    box.innerHTML = list.length ? `<div class="table-wrap"><table>
      <thead><tr><th>التاجر</th><th>عن إيه</th><th>المبلغ</th><th>الطريقة</th><th>المحوّل منه</th><th>الوقت</th><th></th></tr></thead>
      <tbody>${list.map((p) => `<tr data-id="${p.id}">
        <td class="name-cell"><b>${esc(p.shop_name || p.customer_name)}</b><span class="meta num">${esc(p.phone)}</span></td>
        <td>${p.purpose === 'order' ? `طلب <a href="#" data-order="${p.order_id}" class="num">${p.order_number}</a>` : 'سداد آجل'}</td>
        <td class="num"><b>${egp(p.amount)}</b></td>
        <td>${esc(p.method_label || p.method)}</td>
        <td>${esc(p.sender || '—')}</td>
        <td class="small">${dateText(p.created_at)}${p.reject_reason ? `<span class="per">${esc(p.reject_reason)}</span>` : ''}</td>
        <td><div class="row" style="gap:4px">${p.has_proof ? `<a class="btn sm" href="${API}/payments/${p.id}/proof" target="_blank" rel="noopener">الصورة</a>` : ''}
          ${p.status === 'pending' ? '<button class="btn sm primary" data-ok>وصل</button><button class="btn sm danger" data-no>رفض</button>' : ''}</div></td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="muted">مفيش.</p>';
    box.querySelectorAll('tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      const ok = tr.querySelector('[data-ok]');
      if (ok) ok.onclick = () => run(ok, async () => { await api('POST', `${API}/payments/${id}/decide`, { approve: true }); toast('اتأكد'); await loadSummary(); loadPayments(); });
      const no = tr.querySelector('[data-no]');
      if (no) no.onclick = () => {
        const reason = prompt('سبب الرفض (هيوصل للتاجر):', 'المبلغ ماوصلش');
        if (!reason) return;
        run(no, async () => { await api('POST', `${API}/payments/${id}/decide`, { approve: false, reason }); toast('اترفض'); await loadSummary(); loadPayments(); });
      };
      const link = tr.querySelector('[data-order]');
      if (link) link.onclick = (e) => { e.preventDefault(); openOrder(Number(link.dataset.order)); };
    });
  }

  function drawMethods() {
    const box = view.querySelector('#methods');
    box.innerHTML = state.methods.map((m) => `
      <div class="card stack" data-code="${m.code}" style="margin:0">
        <div class="row"><input type="text" class="m-label" value="${esc(m.label)}" maxlength="60" style="flex:1;font-weight:600">
          <label class="switch"><input type="checkbox" class="m-on" ${m.is_enabled ? 'checked' : ''}><span class="track"></span><span>شغّالة</span></label></div>
        ${['instapay', 'wallet'].includes(m.code) ? `
          <label class="field"><span>${m.code === 'instapay' ? 'عنوان انستاباي' : 'رقم المحفظة'}</span><input type="text" class="m-account" value="${esc(m.account)}" maxlength="120" dir="ltr"></label>
          <div class="row">
            <label class="field" style="flex:1"><span>العمولة</span><input type="number" class="m-fee" value="${m.fee_value}" min="0" step="0.1"></label>
            <label class="field" style="flex:1"><span>نوعها</span><select class="m-type"><option value="percent" ${m.fee_type === 'percent' ? 'selected' : ''}>نسبة ٪</option><option value="fixed" ${m.fee_type === 'fixed' ? 'selected' : ''}>مبلغ ثابت</option></select></label>
            <label class="field" style="flex:1"><span>مين بيدفعها</span><select class="m-payer"><option value="customer" ${m.fee_payer === 'customer' ? 'selected' : ''}>التاجر</option><option value="store" ${m.fee_payer === 'store' ? 'selected' : ''}>إحنا</option></select></label>
          </div>` : ''}
        ${m.code === 'cash' ? `<label class="field"><span>أقصى مبلغ للكاش عند الاستلام (0 = من غير حد)</span><input type="number" class="m-max" value="${m.max_amount}" min="0" step="1"></label>` : ''}
        ${m.code === 'credit' ? '<p class="small muted" style="margin:0">بتظهر بس للتاجر اللي ليه حد آجل من صفحة الآجل.</p>' : ''}
        <label class="field"><span>تعليمات للتاجر (اختياري)</span><textarea class="m-instr" maxlength="600" style="min-height:60px">${esc(m.instructions)}</textarea></label>
        <div class="row"><span class="spacer"></span><button class="btn sm primary" data-save>حفظ</button></div>
      </div>`).join('');
    box.querySelectorAll('[data-code]').forEach((card) => {
      const code = card.dataset.code;
      const val = (sel) => { const el = card.querySelector(sel); return el ? el.value : undefined; };
      const save = (btn) => run(btn, async () => {
        const body = { label: val('.m-label'), is_enabled: card.querySelector('.m-on').checked, instructions: val('.m-instr') };
        if (card.querySelector('.m-account')) Object.assign(body, { account: val('.m-account'), fee_value: numOrNull(val('.m-fee')) || 0, fee_type: val('.m-type'), fee_payer: val('.m-payer') });
        if (card.querySelector('.m-max')) body.max_amount = numOrNull(val('.m-max')) || 0;
        try {
          state.methods = await api('PUT', `${API}/payment-methods/${code}`, body);
          toast('اتحفظ');
        } finally { drawMethods(); }
      });
      card.querySelector('[data-save]').onclick = (e) => save(e.target);
      card.querySelector('.m-on').onchange = (e) => save(e.target);
    });
    const calc = () => {
      const products = Number(view.querySelector('#calc').value || 0);
      const delivery = Number((state.summary.settings || {}).delivery_fee || 0);
      view.querySelector('#calc-out').innerHTML = state.methods.filter((m) => m.is_enabled).map((m) => {
        const base = products + delivery;
        const fee = ['instapay', 'wallet'].includes(m.code) ? (m.fee_type === 'fixed' ? m.fee_value : Math.round(base * m.fee_value) / 100) : 0;
        const grand = base + (m.fee_payer === 'customer' ? fee : 0);
        return `<div><b>${esc(m.label)}:</b> التاجر يدفع <b class="num">${egp(grand)}</b>${fee ? ` (العمولة ${egp(fee)} ${m.fee_payer === 'customer' ? 'عليه' : 'علينا'})` : ''}</div>`;
      }).join('') + `<div class="muted">التوصيل ${egp(delivery)} من الإعدادات.</div>`;
    };
    view.querySelector('#calc').oninput = calc;
    calc();
  }

  // -------------------------------------------------------------- credit
  async function renderCredit() {
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>الآجل</h2><p>آجل التجار في روز بس، ومنفصل عن الفكة. حد الآجل صفر يعني التاجر مايشوفش «على الحساب».</p></div>
        <button class="btn primary" id="cr-add">آجل لتاجر جديد</button></div>
      <div class="chips" id="cr-status" style="margin-bottom:12px">${[['', 'الكل'], ['overdue', 'متأخر'], ['near_limit', 'قرب الحد'], ['ok', 'منتظم']].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${(state.creditStatus || '') === k}">${l}</button>`).join('')}</div>
      <div id="cr-list"><p class="muted">بيحمّل…</p></div>`;
    view.querySelectorAll('#cr-status .chip').forEach((b) => (b.onclick = () => { state.creditStatus = b.dataset.k; renderCredit(); }));
    view.querySelector('#cr-add').onclick = async () => {
      const q = prompt('اكتب اسم التاجر أو موبايله:');
      if (!q) return;
      const list = await run(null, () => api('GET', `${API}/trader-accounts?${qs({ status: 'approved', q })}`));
      if (!list) return;
      if (!list.length) { toast('مفيش تاجر متقبل بالاسم ده', true); return; }
      const pick = list.length === 1 ? list[0] : list[Number(prompt(list.slice(0, 9).map((t, n) => `${n + 1}. ${t.shop_name || t.name} (${t.phone})`).join('\n') + '\n\nاكتب الرقم:', '1')) - 1];
      if (pick) openCredit(pick.customer_id);
    };
    const list = await run(null, () => api('GET', `${API}/credit?${qs({ status: state.creditStatus || '' })}`));
    const box = view.querySelector('#cr-list');
    if (!list || !box) return;
    const badge = { overdue: 'red', near_limit: 'amber', ok: 'green' };
    box.innerHTML = list.length ? `<div class="table-wrap"><table>
      <thead><tr><th>التاجر</th><th>عليه</th><th>الحد</th><th>المتاح</th><th>ميعاد السداد</th><th>الحالة</th></tr></thead>
      <tbody>${list.map((c) => `<tr class="clickable" data-cid="${c.customer_id}">
        <td class="name-cell"><b>${esc(c.shop_name || c.name)}</b><span class="meta num">${esc(c.phone)}</span></td>
        <td class="num"><b>${egp(c.balance)}</b>${c.reserved ? `<span class="per">+ ${egp(c.reserved)} طلبات مفتوحة</span>` : ''}</td>
        <td class="num">${egp(c.limit)}</td><td class="num">${egp(c.available)}</td>
        <td class="small">${c.next_due ? dateText(c.next_due) : '—'}</td>
        <td><span class="badge ${badge[c.status]}">${esc(c.status_label)}</span></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="card empty">مفيش تجار عندهم آجل. دوس «آجل لتاجر جديد» وحط له حد.</div>';
    box.querySelectorAll('tr[data-cid]').forEach((tr) => (tr.onclick = () => openCredit(Number(tr.dataset.cid))));
  }

  async function openCredit(cid) {
    openDrawer('<div class="drawer-head"><button class="btn" data-close>رجوع</button><b>بيحمّل…</b></div>');
    const c = await run(null, () => api('GET', `${API}/credit/${cid}`));
    if (c) drawCredit(cid, c);
  }

  function drawCredit(cid, c) {
    const days = (state.summary.settings || {}).credit_period_days || 30;
    openDrawer(`
      <div class="drawer-head"><button class="btn" data-close>رجوع</button><b style="font-size:17px;flex:1">حساب الآجل</b><span class="badge ${{ overdue: 'red', near_limit: 'amber', ok: 'green' }[c.status]}">${esc(c.status_label)}</span></div>
      <div class="drawer-body">
        <div class="stats">
          <div class="stat ${c.balance ? 'warn' : ''}"><b class="num">${egp(c.balance)}</b><span>عليه</span></div>
          <div class="stat"><b class="num">${egp(c.limit)}</b><span>الحد</span></div>
          <div class="stat good"><b class="num">${egp(c.available)}</b><span>المتاح</span></div>
          <div class="stat ${c.overdue ? 'bad' : ''}"><b>${c.next_due ? new Date(c.next_due).toLocaleDateString('ar-EG-u-nu-latn') : '—'}</b><span>ميعاد السداد</span></div>
        </div>
        <section class="card stack">
          <h3>الحد وفترة السداد</h3>
          <div class="row">
            <label class="field" style="flex:1"><span>حد الآجل (ج)</span><input type="number" id="cr-limit" min="0" step="100" value="${c.limit}"></label>
            <label class="field" style="flex:1"><span>فترة السداد بالأيام (فاضي = ${days} من الإعدادات)</span><input type="number" id="cr-days" min="1" max="365" value=""></label>
            <button class="btn primary" id="cr-save" style="align-self:flex-end">حفظ</button>
          </div>
        </section>
        <section class="card stack">
          <h3>تسجيل حركة</h3>
          <div class="row">
            <select id="cr-kind" style="width:auto"><option value="payment">سداد كاش</option><option value="adjustment">تسوية (+ يزوّد، − يقلّل)</option></select>
            <input type="number" id="cr-amount" step="0.01" placeholder="المبلغ" style="flex:1;min-width:120px">
            <input type="text" id="cr-note" maxlength="200" placeholder="ملاحظة" style="flex:2;min-width:160px">
            <button class="btn" id="cr-entry">سجّل</button>
          </div>
        </section>
        <section class="card">
          <h3 style="margin-bottom:8px">الحركات</h3>
          ${c.entries.length ? `<div class="table-wrap"><table><thead><tr><th>التاريخ</th><th>الحركة</th><th>المبلغ</th><th>الاستحقاق</th></tr></thead><tbody>${c.entries.map((e) => `<tr>
            <td class="small">${dateText(e.created_at)}</td><td>${esc(e.label)}${e.order_number ? ` <span class="num">${e.order_number}</span>` : ''}${e.note ? `<span class="per">${esc(e.note)}</span>` : ''}</td>
            <td class="num ${e.kind === 'payment' ? 'down' : 'up'}">${e.kind === 'payment' ? '−' : ''}${egp(Math.abs(e.amount))}</td><td class="small">${e.due_date ? dateText(e.due_date) : ''}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">مفيش حركات.</p>'}
        </section>
      </div>`);
    const P = drawerPanel;
    P.querySelector('#cr-save').onclick = (e) => run(e.target, async () => {
      const r = await api('PUT', `${API}/credit/${cid}`, { credit_limit: numOrNull(P.querySelector('#cr-limit').value) || 0, period_days: numOrNull(P.querySelector('#cr-days').value) });
      toast('اتحفظ'); drawCredit(cid, r);
    });
    P.querySelector('#cr-entry').onclick = (e) => run(e.target, async () => {
      const r = await api('POST', `${API}/credit/${cid}/entries`, { kind: P.querySelector('#cr-kind').value, amount: numOrNull(P.querySelector('#cr-amount').value), note: P.querySelector('#cr-note').value });
      toast('اتسجّل'); drawCredit(cid, r);
    });
  }

  // ------------------------------------------------------------ settings
  function renderSettings() {
    const s = state.summary.settings;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>الإعدادات</h2><p>أرقام بتغيّرها بإيدك وقت ما تحب. صفر يعني الحاجة دي مقفولة.</p></div></div>
      <div class="grid-2">
        <section class="card stack">
          <h3>الطلب والتوصيل</h3>
          <label class="field"><span>أقل قيمة للطلب (ج)</span><input type="number" id="s-min" min="0" step="10" value="${esc(s.min_order_total)}"></label>
          <label class="field"><span>رسوم التوصيل (ج)</span><input type="number" id="s-delivery" min="0" step="5" value="${esc(s.delivery_fee)}"></label>
          <p class="small muted" style="margin:0">دي رسوم أي تاجر مالوش منطقة. رسوم وأقل طلب كل منطقة من قسم «التوصيل».</p>
        </section>
        <section class="card stack">
          <h3>النقاط</h3>
          <label class="field"><span>نقطة واحدة لكل كام جنيه؟ (0 = من غير نقاط)</span><input type="number" id="s-points" min="0" step="1" value="${esc(s.points_egp_per_point)}"></label>
          <p class="small muted" style="margin:0">بتتحسب على إجمالي الأصناف بعد المراجعة، وبتتضاف لرصيد روز بعد التسليم.</p>
        </section>
        <section class="card stack">
          <h3>الآجل</h3>
          <label class="field"><span>فترة السداد الافتراضية (أيام)</span><input type="number" id="s-days" min="1" max="365" value="${esc(s.credit_period_days)}"></label>
          <label class="switch"><input type="checkbox" id="s-block" ${s.credit_block_overdue === '1' ? 'checked' : ''}><span class="track"></span><span>وقّف الطلبات الجديدة للتاجر المتأخر</span></label>
        </section>
        <section class="card stack">
          <h3>المرتجع</h3>
          <label class="field"><span>مدة الإرجاع بالأيام من الاستلام</span><input type="number" id="s-return" min="1" max="90" value="${esc(s.return_days || 15)}"></label>
        </section>
        <section class="card stack">
          <h3>روز بلس</h3>
          <label class="switch"><input type="checkbox" id="s-plus" ${s.plus_enabled === '1' ? 'checked' : ''}><span class="track"></span><span>اعرض روز بلس للتجار</span></label>
          <div class="row"><label class="field" style="flex:1"><span>سعر الشهر (ج)</span><input type="number" id="s-plus-m" min="0" step="5" value="${esc(s.plus_price_month || 0)}"></label>
            <label class="field" style="flex:1"><span>سعر 3 شهور (ج)</span><input type="number" id="s-plus-q" min="0" step="5" value="${esc(s.plus_price_quarter || 0)}"></label></div>
          <label class="switch"><input type="checkbox" id="s-plus-del" ${s.plus_free_delivery !== '0' ? 'checked' : ''}><span class="track"></span><span>التوصيل مجاني للمشترك</span></label>
          <label class="field"><span>المشترك بيدفع كام ٪ من عمولة المحفظة وانستاباي؟</span><input type="number" id="s-plus-fee" min="0" max="100" step="5" value="${esc(s.plus_fee_share || 50)}"></label>
          <p class="small muted" style="margin:0">50 يعني نص العمولة. السعر صفر يعني المدة دي مش معروضة.</p>
        </section>
      </div>
      <div class="row" style="margin-top:16px"><span class="spacer"></span><button class="btn primary" id="s-save">حفظ الإعدادات</button></div>
      <div class="grid-2" id="accounts-box" style="margin-top:16px"></div>`;
    accountsSection(view.querySelector('#accounts-box'));
    view.querySelector('#s-save').onclick = (e) => run(e.target, async () => {
      await api('PUT', `${API}/settings`, {
        min_order_total: numOrNull(view.querySelector('#s-min').value) || 0,
        delivery_fee: numOrNull(view.querySelector('#s-delivery').value) || 0,
        points_egp_per_point: numOrNull(view.querySelector('#s-points').value) || 0,
        credit_period_days: numOrNull(view.querySelector('#s-days').value) || 30,
        credit_block_overdue: view.querySelector('#s-block').checked,
        return_days: numOrNull(view.querySelector('#s-return').value) || 15,
        plus_enabled: view.querySelector('#s-plus').checked,
        plus_price_month: numOrNull(view.querySelector('#s-plus-m').value) || 0,
        plus_price_quarter: numOrNull(view.querySelector('#s-plus-q').value) || 0,
        plus_free_delivery: view.querySelector('#s-plus-del').checked,
        plus_fee_share: numOrNull(view.querySelector('#s-plus-fee').value) ?? 50,
      });
      toast('الإعدادات اتحفظت');
      render();
    });
  }

  // ============================================================ phase 5
  const clockText = (s) => (s == null ? '—' : `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`);

  function pinCard(p) {
    const link = `${location.origin}/pick/`;
    const msg = `أهلًا ${p.name}، ده رابط تجهيز طلبات روز: ${link}\nادخل برقم موبايلك ${p.phone} والكود السري: ${p.pin}\nمتشاركش الكود مع حد.`;
    const phone = p.phone.startsWith('01') ? '2' + p.phone : p.phone;
    return `<div class="alert good stack" style="gap:8px">
      <div>الكود السري لـ <b>${esc(p.name)}</b>: <b class="num" style="font-size:22px;letter-spacing:4px">${esc(p.pin)}</b></div>
      <div class="small">الكود بيظهر مرة واحدة بس. ابعته للموظف دلوقتي.</div>
      <div class="row"><a class="btn sm primary" href="https://wa.me/${esc(phone)}?text=${encodeURIComponent(msg)}" target="_blank" rel="noopener">ابعته واتساب</a>
      <button class="btn sm" id="copy-pin">نسخ الرسالة</button></div></div>`;
  }

  async function renderEmployees() {
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>الموظفين والمناديب</h2><p>الكل بيدخل من الموبايل على <b dir="ltr">${esc(location.origin)}/pick/</b> برقم موبايله وكود من 4 أرقام. موظف التجهيز بيجهّز الفواتير ويستلم بضاعة الموردين، والمندوب بيشوف مشاويره بس.</p></div></div>
      <div class="card" style="margin-bottom:16px">
        <form id="e-form" class="row">
          <input type="text" id="e-name" placeholder="اسم الموظف" maxlength="60" required style="flex:1;min-width:160px">
          <input type="tel" id="e-phone" placeholder="موبايله (01xxxxxxxxx)" maxlength="20" required style="flex:1;min-width:160px" dir="ltr">
          <select id="e-role" style="width:auto"><option value="picker">تجهيز</option><option value="driver">مندوب</option><option value="both">تجهيز ومندوب</option></select>
          <button class="btn primary">إضافة موظف</button>
        </form>
        <div id="pin-box" style="margin-top:12px"></div>
      </div>
      <div id="e-list"><p class="muted">بيحمّل…</p></div>`;
    const showPin = (p) => {
      const box = view.querySelector('#pin-box');
      box.innerHTML = pinCard(p);
      box.querySelector('#copy-pin').onclick = (e) => run(e.target, async () => {
        await navigator.clipboard.writeText(`رابط تجهيز طلبات روز: ${location.origin}/pick/\nرقم الموبايل: ${p.phone}\nالكود السري: ${p.pin}`);
        toast('اتنسخت');
      });
    };
    view.querySelector('#e-form').onsubmit = (e) => {
      e.preventDefault();
      run(e.submitter, async () => {
        const p = await api('POST', `${API}/pickers`, { name: view.querySelector('#e-name').value, phone: view.querySelector('#e-phone').value, role: view.querySelector('#e-role').value });
        view.querySelector('#e-name').value = ''; view.querySelector('#e-phone').value = '';
        showPin(p);
        loadEmployees(showPin);
      });
    };
    loadEmployees(showPin);
  }

  async function loadEmployees(showPin) {
    const box = view.querySelector('#e-list');
    const list = await run(null, () => api('GET', `${API}/pickers`));
    if (!list || !box) return;
    box.innerHTML = list.length ? `<div class="table-wrap"><table>
      <thead><tr><th>الموظف</th><th>الدور</th><th>الحالة</th><th>فواتير النهارده</th><th>متوسط الوقت (30 يوم)</th><th>آخر 7 أيام</th><th>أخطاء تجهيز (30 يوم)</th><th>آخر دخول</th><th></th></tr></thead>
      <tbody>${list.map((p) => `<tr data-id="${p.id}">
        <td class="name-cell"><b>${esc(p.name)}</b><span class="meta num">${esc(p.phone)}</span></td>
        <td><select class="e-role" style="width:auto">${options(Object.entries(ROLE_LABEL).map(([v, l]) => ({ value: v, label: l })), p.role)}</select></td>
        <td>${!p.is_active ? '<span class="badge gray">موقوف</span>' : p.locked ? '<span class="badge red">مقفول (محاولات غلط)</span>' : p.working_on ? `<span class="badge amber">بيجهّز ${p.working_on}</span>` : '<span class="badge green">متاح</span>'}</td>
        <td class="num">${p.today.toLocaleString('ar-EG-u-nu-latn')}</td>
        <td class="num">${clockText(p.avg_seconds)}</td>
        <td class="num">${p.week.toLocaleString('ar-EG-u-nu-latn')}</td>
        <td class="num">${p.errors ? `<span class="badge red">${p.errors}</span>` : '0'}</td>
        <td class="small">${dateText(p.last_login_at)}</td>
        <td><div class="row" style="gap:4px"><button class="btn sm" data-pin>كود جديد</button>
          <button class="btn sm ${p.is_active ? 'danger' : 'primary'}" data-active="${p.is_active ? 0 : 1}">${p.is_active ? 'إيقاف' : 'تشغيل'}</button></div></td>
      </tr>`).join('')}</tbody></table></div>
      <p class="small muted">خطأ التجهيز = صنف رجع بسبب «صنف غلط» من طلب الموظف ده جهّزه.</p>`
      : '<div class="card empty">لسه مفيش موظفين. ضيف أول موظف من فوق.</div>';
    box.querySelectorAll('tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      tr.querySelector('.e-role').onchange = (e) => run(e.target, async () => { await api('POST', `${API}/pickers/${id}/role`, { role: e.target.value }); toast('الدور اتغيّر'); loadEmployees(showPin); });
      tr.querySelector('[data-pin]').onclick = (e) => {
        if (!confirm('الكود القديم هيبطل، والموظف هيخرج من الموبايل. تكمّل؟')) return;
        run(e.target, async () => { showPin(await api('POST', `${API}/pickers/${id}/reset-pin`)); loadEmployees(showPin); });
      };
      const act = tr.querySelector('[data-active]');
      act.onclick = () => {
        const on = act.dataset.active === '1';
        if (!on && !confirm('الموظف هيخرج، ولو كان بيجهّز طلب هيرجع للقايمة. تكمّل؟')) return;
        run(act, async () => { await api('POST', `${API}/pickers/${id}/active`, { active: on }); toast(on ? 'اتشغّل' : 'اتوقف'); loadEmployees(showPin); });
      };
    });
  }

  function pickingSection(d) {
    if (!['received', 'preparing', 'ready', 'delivered'].includes(d.status) && !d.pick_started_at) return '';
    let body = '';
    if (d.status === 'received') body = '<span class="muted">مستني موظف تجهيز يبدأ.</span>';
    else if (d.status === 'preparing') body = `<b>${esc(d.picker_name)}</b> بيجهّزه من ${dateText(d.pick_started_at)} <button class="btn sm" id="release-pick" style="margin-inline-start:8px">فك القفل</button>`;
    else if (d.pick_finished_at) body = `جهّزه <b>${esc(d.picker_name)}</b> في <b class="num">${clockText(d.pick_seconds)}</b> · ${dateText(d.pick_finished_at)}`;
    return body ? `<section class="card"><div class="row"><h3 style="flex:1">التجهيز</h3></div><div style="margin-top:6px">${body}</div></section>` : '';
  }

  // ============================================================ phase 6
  const ROLE_LABEL = { picker: 'تجهيز', driver: 'مندوب', both: 'تجهيز ومندوب' };

  async function renderDelivery() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const b = await run(null, () => api('GET', `${API}/delivery`));
    if (!b) return;
    state.zones = b.zones;
    state.zonesList = b.zones;
    const drivers = b.drivers.filter((d) => d.is_active);
    const driverOptions = (sel) => options(drivers.map((d) => ({ value: d.id, label: `${d.name} (${d.active_trips} مشوار)` })), sel, 'اختار المندوب');
    const row = (o, actions) => `
      <tr data-id="${o.id}">
        <td><a href="#" data-open class="num"><b>${o.number}</b></a></td>
        <td class="name-cell"><b>${esc(o.shop)}</b><span class="meta"><span class="num">${esc(o.phone)}</span>${o.zone ? ` · ${esc(o.zone)}` : ''}</span><span class="meta">${esc(o.address || 'مفيش عنوان')}</span></td>
        <td class="num"><b>${egp(o.grand)}</b><span class="per">${esc(o.payment_label)}</span>${o.blocked ? `<span class="badge red">${esc(o.blocked)}</span>` : ''}</td>
        <td>${actions}</td>
      </tr>`;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>التوصيل</h2><p>وزّع الطلبات الجاهزة على المناديب. المندوب بيستلم من التاجر كود من 4 أرقام قبل التسليم، والكاش بيفضل معاه لحد ما تستلمه.</p></div></div>
      ${!drivers.length ? '<div class="alert warn" style="margin-bottom:16px">مفيش مناديب. ضيف موظف بدور «مندوب» من قسم الموظفين.</div>' : ''}
      ${b.failed.length ? `<section class="card" style="border-color:#EBC1C8"><h3 style="margin-bottom:8px">ماتسلّمتش (${b.failed.length})</h3>
        <div class="table-wrap"><table><thead><tr><th>الطلب</th><th>التاجر</th><th>المبلغ</th><th>اللي حصل / مندوب تاني</th></tr></thead><tbody>
        ${b.failed.map((o) => row(o, `<div class="small" style="margin-bottom:6px"><b>${esc(o.driver_name)}</b>: ${esc(o.note)}</div><div class="row" style="gap:4px"><select class="d-driver" style="width:auto">${driverOptions(o.driver_id)}</select><input type="text" class="d-eta" placeholder="الميعاد" maxlength="60" style="width:120px"><button class="btn sm primary" data-assign>ابعته تاني</button></div>`)).join('')}
        </tbody></table></div></section>` : ''}
      <section class="card">
        <h3 style="margin-bottom:8px">جاهزة ومستنية مندوب (${b.waiting.length})</h3>
        ${b.waiting.length ? `<div class="table-wrap"><table><thead><tr><th>الطلب</th><th>التاجر</th><th>المبلغ</th><th>المندوب والوقت المتوقع</th></tr></thead><tbody>
        ${b.waiting.map((o) => row(o, `<div class="row" style="gap:4px"><select class="d-driver" style="width:auto">${driverOptions('')}</select><input type="text" class="d-eta" placeholder="مثلًا: خلال ساعة" maxlength="60" style="width:140px"><button class="btn sm primary" data-assign>سلّمه للمندوب</button></div>`)).join('')}
        </tbody></table></div>` : '<p class="muted">مفيش طلبات جاهزة.</p>'}
      </section>
      <section class="card">
        <h3 style="margin-bottom:8px">مع المناديب (${b.active.length})</h3>
        ${b.active.length ? `<div class="table-wrap"><table><thead><tr><th>الطلب</th><th>التاجر</th><th>المبلغ</th><th>المشوار</th></tr></thead><tbody>
        ${b.active.map((o) => row(o, `<div><b>${esc(o.driver_name)}</b> · <span class="badge ${o.delivery_status === 'assigned' ? 'gray' : 'amber'}">${esc(o.status_label)}</span>${o.eta ? `<span class="per">${esc(o.eta)}</span>` : ''}</div>
          <div class="small" style="margin:4px 0">كود الاستلام: <b class="num">${esc(o.code)}</b>${o.code_attempts ? ` · <span class="up">${o.code_attempts} محاولة غلط</span>` : ''}</div>
          <button class="btn sm" data-unassign>رجّعه من المندوب</button>`)).join('')}
        </tbody></table></div>` : '<p class="muted">مفيش.</p>'}
      </section>
      <div class="grid-2" style="margin-top:16px">
        <section class="card">
          <h3 style="margin-bottom:8px">الكاش مع المناديب</h3>
          ${b.drivers.length ? `<div class="table-wrap"><table><thead><tr><th>المندوب</th><th>معاه</th><th>سلّم النهارده</th><th></th></tr></thead><tbody>
          ${b.drivers.map((d) => `<tr data-driver="${d.id}"><td class="name-cell"><b>${esc(d.name)}</b><span class="meta num">${esc(d.phone)}</span></td>
            <td class="num"><b>${egp(d.cash_on_hand)}</b>${d.cash_orders ? `<span class="per">${d.cash_orders} طلب</span>` : ''}</td><td>${d.delivered_today}</td>
            <td>${d.cash_on_hand ? '<button class="btn sm primary" data-settle>استلمت الكاش</button>' : ''}</td></tr>`).join('')}
          </tbody></table></div>` : '<p class="muted">مفيش مناديب.</p>'}
        </section>
        <section class="card">
          <h3 style="margin-bottom:4px">مناطق التوصيل</h3>
          <p class="small muted" style="margin:0 0 8px">لكل منطقة رسوم توصيل وأقل طلب. التاجر اللي مالوش منطقة بيمشي على الإعدادات العامة. المنطقة بتتحدد للتاجر من قسم التجار.</p>
          <div class="table-wrap"><table><thead><tr><th>المنطقة</th><th>الرسوم</th><th>أقل طلب (0 = العام)</th><th>تجار</th><th>شغّالة</th><th></th></tr></thead><tbody>
          ${b.zones.map((z) => `<tr data-zone="${z.id}"><td><input type="text" class="z-name" value="${esc(z.name)}" maxlength="80"></td>
            <td><input type="number" class="z-fee" value="${z.fee}" min="0" step="5" style="width:90px"></td>
            <td><input type="number" class="z-min" value="${z.min_order}" min="0" step="50" style="width:110px"></td>
            <td>${z.traders}</td><td><input type="checkbox" class="z-on" ${z.is_active ? 'checked' : ''}></td>
            <td><button class="btn sm" data-zsave>حفظ</button></td></tr>`).join('')}
          </tbody></table></div>
          <form id="z-form" class="row" style="margin-top:8px"><input type="text" id="z-new" placeholder="اسم منطقة جديدة" maxlength="80" required style="flex:2;min-width:140px"><input type="number" id="z-fee" placeholder="الرسوم" min="0" step="5" style="flex:1;min-width:90px"><input type="number" id="z-min" placeholder="أقل طلب" min="0" step="50" style="flex:1;min-width:90px"><button class="btn primary">إضافة</button></form>
        </section>
      </div>`;
    view.querySelectorAll('tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      tr.querySelector('[data-open]').onclick = (e) => { e.preventDefault(); openOrder(Number(id)); };
      const assign = tr.querySelector('[data-assign]');
      if (assign) assign.onclick = () => run(assign, async () => {
        const driver = tr.querySelector('.d-driver').value;
        if (!driver) throw new Error('اختار المندوب');
        const r = await api('POST', `${API}/delivery/${id}/assign`, { driver_id: Number(driver), eta: tr.querySelector('.d-eta').value });
        toast(`اتسلّم للمندوب. كود الاستلام ${r.code}`);
        renderDelivery();
      });
      const un = tr.querySelector('[data-unassign]');
      if (un) un.onclick = () => { if (confirm('الطلب هيرجع لقايمة الجاهز. تكمّل؟')) run(un, async () => { await api('POST', `${API}/delivery/${id}/unassign`); renderDelivery(); }); };
    });
    view.querySelectorAll('tr[data-driver]').forEach((tr) => {
      const btn = tr.querySelector('[data-settle]');
      if (btn) btn.onclick = () => { if (confirm('تأكيد إنك استلمت كل الكاش اللي مع المندوب؟')) run(btn, async () => { const r = await api('POST', `${API}/drivers/${tr.dataset.driver}/settle`, {}); toast(`استلمت ${egp(r.amount)} من ${r.orders} طلب`); renderDelivery(); }); };
    });
    view.querySelectorAll('tr[data-zone]').forEach((tr) => {
      tr.querySelector('[data-zsave]').onclick = (e) => run(e.target, async () => {
        await api('PATCH', `${API}/zones/${tr.dataset.zone}`, { name: tr.querySelector('.z-name').value, fee: numOrNull(tr.querySelector('.z-fee').value) || 0, min_order: numOrNull(tr.querySelector('.z-min').value) || 0, is_active: tr.querySelector('.z-on').checked });
        toast('اتحفظ');
      });
    });
    view.querySelector('#z-form').onsubmit = (e) => {
      e.preventDefault();
      run(e.submitter, async () => {
        await api('POST', `${API}/zones`, { name: view.querySelector('#z-new').value, fee: numOrNull(view.querySelector('#z-fee').value) || 0, min_order: numOrNull(view.querySelector('#z-min').value) || 0 });
        toast('المنطقة اتضافت');
        renderDelivery();
      });
    };
  }

  function deliverySection(d) {
    if (!d.driver_id && !['ready', 'out_for_delivery'].includes(d.status)) return '';
    const label = { assigned: 'متسلّم للمندوب', out: 'خرج للتوصيل', arrived: 'المندوب وصل', failed: 'ماتسلّمش', delivered: 'اتسلّم' }[d.delivery_status] || 'مستني مندوب';
    return `<section class="card"><h3 style="margin-bottom:6px">التوصيل</h3>
      <div>${d.driver_name ? `المندوب <b>${esc(d.driver_name)}</b> · ` : ''}<span class="badge ${d.delivery_status === 'failed' ? 'red' : d.delivery_status === 'delivered' ? 'green' : 'amber'}">${label}</span>${d.delivery_code && d.status !== 'delivered' ? ` · كود الاستلام <b class="num">${esc(d.delivery_code)}</b>` : ''}</div>
      ${d.delivery_note ? `<div class="small" style="margin-top:4px">${esc(d.delivery_note)}</div>` : ''}
      ${d.delivery_address ? `<div class="small muted" style="margin-top:4px">${esc(d.delivery_address)}</div>` : ''}
      ${d.cash_collected ? `<div class="small" style="margin-top:4px">المندوب استلم ${egp(d.cash_collected)} كاش${d.settlement_id ? '، وسلّمهم' : '، ولسه معاه'}.</div>` : ''}
      ${['ready', 'out_for_delivery'].includes(d.status) ? '<div class="row" style="margin-top:8px"><button class="btn sm" id="go-delivery">قسم التوصيل</button></div>' : ''}</section>`;
  }

  // ============================================================ phase 7
  const RET_TABS = [['requested', 'محتاجة قرار'], ['accepted', 'مستنية الاستلام'], ['picked_up', 'مستنية الفحص'], ['inspected', 'مستنية رجوع الفلوس'], ['refunded', 'اتقفلت'], ['rejected', 'اترفضت'], ['', 'الكل']];
  const RET_BADGE = { requested: 'amber', accepted: 'pink', picked_up: 'pink', inspected: 'amber', refunded: 'green', rejected: 'gray' };

  async function renderReturns() {
    state.retStatus = state.retStatus == null ? 'requested' : state.retStatus;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>المرتجعات</h2><p>التاجر بيطلب الإرجاع في خلال ${esc(state.summary.settings.return_days || 15)} يوم من الاستلام. الفلوس بترجع كاش بس، أو بتتخصم من الآجل لو الطلب كان على الحساب.</p></div></div>
      <div class="chips" id="ret-tabs" style="margin-bottom:12px"></div>
      <div id="ret-list"><p class="muted">بيحمّل…</p></div>`;
    const data = await run(null, () => api('GET', `${API}/returns?${qs({ status: state.retStatus })}`));
    if (!data) return;
    view.querySelector('#ret-tabs').innerHTML = RET_TABS.map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${state.retStatus === k}">${l}${k && data.counts[k] ? ` <small>${data.counts[k]}</small>` : ''}</button>`).join('');
    view.querySelectorAll('#ret-tabs .chip').forEach((b) => (b.onclick = () => { state.retStatus = b.dataset.k; renderReturns(); }));
    const box = view.querySelector('#ret-list');
    box.innerHTML = data.items.length ? `<div class="table-wrap"><table><thead><tr><th>الطلب</th><th>التاجر</th><th>أصناف</th><th>الأسباب</th><th>القيمة</th><th>الحالة</th><th>الوقت</th></tr></thead><tbody>
      ${data.items.map((r) => `<tr class="clickable" data-id="${r.id}"><td class="num"><b>${r.order_number}</b></td><td class="name-cell"><b>${esc(r.shop_name || r.name)}</b><span class="meta num">${esc(r.phone)}</span></td>
        <td>${r.items}</td><td class="small">${esc(r.reasons)}</td><td class="num">${egp(r.status === 'refunded' ? r.refund_amount : r.max_refund)}</td>
        <td><span class="badge ${RET_BADGE[r.status]}">${esc(r.status_label)}</span></td><td class="small">${dateText(r.created_at)}</td></tr>`).join('')}
      </tbody></table></div>` : '<div class="card empty">مفيش.</div>';
    box.querySelectorAll('tr[data-id]').forEach((tr) => (tr.onclick = () => openReturn(Number(tr.dataset.id))));
  }

  async function openReturn(id) {
    openDrawer('<div class="drawer-head"><button class="btn" data-close>رجوع</button><b>بيحمّل…</b></div>');
    const r = await run(null, () => api('GET', `${API}/returns/${id}`));
    if (r) drawReturn(r);
  }

  async function drawReturn(r) {
    const drivers = r.status === 'requested' ? ((await run(null, () => api('GET', `${API}/delivery`))) || { drivers: [] }).drivers.filter((d) => d.is_active) : [];
    openDrawer(`
      <div class="drawer-head"><button class="btn" data-close>رجوع</button>
        <div style="flex:1"><b style="font-size:17px">مرتجع طلب <span class="num">${r.order_number}</span></b> <span class="badge ${RET_BADGE[r.status]}">${esc(r.status_label)}</span>
        <div class="small muted">${esc(r.customer.shop_name || r.customer.name)} · <span class="num">${esc(r.customer.phone)}</span> · ${dateText(r.created_at)}</div></div></div>
      <div class="drawer-body">
        ${r.note ? `<div class="alert info"><b>ملاحظة التاجر:</b> ${esc(r.note)}</div>` : ''}
        ${r.has_photo ? `<a class="btn sm" href="${API}/returns/${r.id}/photo" target="_blank" rel="noopener">شوف الصورة</a>` : ''}
        <section class="card"><div class="table-wrap"><table><thead><tr><th>الصنف</th><th>المورد</th><th>المرتجع</th><th>السبب</th><th>السعر</th><th>الفحص</th></tr></thead><tbody>
          ${r.items.map((i) => `<tr><td><b>${esc(i.name)}</b></td><td class="small">${esc(i.supplier)}</td><td>${i.qty} ${esc(i.unit_label)}</td><td><span class="badge ${i.reason === 'wrong_item' ? 'red' : 'gray'}">${esc(i.reason_label)}</span></td><td class="num">${egp(i.unit_price)}</td>
            <td class="small">${i.sound_qty == null ? '—' : `سليم ${i.sound_qty} · تالف ${i.damaged_qty}${i.missing_qty ? ` · <span class="up">ماوصلش ${i.missing_qty}</span>` : ''}`}</td></tr>`).join('')}
        </tbody></table></div>
        <p class="small muted" style="margin:8px 0 0">قيمة المطلوب إرجاعه ${egp(r.max_refund)}. «صنف غلط» بيتحسب خطأ تجهيز على الموظف اللي جهّز الطلب.</p></section>
        ${r.status === 'requested' ? `
          <section class="card stack"><h3>القرار</h3>
            <label class="check"><input type="radio" name="pk" value="driver" checked> مندوب يستلمها من التاجر</label>
            <select id="ret-driver">${options(drivers.map((d) => ({ value: d.id, label: d.name })), '', 'اختار المندوب')}</select>
            <label class="check"><input type="radio" name="pk" value="store"> التاجر هيجيبها للمحل</label>
            <div class="row"><button class="btn primary" id="ret-ok">قبول</button><span class="spacer"></span><button class="btn danger" id="ret-no">رفض</button></div>
          </section>` : ''}
        ${r.status === 'inspected' ? `
          <section class="card stack"><h3>رجوع الفلوس</h3>
            <p class="small muted" style="margin:0">المستحق بعد الفحص ${egp(r.suggested_refund)} (السليم والتالف، من غير اللي ماوصلش). ${r.payment_method === 'credit' ? 'الطلب كان على الحساب، فالمبلغ هيتخصم من الآجل الأول.' : 'هيرجع كاش.'}</p>
            <div class="row"><input type="number" id="ret-amount" min="0" max="${r.suggested_refund}" step="0.5" value="${r.suggested_refund}" style="max-width:180px"><button class="btn primary" id="ret-refund">رجّعت المبلغ</button></div>
            <p class="small muted" style="margin:0">نصيب المرتجع من نقاط الطلب هيتشال من رصيد التاجر.</p>
          </section>` : ''}
        ${r.status === 'refunded' ? `<div class="alert good">رجع ${r.refund_cash ? `${egp(r.refund_cash)} كاش` : ''}${r.refund_cash && r.refund_credit ? ' و' : ''}${r.refund_credit ? `${egp(r.refund_credit)} من الآجل` : ''}${!r.refund_amount ? 'مفيش مبلغ' : ''}${r.points_removed ? ` · اتشال ${r.points_removed} نقطة` : ''}.</div>` : ''}
        ${r.status === 'rejected' ? `<div class="alert bad">اترفض: ${esc(r.reject_reason)}</div>` : ''}
        ${['accepted', 'picked_up'].includes(r.status) ? `<div class="alert info">${r.status === 'accepted' ? (r.pickup === 'driver' ? `مستني المندوب ${esc(r.driver_name)} يستلمها.` : 'مستني التاجر يجيبها للمحل، وبعدين موظف التجهيز يفحصها.') : 'مستني موظف التجهيز يفحصها من الموبايل.'}</div>` : ''}
      </div>`);
    const P = drawerPanel;
    const ok = P.querySelector('#ret-ok');
    if (ok) ok.onclick = () => run(ok, async () => {
      const pickup = P.querySelector('[name=pk]:checked').value;
      const res = await api('POST', `${API}/returns/${r.id}/decide`, { approve: true, pickup, driver_id: pickup === 'driver' ? Number(P.querySelector('#ret-driver').value || 0) : null });
      toast('اتقبل، والتاجر اتبلّغ'); await loadSummary(); drawReturn(res); if (state.view === 'returns') renderReturns();
    });
    const no = P.querySelector('#ret-no');
    if (no) no.onclick = () => { const reason = prompt('سبب الرفض (هيوصل للتاجر):'); if (reason) run(no, async () => { const res = await api('POST', `${API}/returns/${r.id}/decide`, { approve: false, reason }); toast('اترفض'); await loadSummary(); drawReturn(res); if (state.view === 'returns') renderReturns(); }); };
    const refundBtn = P.querySelector('#ret-refund');
    if (refundBtn) refundBtn.onclick = () => { if (confirm('تأكيد إن المبلغ رجع للتاجر؟')) run(refundBtn, async () => { const res = await api('POST', `${API}/returns/${r.id}/refund`, { amount: numOrNull(P.querySelector('#ret-amount').value) || 0 }); toast('اتقفل'); await loadSummary(); drawReturn(res); if (state.view === 'returns') renderReturns(); }); };
  }

  async function renderRatings() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const d = await run(null, () => api('GET', `${API}/ratings`));
    if (!d) return;
    const max = Math.max(1, ...Object.values(d.distribution));
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>التقييمات</h2><p>تقييم عام لتجربة التاجر بعد ما يستلم، وبيوصلك إنت بس.</p></div></div>
      <div class="grid-2">
        <section class="card stack"><div class="row"><b style="font-size:40px" class="num">${d.average == null ? '—' : d.average}</b><span class="muted">من 5 · ${d.count} تقييم</span></div>
          ${[5, 4, 3, 2, 1].map((n) => `<div class="row" style="gap:8px"><span class="num" style="width:16px">${n}</span><div style="flex:1;background:#EFE9E7;border-radius:6px;height:12px"><div style="width:${(d.distribution[n] / max) * 100}%;background:#C98A2E;height:12px;border-radius:6px"></div></div><span class="num small" style="width:28px">${d.distribution[n]}</span></div>`).join('')}
        </section>
        <section class="card"><h3 style="margin-bottom:8px">اللي عجب التجار</h3>${d.liked.length ? d.liked.map(([k, n]) => `<div class="row" style="padding:4px 0;border-bottom:1px solid var(--line)"><span style="flex:1">${esc(k)}</span><b class="num">${n}</b></div>`).join('') : '<p class="muted">لسه مفيش.</p>'}</section>
      </div>
      <section class="card" style="margin-top:16px">${d.items.length ? `<div class="table-wrap"><table><thead><tr><th>التاجر</th><th>الطلب</th><th>التقييم</th><th>عجبه</th><th>عايزنا نحسّن</th><th>الوقت</th></tr></thead><tbody>
        ${d.items.map((r) => `<tr><td>${esc(r.shop_name || r.name)}</td><td class="num">${r.order_number}</td><td style="color:#C98A2E;white-space:nowrap">${'★'.repeat(r.stars)}<span style="color:#D9CDCA">${'★'.repeat(5 - r.stars)}</span></td><td class="small">${r.liked.map(esc).join('، ')}</td><td class="small">${esc(r.improve)}</td><td class="small">${dateText(r.created_at)}</td></tr>`).join('')}
      </tbody></table></div>` : '<p class="muted">لسه مفيش تقييمات.</p>'}</section>`;
  }

  const FB_KIND = { complaint: ['red', 'شكوى'], suggestion: ['green', 'اقتراح'], rating: ['amber', 'من تقييم'] };
  async function renderFeedback() {
    state.fbKind = state.fbKind || '';
    state.fbStatus = state.fbStatus || 'open';
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const d = await run(null, () => api('GET', `${API}/feedback?${qs({ kind: state.fbKind, status: state.fbStatus })}`));
    if (!d) return;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>الشكاوى والمقترحات</h2><p>ردّك بيوصل للتاجر كإشعار جوه شاشة الجملة.</p></div></div>
      <div class="row" style="margin-bottom:12px"><div class="chips">${[['', 'الكل'], ['complaint', 'شكاوى'], ['suggestion', 'مقترحات'], ['rating', 'من التقييمات']].map(([k, l]) => `<button class="chip" data-kind="${k}" aria-pressed="${state.fbKind === k}">${l}</button>`).join('')}</div><span class="spacer"></span>
        <div class="chips">${[['open', 'مفتوحة'], ['resolved', 'اتحلّت'], ['', 'الكل']].map(([k, l]) => `<button class="chip" data-status="${k}" aria-pressed="${state.fbStatus === k}">${l}</button>`).join('')}</div></div>
      ${d.items.length ? d.items.map((f) => `
        <section class="card stack" data-id="${f.id}">
          <div class="row"><span class="badge ${FB_KIND[f.kind][0]}">${FB_KIND[f.kind][1]}</span><b>${esc(f.shop_name || f.name)}</b><span class="small muted num">${esc(f.phone)}</span>${f.order_number ? `<span class="small">طلب <span class="num">${f.order_number}</span></span>` : ''}${f.stars ? `<span style="color:#C98A2E">${'★'.repeat(f.stars)}</span>` : ''}<span class="spacer"></span><span class="small muted">${dateText(f.created_at)}</span></div>
          <div>${esc(f.text)}</div>
          ${f.reply ? `<div class="alert good small">ردّك: ${esc(f.reply)}</div>` : ''}
          <div class="row"><input type="text" class="fb-reply" maxlength="1000" placeholder="اكتب رد للتاجر" style="flex:1;min-width:200px"><button class="btn sm" data-reply>ابعت الرد</button>
            ${f.status === 'open' ? '<button class="btn sm primary" data-resolve>علّمها اتحلّت</button>' : '<button class="btn sm" data-reopen>افتحها تاني</button>'}</div>
        </section>`).join('') : '<div class="card empty">مفيش.</div>'}`;
    view.querySelectorAll('[data-kind]').forEach((b) => (b.onclick = () => { state.fbKind = b.dataset.kind; renderFeedback(); }));
    view.querySelectorAll('[data-status]').forEach((b) => (b.onclick = () => { state.fbStatus = b.dataset.status; renderFeedback(); }));
    view.querySelectorAll('section[data-id]').forEach((sec) => {
      const id = sec.dataset.id;
      sec.querySelector('[data-reply]').onclick = (e) => run(e.target, async () => { const reply = sec.querySelector('.fb-reply').value.trim(); if (!reply) throw new Error('اكتب الرد'); await api('POST', `${API}/feedback/${id}`, { reply }); toast('الرد وصل للتاجر'); renderFeedback(); });
      const res = sec.querySelector('[data-resolve]');
      if (res) res.onclick = () => run(res, async () => { const reply = sec.querySelector('.fb-reply').value.trim(); await api('POST', `${API}/feedback/${id}`, { reply: reply || null, resolve: true }); toast('اتقفلت'); await loadSummary(); renderFeedback(); });
      const reo = sec.querySelector('[data-reopen]');
      if (reo) reo.onclick = () => run(reo, async () => { await api('POST', `${API}/feedback/${id}`, {}); await loadSummary(); renderFeedback(); });
    });
  }

  // ============================================================ phase 8
  const n0 = (v) => Number(v || 0).toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 1 });
  const dayText = (iso) => (iso ? new Date(iso).toLocaleDateString('ar-EG-u-nu-latn', { dateStyle: 'medium' }) : '—');

  async function renderPlus() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const d = await run(null, () => api('GET', `${API}/plus`));
    if (!d) return;
    const s = d.settings;
    const traders = (await api('GET', `${API}/trader-accounts?status=approved`).catch(() => [])) || [];
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>روز بلس</h2><p>اشتراك شهري أو 3 شهور: التوصيل مجاني، وجزء بس من عمولة الدفع، وطلبات المشترك بتتجهز الأول. الأسعار من «الإعدادات».</p></div></div>
      ${s.plus_enabled !== '1' ? `<div class="alert warn" style="margin-bottom:16px">روز بلس مقفول دلوقتي، والتجار مش شايفينه. شغّله وحط الأسعار من «الإعدادات».</div>` : ''}
      <div class="stats">
        <div class="stat good"><b class="num">${n0(d.stats.active)}</b><span>مشترك دلوقتي</span></div>
        <div class="stat ${d.stats.ending_soon ? 'warn' : ''}"><b class="num">${n0(d.stats.ending_soon)}</b><span>بيخلص خلال 3 أيام</span></div>
        <div class="stat"><b class="num">${egp(d.stats.revenue_month)}</b><span>اشتراكات الشهر ده</span></div>
        <div class="stat"><b class="num">${egp(d.stats.saved_month)}</b><span>وفّرهم المشتركين الشهر ده</span></div>
      </div>
      <section class="card" style="margin-bottom:16px"><h3 style="margin-bottom:8px">طلبات الاشتراك</h3>
        ${d.requests.length ? `<div class="table-wrap"><table><thead><tr><th>التاجر</th><th>المدة</th><th>المبلغ</th><th>الدفع</th><th>الوقت</th><th></th></tr></thead><tbody>
          ${d.requests.map((r) => `<tr data-id="${r.id}"><td class="name-cell"><b>${esc(r.shop_name || r.name)}</b><span class="meta num">${esc(r.phone)}</span></td><td>${esc(r.plan_label)}</td><td class="num">${egp(r.price)}</td>
            <td>${esc(r.method_label)}${r.sender ? `<div class="small muted">${esc(r.sender)}</div>` : ''}${r.has_proof ? ` <a class="small" href="${API}/plus/${r.id}/proof" target="_blank" rel="noopener">صورة التحويل</a>` : ''}</td>
            <td class="small">${dateText(r.created_at)}</td>
            <td><div class="row" style="flex-wrap:nowrap"><button class="btn sm primary" data-ok>${r.method === 'cash' ? 'استلمت الكاش، فعّل' : 'التحويل وصل، فعّل'}</button><button class="btn sm danger" data-no>رفض</button></div></td></tr>`).join('')}
        </tbody></table></div>` : '<p class="muted" style="margin:0">مفيش طلبات مستنية.</p>'}
      </section>
      <section class="card" style="margin-bottom:16px"><h3 style="margin-bottom:8px">المشتركين</h3>
        ${d.active.length ? `<div class="table-wrap"><table><thead><tr><th>التاجر</th><th>المدة</th><th>من</th><th>لحد</th><th>باقي</th><th></th></tr></thead><tbody>
          ${d.active.map((r) => `<tr data-id="${r.id}"><td class="name-cell"><b>${esc(r.shop_name || r.name)}</b><span class="meta num">${esc(r.phone)}</span></td><td>${esc(r.plan_label)}</td><td class="small">${dayText(r.starts_at)}</td><td class="small">${dayText(r.ends_at)}</td>
            <td>${r.days_left <= 3 ? `<span class="badge amber">${n0(r.days_left)} يوم</span>` : `<span class="num">${n0(r.days_left)} يوم</span>`}</td>
            <td><button class="btn sm ghost danger" data-cancel>إلغاء</button></td></tr>`).join('')}
        </tbody></table></div>` : '<p class="muted" style="margin:0">لسه مفيش مشتركين.</p>'}
      </section>
      <section class="card stack"><h3>اشتراك من الإدارة</h3>
        <p class="small muted" style="margin:0">لتاجر دفع في المحل من غير ما يطلب من الموبايل. لو عنده اشتراك شغّال، الجديد بيبدأ بعده.</p>
        <div class="row">
          <select id="pg-trader" style="flex:2;min-width:200px">${options(traders.map((t) => ({ value: t.customer_id, label: `${t.shop_name || t.name} · ${t.phone}` })), '', 'اختار التاجر')}</select>
          <select id="pg-plan" style="flex:1;min-width:120px"><option value="month">شهر</option><option value="quarter">3 شهور</option></select>
          <label class="check"><input type="checkbox" id="pg-paid" checked> دفع سعر الاشتراك</label>
          <button class="btn primary" id="pg-go">فعّل</button>
        </div>
      </section>`;
    view.querySelectorAll('tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      const ok = tr.querySelector('[data-ok]');
      if (ok) ok.onclick = () => run(ok, async () => { await api('POST', `${API}/plus/${id}/decide`, { approve: true }); toast('الاشتراك اتفعّل، والتاجر اتبلّغ'); await loadSummary(); renderPlus(); });
      const no = tr.querySelector('[data-no]');
      if (no) no.onclick = () => { const reason = prompt('سبب الرفض (هيوصل للتاجر):'); if (reason) run(no, async () => { await api('POST', `${API}/plus/${id}/decide`, { approve: false, reason }); toast('اترفض'); await loadSummary(); renderPlus(); }); };
      const cancel = tr.querySelector('[data-cancel]');
      if (cancel) cancel.onclick = () => { if (confirm('تلغي الاشتراك ده؟ المزايا هتقف على الطلبات الجديدة.')) run(cancel, async () => { await api('POST', `${API}/plus/${id}/cancel`); toast('اتلغى'); renderPlus(); }); };
    });
    view.querySelector('#pg-go').onclick = (e) => run(e.target, async () => {
      const customer = Number(view.querySelector('#pg-trader').value || 0);
      if (!customer) throw new Error('اختار التاجر');
      await api('POST', `${API}/plus/grant`, { customer_id: customer, plan: view.querySelector('#pg-plan').value, paid: view.querySelector('#pg-paid').checked });
      toast('الاشتراك اتفعّل'); renderPlus();
    });
  }

  // ---------------------------------------------------------------- reports
  const PERIODS = [['day', 'يومي'], ['month', 'شهري'], ['year', 'سنوي']];
  const compact = (v) => (v >= 1000 ? `${n0(v / 1000)} ألف` : n0(v));

  function changeBadge(c, invert) {
    if (c == null) return '<span class="chg flat">مفيش مقارنة</span>';
    if (c === 0) return '<span class="chg flat">زي اللي قبلها</span>';
    const up = c > 0;
    const good = invert ? !up : up;
    return `<span class="chg ${good ? 'up' : 'down'}">${up ? '▲' : '▼'} ${n0(Math.abs(c))}٪</span>`;
  }

  function barChart(points, unit) {
    const max = Math.max(...points.map((p) => p.sales), 0);
    if (!max) return '<div class="chart-empty">مفيش مبيعات في الفترة دي.</div>';
    const step = Math.pow(10, Math.floor(Math.log10(max)));
    const top = Math.ceil(max / step) * step;
    const ticks = [0, top / 2, top];
    const every = points.length > 24 ? 5 : points.length > 12 ? 3 : 1;
    const peak = points.reduce((a, p, i) => (p.sales > points[a].sales ? i : a), 0);
    return `<div class="chart" role="img" aria-label="المبيعات ${unit}">
      <div class="chart-plot">
        ${ticks.map((t) => `<div class="chart-grid" style="bottom:${(t / top) * 100}%"><span>${compact(t)}</span></div>`).join('')}
        <div class="chart-cols">${points.map((p, i) => `
          <div class="chart-col" data-i="${i}" tabindex="0" aria-label="${esc(p.label)}: ${egp(p.sales)}، ${n0(p.orders)} طلب">
            ${i === peak ? `<span class="chart-peak num" style="bottom:calc(${(p.sales / top) * 100}% + 4px)">${compact(p.sales)}</span>` : ''}
            <span class="chart-bar" style="height:${(p.sales / top) * 100}%"></span></div>`).join('')}
        </div>
        <div class="chart-tip" hidden></div>
      </div>
      <div class="chart-x">${points.map((p, i) => `<span>${i % every === 0 ? esc(p.label) : ''}</span>`).join('')}</div>
    </div>`;
  }

  function bindChart(box, points, unitWord) {
    const tip = box.querySelector('.chart-tip');
    if (!tip) return;
    const plot = box.querySelector('.chart-plot');
    const show = (col) => {
      const p = points[Number(col.dataset.i)];
      tip.innerHTML = `<b>${esc(unitWord)} ${esc(p.label)}</b><br>${egp(p.sales)} · ${n0(p.orders)} طلب`;
      tip.hidden = false;
      const r = col.getBoundingClientRect(), pr = plot.getBoundingClientRect();
      const x = Math.min(Math.max(r.left - pr.left + r.width / 2, 70), pr.width - 70);
      tip.style.left = `${x}px`;
    };
    box.querySelectorAll('.chart-col').forEach((col) => {
      col.onmouseenter = () => show(col); col.onfocus = () => show(col);
      col.onmouseleave = () => { tip.hidden = true; }; col.onblur = () => { tip.hidden = true; };
    });
  }

  async function renderReports() {
    state.repTab = state.repTab || 'store';
    state.repPeriod = state.repPeriod || 'month';
    const isStore = state.repTab === 'store';
    const params = { period: state.repPeriod, ref: state.repRef || '' };
    if (!isStore && state.repSupplier) params.supplier_id = state.repSupplier;
    view.innerHTML = `
      <div class="ws-section-head no-print"><div><h2>التقارير</h2><p>المبيعات بتتحسب يوم ما الطلب يتسلّم، بقيمته النهائية بعد المراجعة. المرتجع بيتحسب يوم ما الفلوس ترجع.</p></div></div>
      <div class="row no-print" style="margin-bottom:12px">
        <div class="chips" role="group"><button class="chip" data-tab="store" aria-pressed="${isStore}">المتجر</button><button class="chip" data-tab="suppliers" aria-pressed="${!isStore}">الموردين</button></div>
        <div class="chips" role="group">${PERIODS.map(([k, l]) => `<button class="chip" data-period="${k}" aria-pressed="${state.repPeriod === k}">${l}</button>`).join('')}</div>
        <span class="spacer"></span>
        <button class="btn sm" id="rep-xlsx">تنزيل Excel</button><button class="btn sm" id="rep-print">طباعة</button>
      </div>
      <div id="rep-body"><p class="muted">بيحمّل…</p></div>`;
    view.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { state.repTab = b.dataset.tab; state.repSupplier = null; renderReports(); }));
    view.querySelectorAll('[data-period]').forEach((b) => (b.onclick = () => { state.repPeriod = b.dataset.period; state.repRef = ''; renderReports(); }));
    view.querySelector('#rep-print').onclick = () => window.print();
    view.querySelector('#rep-xlsx').onclick = () => { location.href = `${API}/reports${isStore ? '' : '/suppliers'}/export?${qs(params)}`; };
    const data = await run(null, () => api('GET', `${API}/reports${isStore ? '' : '/suppliers'}?${qs(params)}`));
    if (!data) return;
    const body = view.querySelector('#rep-body');
    const rg = data.range;
    const nav = `<div class="rep-nav"><button class="btn sm no-print" id="rep-prev" aria-label="الفترة اللي قبلها">‹ اللي قبلها</button><h3 class="rep-title">${isStore ? 'تقرير الجملة' : 'تقرير الموردين'}: ${esc(rg.title)}</h3><button class="btn sm no-print" id="rep-next" ${rg.next_key ? '' : 'disabled'}>اللي بعدها ›</button></div>`;
    if (isStore) {
      const m = data.metrics, c = data.compare;
      const unitWord = { day: 'الساعة', month: 'يوم', year: '' }[rg.period];
      const kpis = [['sales', 'المبيعات', egp], ['orders', 'طلبات اتسلّمت', n0], ['average', 'متوسط الطلب', egp], ['profit', 'الربح (بيع - شراء)', egp], ['returns', 'المرتجعات', egp, true], ['net', 'الصافي بعد المرتجع', egp]];
      body.innerHTML = `${nav}
        <div class="kpis">${kpis.map(([k, l, f, inv]) => `<div class="kpi"><span>${l}</span><b class="num">${f(c[k].now)}</b>${changeBadge(c[k].change, inv)}<small>اللي قبلها ${f(c[k].before)}</small></div>`).join('')}</div>
        <section class="card" style="margin-bottom:16px"><h3 style="margin-bottom:4px">المبيعات ${{ day: 'بالساعة', month: 'باليوم', year: 'بالشهر' }[rg.period]}</h3>${barChart(data.chart, unitWord)}</section>
        <div class="stats">
          <div class="stat"><b class="num">${n0(m.new_orders)}</b><span>طلبات جديدة اتبعتت</span></div>
          <div class="stat"><b class="num">${n0(m.cancelled)}</b><span>طلبات اتلغت</span></div>
          <div class="stat"><b class="num">${n0(m.returns_count)}</b><span>مرتجعات رجعت فلوسها</span></div>
          <div class="stat"><b class="num">${n0(m.shortages)}</b><span>نواقص اتسجّلت</span></div>
          <div class="stat"><b class="num">${n0(m.requests)}</b><span>طلبات توفير</span></div>
          <div class="stat"><b class="num">${egp(m.delivery_fees)}</b><span>رسوم توصيل</span></div>
          <div class="stat"><b class="num">${egp(m.plus_revenue)}</b><span>اشتراكات روز بلس</span></div>
          <div class="stat"><b class="num">${n0(m.new_traders)}</b><span>تجار جداد</span></div>
        </div>
        <div class="grid-2">
          <section class="card"><h3 style="margin-bottom:8px">أكتر الأصناف</h3>${data.top_products.length ? `<div class="table-wrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المبيعات</th><th>الربح</th></tr></thead><tbody>
            ${data.top_products.map((p) => `<tr><td>${esc(p.name)}</td><td>${n0(p.qty)} ${esc(p.unit_label)}</td><td class="num">${egp(p.value)}</td><td class="num">${egp(p.profit)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">مفيش.</p>'}</section>
          <section class="card"><h3 style="margin-bottom:8px">أكتر التجار</h3>${data.top_traders.length ? `<div class="table-wrap"><table><thead><tr><th>التاجر</th><th>الطلبات</th><th>المبيعات</th></tr></thead><tbody>
            ${data.top_traders.map((t) => `<tr><td class="name-cell"><b>${esc(t.shop_name || t.name)}</b><span class="meta num">${esc(t.phone)}</span></td><td class="num">${n0(t.orders)}</td><td class="num">${egp(t.value)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">مفيش.</p>'}</section>
          <section class="card"><h3 style="margin-bottom:8px">المبيعات حسب الوحدة</h3>
            ${data.by_unit.map((u) => `<div class="share-row"><span>${esc(u.label)}</span><div class="share-track"><div class="share-fill" style="width:${u.share}%"></div></div><span class="num">${n0(u.share)}٪</span><span class="num small muted">${egp(u.value)}</span></div>`).join('')}
            <h3 style="margin:14px 0 8px">طرق الدفع</h3>${data.by_method.length ? data.by_method.map((x) => `<div class="row small" style="padding:4px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(x.label)}</span><span class="num">${n0(x.orders)} طلب</span><b class="num" style="min-width:100px;text-align:left">${egp(x.value)}</b></div>`).join('') : '<p class="muted">مفيش.</p>'}</section>
          <section class="card"><h3 style="margin-bottom:8px">أكتر النواقص</h3>${data.top_shortages.length ? data.top_shortages.map((x) => `<div class="row small" style="padding:4px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(x.name)}${x.is_request ? ' <span class="badge pink">طلب توفير</span>' : ''}</span><span class="num">${n0(x.times)} مرة · ${n0(x.traders)} تاجر</span></div>`).join('') : '<p class="muted">مفيش.</p>'}</section>
        </div>`;
      bindChart(body, data.chart, unitWord);
    } else {
      const t = data.totals;
      const d = data.detail;
      body.innerHTML = `${nav}
        <div class="stats">
          <div class="stat"><b class="num">${egp(t.cost)}</b><span>تكلفة الشراء</span></div>
          <div class="stat"><b class="num">${egp(t.sales)}</b><span>المبيعات</span></div>
          <div class="stat good"><b class="num">${egp(t.profit)}</b><span>الربح</span></div>
          <div class="stat ${t.unavailable ? 'warn' : ''}"><b class="num">${n0(t.unavailable)}</b><span>أصناف ماكانتش متوفرة</span></div>
          <div class="stat ${t.damaged_qty ? 'bad' : ''}"><b class="num">${egp(t.damaged_cost)}</b><span>تالف رجع (بسعر الشراء)</span></div>
        </div>
        <section class="card" style="margin-bottom:16px"><div class="table-wrap"><table><thead><tr><th>المورد</th><th>طلبات</th><th>تكلفة</th><th>مبيعات</th><th>الربح</th><th>الهامش</th><th>نسبة التوفير</th><th>مش متوفر</th><th>ناقص في الاستلام</th><th>تالف رجع</th></tr></thead><tbody>
          ${data.suppliers.map((s) => `<tr class="clickable${state.repSupplier === s.id ? ' on' : ''}" data-sup="${s.id}"><td><b>${esc(s.alias)}</b>${s.is_active ? '' : ' <span class="badge gray">موقوف</span>'}</td><td class="num">${n0(s.orders)}</td><td class="num">${egp(s.cost)}</td><td class="num">${egp(s.sales)}</td><td class="num">${egp(s.profit)}</td><td class="num">${n0(s.margin)}٪</td>
            <td>${s.fill_rate == null ? '—' : `<span class="badge ${s.fill_rate >= 95 ? 'green' : s.fill_rate >= 80 ? 'amber' : 'red'}">${n0(s.fill_rate)}٪</span>`}</td><td class="num">${n0(s.unavailable)}${s.partial ? ` <span class="small muted">+${n0(s.partial)} جزئي</span>` : ''}</td><td class="num">${n0(s.short_on_receipt)}</td>
            <td class="num">${s.damaged_qty ? `${n0(s.damaged_qty)} · ${egp(s.damaged_cost)}` : '0'}</td></tr>`).join('')}
        </tbody></table></div><p class="small muted" style="margin:8px 0 0">نسبة التوفير = الأصناف اللي المورد وفّرها من اللي اتطلبت منه. التالف بسعر الشراء علشان تحاسب المورد عليه. دوس على مورد لتفاصيله.</p></section>
        ${d ? `
          <h3 style="margin-bottom:10px">${esc(d.supplier.alias)}</h3>
          <div class="grid-2">
            <section class="card"><h3 style="margin-bottom:8px">اللي اتباع من عنده</h3>${d.products.length ? `<div class="table-wrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>تكلفة</th><th>مبيعات</th></tr></thead><tbody>
              ${d.products.map((p) => `<tr><td>${esc(p.name)}${p.supplier_code ? `<div class="small muted">${esc(p.supplier_code)}</div>` : ''}</td><td>${n0(p.qty)} ${esc(p.unit_label)}</td><td class="num">${egp(p.cost)}</td><td class="num">${egp(p.value)}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">مفيش.</p>'}</section>
            <section class="card"><h3 style="margin-bottom:8px">ماكانش متوفر</h3>${d.missing.length ? d.missing.map((p) => `<div class="row small" style="padding:4px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(p.name)}</span><span class="num">${n0(p.times)} مرة · ${n0(p.qty)} ${esc(p.unit_label)}</span></div>`).join('') : '<p class="muted">كل اللي اتطلب كان متوفر.</p>'}
              <h3 style="margin:14px 0 8px">تالف رجع من التجار</h3>${d.damaged.length ? d.damaged.map((x) => `<div class="row small" style="padding:4px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(x.name)} · طلب <span class="num">${x.order_number}</span></span><span class="num">${n0(x.qty)} ${esc(x.unit_label)} · ${egp(x.cost)}</span></div>`).join('') : '<p class="muted">مفيش.</p>'}</section>
          </div>` : ''}`;
      body.querySelectorAll('[data-sup]').forEach((tr) => (tr.onclick = () => { state.repSupplier = state.repSupplier === Number(tr.dataset.sup) ? null : Number(tr.dataset.sup); renderReports(); }));
    }
    body.querySelector('#rep-prev').onclick = () => { state.repRef = rg.prev_key; renderReports(); };
    const next = body.querySelector('#rep-next');
    if (rg.next_key) next.onclick = () => { state.repRef = rg.next_key; renderReports(); };
  }

  // ============================================================ phase 10
  async function renderChatReports() {
    state.crStatus = state.crStatus == null ? 'open' : state.crStatus;
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const d = await run(null, () => api('GET', `${API}/chat-reports?${qs({ status: state.crStatus })}`));
    if (!d) return;
    const st = d.stats;
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>المحادثات والبلاغات</h2><p>المحادثات خاصة بين التجار. هنا بتشوف بس الرسالة اللي اتبلّغ عنها واللي حواليها، وتقدر توقف المحادثات عن تاجر.</p></div></div>
      <div class="stats">
        <div class="stat"><b class="num">${n0(st.visible)}</b><span>تاجر ظاهر للقريبين</span></div>
        <div class="stat"><b class="num">${n0(st.threads)}</b><span>محادثة</span></div>
        <div class="stat"><b class="num">${n0(st.open_splits)}</b><span>تقسيم كرتونة مفتوح</span></div>
        <div class="stat good"><b class="num">${n0(st.ordered_splits)}</b><span>تقسيم اتطلب</span></div>
        <div class="stat ${st.open_reports ? 'warn' : ''}"><b class="num">${n0(st.open_reports)}</b><span>بلاغ مفتوح</span></div>
      </div>
      <div class="chips" style="margin-bottom:12px">${[['open', 'مفتوحة'], ['closed', 'اتقفلت'], ['', 'الكل']].map(([k, l]) => `<button class="chip" data-crs="${k}" aria-pressed="${state.crStatus === k}">${l}</button>`).join('')}</div>
      ${d.items.length ? d.items.map((r) => `
        <section class="card stack" data-rid="${r.id}" style="margin-bottom:12px">
          <div class="row"><span class="badge ${r.status === 'open' ? 'red' : 'gray'}">${r.status === 'open' ? 'مفتوح' : 'اتقفل'}</span>
            <b>بلاغ على ${esc(r.reported.shop_name || r.reported.name)}</b><span class="small muted num">${esc(r.reported.phone)}</span>
            ${r.reported.reports > 1 ? `<span class="badge amber">${n0(r.reported.reports)} بلاغات عليه</span>` : ''}${r.reported.suspended ? '<span class="badge red">المحادثات واقفة</span>' : ''}
            <span class="spacer"></span><span class="small muted">${dateText(r.created_at)}</span></div>
          <div class="small">من: ${esc(r.reporter.shop_name || r.reporter.name)} · السبب: <b>${esc(r.reason)}</b></div>
          <div class="report-context">${r.context.map((m) => `<div class="rc ${m.from}${m.flagged ? ' flagged' : ''}"><span class="small muted">${m.from === 'reported' ? esc(r.reported.shop_name || r.reported.name) : m.from === 'reporter' ? esc(r.reporter.shop_name || r.reporter.name) : 'النظام'}</span>${esc(m.body)}</div>`).join('') || '<p class="small muted">مفيش رسايل.</p>'}</div>
          ${r.admin_note ? `<div class="alert info small">ملاحظتك: ${esc(r.admin_note)}</div>` : ''}
          <div class="row">
            ${r.status === 'open' ? '<input type="text" class="cr-note" maxlength="300" placeholder="ملاحظة (اختياري)" style="flex:1;min-width:180px"><button class="btn sm" data-close>اقفل البلاغ</button>' : ''}
            <button class="btn sm ${r.reported.suspended ? '' : 'danger'}" data-suspend="${r.reported.id}" data-on="${r.reported.suspended ? 0 : 1}">${r.reported.suspended ? 'رجّع المحادثات' : 'وقّف المحادثات عنه'}</button>
          </div>
        </section>`).join('') : '<div class="card empty">مفيش بلاغات.</div>'}
      ${d.suspended.length ? `<section class="card" style="margin-top:16px"><h3 style="margin-bottom:8px">تجار المحادثات واقفة عندهم</h3>${d.suspended.map((s) => `<div class="row" style="padding:6px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(s.shop_name || s.name)} <span class="small muted num">${esc(s.phone)}</span></span><button class="btn sm" data-suspend="${s.customer_id}" data-on="0">رجّع المحادثات</button></div>`).join('')}</section>` : ''}`;
    view.querySelectorAll('[data-crs]').forEach((b) => (b.onclick = () => { state.crStatus = b.dataset.crs; renderChatReports(); }));
    view.querySelectorAll('section[data-rid]').forEach((sec) => {
      const close = sec.querySelector('[data-close]');
      if (close) close.onclick = () => run(close, async () => { await api('POST', `${API}/chat-reports/${sec.dataset.rid}/close`, { note: sec.querySelector('.cr-note').value.trim() }); toast('البلاغ اتقفل'); await loadSummary(); renderChatReports(); });
    });
    view.querySelectorAll('[data-suspend]').forEach((b) => (b.onclick = () => {
      const on = b.dataset.on === '1';
      if (on && !confirm('توقف المحادثات عن التاجر ده؟ هيوصله إشعار، وهيختفي من «تجار قريبين».')) return;
      run(b, async () => { await api('POST', `${API}/chat-suspend/${b.dataset.suspend}`, { on }); toast(on ? 'المحادثات اتوقفت عنه' : 'المحادثات رجعت'); renderChatReports(); });
    }));
  }

  // ======================================================= standalone app
  const logoutBtn = document.getElementById('staff-logout');
  if (logoutBtn) logoutBtn.onclick = async () => { await fetch('/api/staff/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {}); location.href = '/staff-login/'; };

  async function renderPoints() {
    state.codeStatus = state.codeStatus == null ? 'pending' : state.codeStatus;
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const [rewards, codes] = await Promise.all([
      run(null, () => api('GET', `${API}/rewards`)),
      run(null, () => api('GET', `${API}/redemptions?${qs({ status: state.codeStatus, code: state.codeQ || '' })}`)),
    ]);
    if (!rewards || !codes) return;
    const rate = Number(state.summary.settings.points_egp_per_point || 0);
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>نقاط الجملة والمكافآت</h2><p>${rate ? `التاجر بياخد نقطة على كل ${n0(rate)} ج بعد التسليم (من «الإعدادات»).` : 'النقاط مقفولة. حط «نقطة لكل كام جنيه» من «الإعدادات».'} النقاط دي خاصة بالجملة بس.</p></div></div>
      <div class="grid-2">
        <section class="card stack"><h3>كود مكافأة</h3>
          <div class="row"><input type="search" id="code-q" inputmode="numeric" maxlength="6" placeholder="اكتب الكود اللي مع التاجر" value="${esc(state.codeQ || '')}" style="flex:1"><button class="btn" id="code-go">دوّر</button></div>
          <div class="chips">${[['pending', 'مستني الاستخدام'], ['used', 'اتستخدم'], ['cancelled', 'اتلغى'], ['', 'الكل']].map(([k, l]) => `<button class="chip" data-cs="${k}" aria-pressed="${state.codeStatus === k}">${l}</button>`).join('')}</div>
          ${codes.length ? `<div class="table-wrap"><table><thead><tr><th>الكود</th><th>التاجر</th><th>المكافأة</th><th>الحالة</th><th></th></tr></thead><tbody>
            ${codes.map((d) => `<tr data-rid="${d.id}"><td class="num"><b dir="ltr">${esc(d.code)}</b></td><td class="name-cell"><b>${esc(d.shop_name || d.name)}</b><span class="meta num">${esc(d.phone)}</span></td><td>${esc(d.reward_name)}<div class="small muted">${n0(d.points)} نقطة · ${dateText(d.created_at)}</div></td>
              <td><span class="badge ${d.status === 'pending' ? 'amber' : d.status === 'used' ? 'green' : 'gray'}">${esc(d.status_label)}</span></td>
              <td>${d.status === 'pending' ? '<div class="row" style="gap:4px;flex-wrap:nowrap"><button class="btn sm primary" data-use>اتسلّمت</button><button class="btn sm" data-cancel>إلغاء ورجّع النقاط</button></div>' : ''}</td></tr>`).join('')}
          </tbody></table></div>` : '<p class="muted" style="margin:0">مفيش أكواد.</p>'}
        </section>
        <section class="card stack"><h3>المكافآت</h3>
          ${rewards.map((w) => `<div class="row reward-row" data-wid="${w.id}" style="border-top:1px solid var(--line);padding-top:8px">
            <input type="text" class="w-name" value="${esc(w.name)}" maxlength="80" style="flex:2;min-width:140px">
            <input type="number" class="w-cost" value="${w.cost_points}" min="1" style="width:100px" aria-label="النقاط">
            <input type="number" class="w-stock" value="${w.stock == null ? '' : w.stock}" min="0" placeholder="غير محدود" style="width:110px" aria-label="الكمية">
            <label class="check small"><input type="checkbox" class="w-active" ${w.is_active ? 'checked' : ''}> ظاهرة</label>
            <button class="btn sm" data-save>حفظ</button></div>`).join('') || '<p class="muted" style="margin:0">لسه مفيش مكافآت.</p>'}
          <form id="w-form" class="stack" style="border-top:1px solid var(--line);padding-top:10px">
            <b>مكافأة جديدة</b>
            <div class="row"><input type="text" id="w-name" maxlength="80" placeholder="مثلًا: خصم 50 ج على الطلب الجاي" required style="flex:2;min-width:180px">
              <input type="number" id="w-cost" min="1" placeholder="النقاط" required style="width:100px"><input type="number" id="w-stock" min="0" placeholder="الكمية (اختياري)" style="width:140px"></div>
            <input type="text" id="w-desc" maxlength="300" placeholder="وصف قصير للتاجر (اختياري)">
            <button class="btn primary">إضافة</button>
          </form>
        </section>
      </div>`;
    view.querySelectorAll('[data-cs]').forEach((b) => (b.onclick = () => { state.codeStatus = b.dataset.cs; renderPoints(); }));
    const searchCode = () => { state.codeQ = view.querySelector('#code-q').value.trim(); state.codeStatus = ''; renderPoints(); };
    view.querySelector('#code-go').onclick = searchCode;
    view.querySelector('#code-q').onkeydown = (e) => { if (e.key === 'Enter') searchCode(); };
    view.querySelectorAll('tr[data-rid]').forEach((tr) => {
      const close = (used, b) => run(b, async () => { await api('POST', `${API}/redemptions/${tr.dataset.rid}`, { used }); toast(used ? 'اتعلّم إنه اتستخدم' : 'اتلغى والنقاط رجعت'); renderPoints(); });
      const u = tr.querySelector('[data-use]'); if (u) u.onclick = () => close(true, u);
      const c = tr.querySelector('[data-cancel]'); if (c) c.onclick = () => { if (confirm('تلغي الكود وترجّع النقاط للتاجر؟')) close(false, c); };
    });
    view.querySelectorAll('.reward-row').forEach((row) => (row.querySelector('[data-save]').onclick = (e) => run(e.target, async () => {
      const stock = row.querySelector('.w-stock').value;
      await api('PATCH', `${API}/rewards/${row.dataset.wid}`, { name: row.querySelector('.w-name').value, cost_points: Number(row.querySelector('.w-cost').value), stock: stock === '' ? null : Number(stock), is_active: row.querySelector('.w-active').checked });
      toast('اتحفظت');
    })));
    view.querySelector('#w-form').onsubmit = (e) => {
      e.preventDefault();
      run(e.submitter, async () => {
        const stock = view.querySelector('#w-stock').value;
        await api('POST', `${API}/rewards`, { name: view.querySelector('#w-name').value, cost_points: Number(view.querySelector('#w-cost').value), stock: stock === '' ? null : Number(stock), description: view.querySelector('#w-desc').value });
        toast('المكافأة اتضافت'); renderPoints();
      });
    };
  }

  async function traderPointsDrawer(c) {
    openDrawer('<div class="drawer-head"><button class="btn" data-close>رجوع</button><b>بيحمّل…</b></div>');
    const d = await run(null, () => api('GET', `${API}/trader-accounts/${c.customer_id}/points`));
    if (!d) return;
    openDrawer(`
      <div class="drawer-head"><button class="btn" data-close>رجوع</button><div style="flex:1"><b style="font-size:17px">${esc(c.shop_name || c.name)}</b><div class="small muted num">${esc(c.phone)}</div></div></div>
      <div class="drawer-body">
        <div class="stats"><div class="stat"><b class="num">${n0(d.balance)}</b><span>نقطة</span></div></div>
        <section class="card stack"><h3>تعديل يدوي</h3>
          <div class="row"><input type="number" id="pa-points" placeholder="مثلًا 50 أو -20" style="width:140px"><input type="text" id="pa-note" maxlength="150" placeholder="السبب" style="flex:1"><button class="btn primary" id="pa-go">سجّل</button></div></section>
        <section class="card">${d.entries.length ? d.entries.map((e) => `<div class="row small" style="padding:6px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(e.label)}: ${esc(e.description)}<br><span class="muted">${dateText(e.created_at)}</span></span><b class="num" dir="ltr">${e.points > 0 ? '+' : ''}${n0(e.points)}</b></div>`).join('') : '<p class="muted" style="margin:0">مفيش حركات.</p>'}</section>
      </div>`);
    drawerPanel.querySelector('#pa-go').onclick = (e) => run(e.target, async () => {
      await api('POST', `${API}/trader-accounts/${c.customer_id}/points`, { points: Number(drawerPanel.querySelector('#pa-points').value), note: drawerPanel.querySelector('#pa-note').value });
      toast('اتسجّل'); traderPointsDrawer(c);
    });
  }

  async function accountsSection(box) {
    const me = await api('GET', '/api/staff/me').catch(() => ({}));
    const isOwner = me.role === 'owner';
    box.innerHTML = `
      <section class="card stack"><h3>اللوجو</h3>
        <div class="row"><img src="/api/branding/logo?ts=${Date.now()}" alt="" style="width:56px;height:56px;object-fit:contain;border-radius:12px;background:#fff;border:1px solid var(--line)">
          <input type="file" id="logo-file" accept="image/png,image/jpeg,image/webp" style="flex:1"><button class="btn" id="logo-go">ارفع</button></div>
        <p class="small muted" style="margin:0">PNG بخلفية شفافة أحسن. بيظهر في كل صفحات الجملة.</p></section>
      ${isOwner ? `<section class="card stack"><h3>حسابات الإدارة</h3><div id="staff-list"><p class="muted">بيحمّل…</p></div>
        <form id="st-form" class="row"><input type="text" id="st-user" placeholder="اسم الدخول (إنجليزي)" maxlength="40" required style="width:160px" dir="ltr"><input type="text" id="st-name" placeholder="الاسم" maxlength="80" required style="flex:1;min-width:120px">
          <select id="st-role" style="width:auto"><option value="manager">مدير</option><option value="owner">مالك</option></select><input type="password" id="st-pass" placeholder="كلمة السر (8+)" required style="width:150px" dir="ltr"><button class="btn primary">إضافة</button></form></section>` : ''}`;
    box.querySelector('#logo-go').onclick = (e) => run(e.target, async () => {
      const f = box.querySelector('#logo-file').files[0];
      if (!f) throw new Error('اختار صورة');
      const image = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(f); });
      await api('POST', `${API}/branding/logo`, { image });
      toast('اللوجو اتحدّث'); accountsSection(box);
    });
    if (!isOwner) return;
    const drawStaff = (list) => {
      box.querySelector('#staff-list').innerHTML = list.map((u) => `<div class="row" data-uid="${u.id}" style="padding:6px 0;border-top:1px solid var(--line)"><span style="flex:1"><b>${esc(u.display_name)}</b> <span class="small muted" dir="ltr">${esc(u.username)}</span> <span class="badge ${u.role === 'owner' ? 'pink' : 'gray'}">${u.role === 'owner' ? 'مالك' : 'مدير'}</span>${u.active ? '' : ' <span class="badge red">موقوف</span>'}</span>
        <button class="btn sm" data-pw>كلمة سر جديدة</button><button class="btn sm ${u.active ? 'danger' : ''}" data-act>${u.active ? 'إيقاف' : 'تشغيل'}</button></div>`).join('');
      box.querySelectorAll('[data-uid]').forEach((row) => {
        const u = list.find((x) => String(x.id) === row.dataset.uid);
        row.querySelector('[data-pw]').onclick = (e) => { const pw = prompt(`كلمة السر الجديدة لـ ${u.display_name} (8 حروف على الأقل):`); if (pw) run(e.target, async () => { drawStaff(await api('PATCH', `${API}/staff/${u.id}`, { password: pw })); toast('اتغيّرت'); }); };
        row.querySelector('[data-act]').onclick = (e) => run(e.target, async () => drawStaff(await api('PATCH', `${API}/staff/${u.id}`, { active: !u.active })));
      });
    };
    drawStaff(await api('GET', `${API}/staff`));
    box.querySelector('#st-form').onsubmit = (e) => {
      e.preventDefault();
      run(e.submitter, async () => {
        await api('POST', `${API}/staff`, { username: box.querySelector('#st-user').value, display_name: box.querySelector('#st-name').value, role: box.querySelector('#st-role').value, password: box.querySelector('#st-pass').value });
        toast('الحساب اتعمل'); accountsSection(box);
      });
    };
  }

  // -------------------------------------------------------- subscription
  async function renderSubscription() {
    const settings = state.summary.settings;
    const subscriberTier = tiersList().find((t) => t.is_subscriber);
    view.innerHTML = `
      <div class="ws-section-head"><div><h2>إعلان الاشتراك وطلباته</h2><p>الإعلان بيظهر جوه شاشة الجملة للتاجر اللي مش مشترك. لما يطلب، الطلب بيوصل هنا.</p></div></div>
      ${subscriberTier ? '' : '<div class="alert warn" style="margin-bottom:16px">مفيش فئة متعلّمة «فئة المشتركين». علّم فئة من صفحة الفئات والتسعير علشان الموافقة تنقل التاجر ليها.</div>'}
      <div class="grid-2">
        <section class="card stack">
          <label class="switch"><input type="checkbox" id="a-on" ${settings.announcement_enabled === '1' ? 'checked' : ''}><span class="track"></span><b>الإعلان شغّال</b></label>
          <label class="field"><span>العنوان</span><input type="text" id="a-title" maxlength="120" value="${esc(settings.announcement_title)}"></label>
          <label class="field"><span>النص</span><textarea id="a-text" maxlength="600">${esc(settings.announcement_text)}</textarea></label>
          <div class="row"><span class="small muted">الموافقة بتنقل التاجر لفئة «${esc(subscriberTier ? subscriberTier.name : '—')}»${subscriberTier ? ` (هامش ${subscriberTier.margin_percent == null ? 'عام' : pct(subscriberTier.margin_percent)})` : ''}.</span><span class="spacer"></span><button class="btn primary" id="a-save">حفظ</button></div>
        </section>
        <section class="card">
          <h3 style="margin-bottom:10px">شكله عند التاجر</h3>
          <div class="phone"><div class="promo" id="a-preview"></div></div>
        </section>
      </div>
      <section class="card" style="margin-top:16px">
        <div class="row" style="margin-bottom:12px"><h3>طلبات الاشتراك</h3><span class="spacer"></span>
          <div class="chips" id="r-status">${[['pending', 'مستنية'], ['approved', 'اتقبلت'], ['rejected', 'اترفضت'], ['', 'الكل']].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${state.subStatus === k}">${l}</button>`).join('')}</div>
        </div>
        <div id="r-list"><p class="muted">بيحمّل…</p></div>
      </section>`;
    const preview = () => {
      view.querySelector('#a-preview').innerHTML = `<b>${esc(view.querySelector('#a-title').value)}</b><p>${esc(view.querySelector('#a-text').value)}</p><span class="btn sm">اطلب الاشتراك</span>`;
      view.querySelector('.phone').style.opacity = view.querySelector('#a-on').checked ? '1' : '.45';
    };
    ['#a-title', '#a-text', '#a-on'].forEach((id) => view.querySelector(id).addEventListener('input', preview));
    view.querySelector('#a-on').addEventListener('change', preview);
    preview();
    view.querySelector('#a-save').onclick = (e) => run(e.target, async () => {
      await api('PUT', `${API}/settings`, { announcement_enabled: view.querySelector('#a-on').checked, announcement_title: view.querySelector('#a-title').value, announcement_text: view.querySelector('#a-text').value });
      toast('الإعلان اتحفظ');
      await loadSummary();
    });
    view.querySelectorAll('#r-status .chip').forEach((b) => (b.onclick = () => { state.subStatus = b.dataset.k; view.querySelectorAll('#r-status .chip').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); loadRequests(); }));
    loadRequests();
  }

  async function loadRequests() {
    const box = view.querySelector('#r-list');
    const list = await run(null, () => api('GET', `${API}/subscription-requests?${qs({ status: state.subStatus || 'all' })}`));
    if (!list) return;
    const tiers = tiersList();
    const subscriberTier = tiers.find((t) => t.is_subscriber);
    const labels = { pending: ['amber', 'مستني'], approved: ['green', 'اتقبل'], rejected: ['gray', 'اترفض'] };
    box.innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>التاجر</th><th>الموبايل</th><th>فئته دلوقتي</th><th>التاريخ</th><th>الحالة</th><th></th></tr></thead><tbody>${list.map((r) => `
      <tr data-id="${r.id}"><td>${esc(r.name)}${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ''}</td><td class="num">${esc(r.phone)}</td><td>${esc(r.current_tier_name)}</td><td class="small">${dateText(r.created_at)}</td>
      <td><span class="badge ${labels[r.status][0]}">${labels[r.status][1]}</span>${r.tier_name ? ` <span class="small muted">${esc(r.tier_name)}</span>` : ''}</td>
      <td>${r.status === 'pending' ? `<div class="row"><select class="r-tier" style="width:auto">${options(tiers.map((t) => ({ value: t.id, label: t.name })), subscriberTier ? subscriberTier.id : '')}</select><button class="btn sm primary" data-ok="1">موافقة</button><button class="btn sm danger" data-no="1">رفض</button></div>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">مفيش طلبات.</p>';
    box.querySelectorAll('tr[data-id]').forEach((tr) => {
      const decide = (approve, btn) => run(btn, async () => {
        const r = await api('POST', `${API}/subscription-requests/${tr.dataset.id}/decide`, { approve, tier_id: approve ? Number(tr.querySelector('.r-tier').value) : null });
        toast(approve ? `اتقبل واتنقل لفئة «${r.tier_name}»` : 'الطلب اترفض');
        await loadSummary();
        loadRequests();
      });
      const ok = tr.querySelector('[data-ok]');
      if (ok) { ok.onclick = () => decide(true, ok); tr.querySelector('[data-no]').onclick = (e) => decide(false, e.target); }
    });
  }

  // ---------------------------------------------------------------- start
  const initial = (location.hash || '').slice(1);
  go(['orders', 'points', 'chats', 'plus', 'reports', 'returns', 'ratings', 'feedback', 'delivery', 'employees', 'payments', 'credit', 'settings', 'shortages', 'traders', 'products', 'import', 'suppliers', 'pricing', 'subscription'].includes(initial) ? initial : 'orders');
})();
