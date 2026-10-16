// روز جملة: the screen for a shop's employees. They pick items and quantities (prices only if the
// manager allows) and send the list to the manager, or straight to Rose when allowed.
(function () {
  'use strict';

  const UNITS = [['piece', 'قطعة'], ['dozen', 'دستة'], ['carton', 'كرتونة']];
  const UNIT = Object.fromEntries(UNITS);
  const LIST_KEY = 'rose-wholesale-team-list-v1';
  const view = document.getElementById('view');
  const state = { me: null, q: '', section: '', page: 1 };

  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (v) => `${Number(v || 0).toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ج`;
  const int = (v) => Number(v || 0).toLocaleString('ar-EG-u-nu-latn');
  const when = (v) => (v ? new Date(v).toLocaleString('ar-EG-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' }) : '');

  async function api(method, url, body) {
    let res;
    try {
      res = await fetch(url, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    } catch (_) { throw new Error('مفيش نت. جرّب تاني.'); }
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (res.status === 401 && url !== '/api/team/login') { state.me = { logged_in: false }; route(); throw new Error('سجّل دخول الأول'); }
    if (!res.ok) throw new Error(data && typeof data.detail === 'string' ? data.detail : 'حصلت مشكلة. جرّب تاني.');
    return data;
  }
  let toastTimer;
  function toast(msg, error) {
    const el = document.getElementById('toast');
    el.textContent = msg; el.className = 'toast' + (error ? ' error' : ''); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, error ? 5000 : 2500);
  }
  async function run(btn, fn) {
    if (btn) btn.disabled = true;
    try { return await fn(); } catch (e) { toast(e.message, true); return undefined; } finally { if (btn) btn.disabled = false; }
  }

  // --------------------------------------------------------------- list
  let list = (() => { try { const v = JSON.parse(localStorage.getItem(LIST_KEY) || '[]'); return Array.isArray(v) ? v : []; } catch (_) { return []; } })();
  function saveList() {
    try { localStorage.setItem(LIST_KEY, JSON.stringify(list)); } catch (_) { /* memory only */ }
    drawBar();
  }
  function drawBar() {
    const n = list.length;
    const show = state.me && state.me.logged_in && n && !['list'].includes(currentRoute().name);
    document.getElementById('cart-bar').hidden = !show;
    document.getElementById('cart-bar-text').textContent = `${int(n)} صنف${state.me && state.me.can_see_prices ? ` · ${money(list.reduce((s, l) => s + (l.price || 0) * l.qty, 0))}` : ''}`;
    const c = document.getElementById('list-count');
    c.hidden = !n; c.textContent = int(n);
  }
  function addLine(p, unit, qty) {
    const line = list.find((l) => l.product_id === p.id && l.unit === unit);
    const price = p.units[unit] && p.units[unit].price;
    if (line) line.qty = Math.min(9999, line.qty + qty);
    else list.push({ product_id: p.id, unit, qty, note: '', name: p.name, price: price ?? null });
    saveList();
    toast(`اتضاف: ${p.name}`);
  }

  // ------------------------------------------------------------- routing
  function currentRoute() { const [name, id] = (location.hash.slice(1) || 'catalog').split('/'); return { name, id }; }
  window.addEventListener('hashchange', route);
  let seqN = 0;
  const stale = (s) => s !== seqN;

  async function loadMe() {
    state.me = await api('GET', '/api/team/me');
    const ok = state.me.logged_in;
    document.getElementById('who').textContent = ok ? `${state.me.name} · ${state.me.shop_name}` : 'الموظفين';
    ['bottom-nav', 'search-row', 'logout'].forEach((id) => { document.getElementById(id).hidden = !ok; });
    if (ok) {
      document.getElementById('nav-notebook').hidden = !state.me.can_use_notebook;
      const n = document.getElementById('notice-count');
      n.hidden = !state.me.unread; n.textContent = int(state.me.unread);
    }
  }

  async function route() {
    seqN += 1;
    window.scrollTo(0, 0);
    if (!state.me) { try { await loadMe(); } catch (e) { view.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; } }
    const r = currentRoute();
    document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('on', a.dataset.nav === r.name));
    drawBar();
    if (!state.me.logged_in) return renderLogin();
    ({ catalog: renderCatalog, list: renderList, requests: renderRequests, notebook: renderNotebook, notices: renderNotices }[r.name] || renderCatalog)();
  }

  document.getElementById('open-list').onclick = () => { location.hash = '#list'; };
  document.getElementById('logout').onclick = () => run(null, async () => {
    await api('POST', '/api/team/logout');
    list = []; saveList(); // the next person on this phone starts with an empty list
    state.me = null;
    if (location.hash && location.hash !== '#catalog') location.hash = '#catalog'; else route();
  });
  const search = document.getElementById('search');
  let searchTimer;
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = search.value.trim(); state.page = 1;
      if (currentRoute().name !== 'catalog') { if (state.q) location.hash = '#catalog'; } else loadCatalog();
    }, 300);
  });

  function renderLogin() {
    view.innerHTML = `
      <div class="gate card stack">
        <h2>دخول موظفين المحل</h2>
        <p class="muted" style="margin:0">ادخل برقم موبايلك والكود اللي صاحب المحل بعتهولك.</p>
        <label class="field">رقم الموبايل<input type="tel" id="l-phone" inputmode="tel" autocomplete="tel" dir="ltr"></label>
        <label class="field">الكود<input type="password" id="l-pin" inputmode="numeric" maxlength="4" autocomplete="one-time-code" dir="ltr" class="pin-input"></label>
        <button class="btn primary block" id="l-go">دخول</button>
        <p class="small muted" style="margin:0">نسيت الكود؟ اطلب كود جديد من صاحب المحل.</p>
      </div>`;
    const go = () => run(document.getElementById('l-go'), async () => {
      await api('POST', '/api/team/login', { phone: document.getElementById('l-phone').value.trim(), pin: document.getElementById('l-pin').value.trim() });
      state.me = null; route();
    });
    document.getElementById('l-go').onclick = go;
    document.getElementById('l-pin').addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  }

  // ------------------------------------------------------------- catalog
  function renderCatalog() {
    const me = state.me;
    view.innerHTML = `
      ${me.can_see_prices ? '' : '<p class="small muted" style="margin:0 0 10px">اختار الأصناف والكميات، وصاحب المحل هيراجع الأسعار.</p>'}
      ${me.sections && me.sections.length > 1 ? `<div class="chips" id="sections"><button class="chip" data-s="" aria-pressed="${!state.section}">الكل</button>${me.sections.map((s) => `<button class="chip" data-s="${s.id}" aria-pressed="${String(state.section) === String(s.id)}">${esc(s.name)}</button>`).join('')}</div>` : ''}
      <div id="products"><p class="empty">بيحمّل…</p></div>`;
    view.querySelectorAll('#sections .chip').forEach((b) => (b.onclick = () => { state.section = b.dataset.s; state.page = 1; view.querySelectorAll('#sections .chip').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); loadCatalog(); }));
    loadCatalog();
  }

  async function loadCatalog() {
    const box = document.getElementById('products');
    if (!box) return;
    const seq = seqN;
    const params = new URLSearchParams({ page: state.page });
    if (state.q) params.set('q', state.q);
    if (state.section) params.set('section_id', state.section);
    let data;
    try { data = await api('GET', '/api/team/catalog?' + params); } catch (e) { box.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
    if (stale(seq) || !box.isConnected) return;
    if (!data.items.length) { box.innerHTML = `<div class="empty"><b>${state.q ? `مفيش «${esc(state.q)}»` : 'مفيش أصناف هنا'}</b></div>`; return; }
    const prices = state.me.can_see_prices;
    box.innerHTML = `<div class="grid">${data.items.map((p) => {
      const units = UNITS.filter(([u]) => p.units[u]);
      const unit = units[0][0];
      return `<article class="product" data-id="${p.id}">
        <div class="p-head">${p.image_url ? `<img class="p-img" src="${esc(p.image_url)}" alt="" loading="lazy">` : `<span class="p-img">${esc(p.name.trim().charAt(0))}</span>`}
          <div><div class="p-name">${esc(p.name)}</div>${p.section_name ? `<div class="p-section">${esc(p.section_name)}</div>` : ''}</div></div>
        ${units.length > 1 ? `<div class="seg" role="group" aria-label="الوحدة">${units.map(([u, l]) => `<button data-u="${u}" aria-pressed="${u === unit}">${l}${p.units[u].pieces && u !== 'piece' ? ` <small>(${int(p.units[u].pieces)})</small>` : ''}</button>`).join('')}</div>` : ''}
        ${prices ? `<div class="p-price"><span class="price num" data-price>${money(p.units[unit].price)}</span></div>` : ''}
        ${p.waiting ? '<div class="alert warn small">مش متوفر دلوقتي. هيتبلغ لما يتوفر.</div>' : `
        <div class="add-row"><div class="stepper"><button data-step="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="1" max="9999" value="1" aria-label="الكمية"><button data-step="-1" aria-label="قلّل">−</button></div>
          <button class="btn primary" data-add style="flex:1">أضف للقايمة</button></div>`}
      </article>`;
    }).join('')}</div>
    ${data.pages > 1 ? `<div class="pager"><button class="btn sm" id="pg-prev" ${data.page <= 1 ? 'disabled' : ''}>السابق</button><span class="muted small">${int(data.page)} من ${int(data.pages)}</span><button class="btn sm" id="pg-next" ${data.page >= data.pages ? 'disabled' : ''}>التالي</button></div>` : ''}`;
    data.items.forEach((p) => {
      const card = box.querySelector(`[data-id="${p.id}"]`);
      let unit = UNITS.find(([u]) => p.units[u])[0];
      card.querySelectorAll('[data-u]').forEach((b) => (b.onclick = () => {
        unit = b.dataset.u;
        card.querySelectorAll('[data-u]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        const pr = card.querySelector('[data-price]'); if (pr) pr.textContent = money(p.units[unit].price);
      }));
      const qty = card.querySelector('.stepper input');
      card.querySelectorAll('[data-step]').forEach((b) => (b.onclick = () => { qty.value = Math.max(1, Math.min(9999, (parseInt(qty.value, 10) || 1) + Number(b.dataset.step))); }));
      const add = card.querySelector('[data-add]');
      if (add) add.onclick = () => { addLine(p, unit, parseInt(qty.value, 10) || 1); qty.value = 1; };
    });
    const prev = document.getElementById('pg-prev');
    if (prev) { prev.onclick = () => { state.page -= 1; loadCatalog(); window.scrollTo(0, 0); }; document.getElementById('pg-next').onclick = () => { state.page += 1; loadCatalog(); window.scrollTo(0, 0); }; }
  }

  // ---------------------------------------------------------------- list
  function renderList() {
    const me = state.me;
    const prices = me.can_see_prices;
    const total = list.reduce((s, l) => s + (l.price || 0) * l.qty, 0);
    view.innerHTML = `
      <div class="page-title"><h2>القايمة</h2>${list.length ? '<button class="btn ghost sm" id="clear">فضّيها</button>' : ''}</div>
      ${list.length ? `
        <section class="card">${list.map((l, i) => `
          <div class="line" data-i="${i}">
            <div class="line-top"><div><div class="line-name">${esc(l.name)}</div>${prices && l.price != null ? `<div class="small muted">${money(l.price)} لل${UNIT[l.unit]}</div>` : ''}</div>${prices && l.price != null ? `<b class="num">${money(l.price * l.qty)}</b>` : ''}</div>
            <div class="row"><div class="stepper"><button data-step="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="1" max="9999" value="${l.qty}" aria-label="الكمية"><button data-step="-1" aria-label="قلّل">−</button></div>
              <span class="small muted">${UNIT[l.unit]}</span><span class="spacer"></span><button class="btn ghost sm danger" data-remove>شيل</button></div>
            <input type="text" class="note" maxlength="200" placeholder="ملاحظة للصنف (اختياري)" value="${esc(l.note || '')}">
          </div>`).join('')}</section>
        <section class="card stack">
          <label class="field">ملاحظة (اختياري)<textarea id="req-note" maxlength="300" placeholder="مثلًا: محتاجينهم قبل الخميس"></textarea></label>
          ${prices ? `<div class="totals"><div class="grand"><span>الإجمالي التقريبي</span><span class="num">${money(total)}</span></div></div>` : ''}
          <div class="alert info">${me.can_send_direct ? `القايمة هتتبعت لروز على طول، والدفع ${me.direct_method === 'credit' ? 'على حساب المحل (آجل)' : 'كاش عند الاستلام'}.` : 'القايمة هتروح لصاحب المحل يراجعها ويوافق عليها.'}</div>
          <button class="btn primary block" id="send">${me.can_send_direct ? 'ابعت لروز' : 'ابعت لصاحب المحل'}</button>
        </section>` : '<div class="empty"><b>القايمة فاضية</b><a class="btn primary" href="#catalog">روح للأصناف</a></div>'}`;
    const clear = document.getElementById('clear');
    if (clear) clear.onclick = () => { if (confirm('تفضّي القايمة؟')) { list = []; saveList(); renderList(); } };
    view.querySelectorAll('.line').forEach((row) => {
      const i = Number(row.dataset.i);
      const q = row.querySelector('.stepper input');
      const set = (v) => { list[i].qty = Math.max(1, Math.min(9999, v || 1)); saveList(); renderList(); };
      row.querySelectorAll('[data-step]').forEach((b) => (b.onclick = () => set((parseInt(q.value, 10) || 1) + Number(b.dataset.step))));
      q.onchange = () => set(parseInt(q.value, 10));
      row.querySelector('[data-remove]').onclick = () => { list.splice(i, 1); saveList(); renderList(); };
      row.querySelector('.note').onchange = (e) => { list[i].note = e.target.value.trim(); saveList(); };
    });
    const send = document.getElementById('send');
    if (send) send.onclick = () => run(send, async () => {
      view.querySelectorAll('.line .note').forEach((n, i) => { list[i].note = n.value.trim(); });
      const res = await api('POST', '/api/team/requests', { items: list.map((l) => ({ product_id: l.product_id, unit: l.unit, qty: l.qty, note: l.note || '' })), note: document.getElementById('req-note').value.trim() });
      list = []; saveList();
      toast(res.status === 'ordered' ? `اتبعت لروز: طلب رقم ${res.order_number}` : 'القايمة وصلت لصاحب المحل');
      location.hash = '#requests';
    });
  }

  // ------------------------------------------------------------ requests
  async function renderRequests() {
    view.innerHTML = '<div class="page-title"><h2>طلباتي</h2></div><div id="r-list"><p class="empty">بيحمّل…</p></div>';
    const seq = seqN;
    const data = await run(null, () => api('GET', '/api/team/requests'));
    if (!data || stale(seq)) return;
    const cls = { pending: 'open', approved: 'done', ordered: 'done', rejected: 'cancelled' };
    document.getElementById('r-list').innerHTML = data.length ? data.map((r) => `
      <section class="card stack">
        <div class="row"><span class="status ${cls[r.status]}">${esc(r.status_label)}</span>${r.order_number ? `<span class="small">طلب <b class="num">${r.order_number}</b></span>` : ''}<span class="spacer"></span><span class="small muted">${when(r.created_at)}</span></div>
        ${r.items.map((i) => `<div class="small">${esc(i.name)}: ${int(i.qty)} ${esc(i.unit_label)}${i.waiting ? ' · <span class="muted">مستني يتوفر</span>' : ''}</div>`).join('')}
        ${r.manager_note ? `<div class="alert info small">صاحب المحل: ${esc(r.manager_note)}</div>` : ''}
        ${r.total != null ? `<div class="small muted">الإجمالي التقريبي ${money(r.total)}</div>` : ''}
      </section>`).join('') : '<div class="empty"><b>لسه مابعتش طلبات</b></div>';
  }

  // ------------------------------------------------------------ notebook
  async function renderNotebook() {
    if (!state.me.can_use_notebook) { location.hash = '#catalog'; return; }
    view.innerHTML = `
      <div class="page-title"><h2>دفتر النواقص</h2></div>
      <p class="small muted" style="margin-top:-6px">ده دفتر المحل. اللي تسجّله بيظهر لصاحب المحل.</p>
      <section class="card stack">
        <input type="text" id="nb-name" maxlength="150" placeholder="اكتب الصنف اللي ناقص" aria-label="الصنف">
        <div class="row"><div class="seg" id="nb-unit" role="group" aria-label="الوحدة">${UNITS.map(([u, l], i) => `<button type="button" data-u="${u}" aria-pressed="${i === 0}">${l}</button>`).join('')}</div>
          <div class="stepper"><button data-s="1" type="button" aria-label="زوّد">+</button><input type="number" id="nb-qty" inputmode="numeric" min="1" max="9999" value="1" aria-label="الكمية"><button data-s="-1" type="button" aria-label="قلّل">−</button></div></div>
        <button class="btn primary block" id="nb-add">سجّل</button>
      </section>
      <div id="nb-items"><p class="empty">بيحمّل…</p></div>`;
    let unit = 'piece';
    view.querySelectorAll('#nb-unit [data-u]').forEach((b) => (b.onclick = () => { unit = b.dataset.u; view.querySelectorAll('#nb-unit [data-u]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); }));
    const qty = document.getElementById('nb-qty');
    view.querySelectorAll('[data-s]').forEach((b) => (b.onclick = () => { qty.value = Math.max(1, Math.min(9999, (parseInt(qty.value, 10) || 1) + Number(b.dataset.s))); }));
    const draw = (data) => {
      const box = document.getElementById('nb-items');
      if (!box) return;
      box.innerHTML = data.items.length ? `<section class="card">${data.items.map((x) => `
        <div class="line" data-cid="${esc(x.client_id)}"><div class="line-top"><div><div class="line-name">${esc(x.name)}</div><div class="small muted">${int(x.qty)} ${UNIT[x.unit]}${x.note ? ' · ' + esc(x.note) : ''}${x.is_request ? ' · مش عند روز' : ''}</div></div>
          <button class="btn ghost sm danger" data-del>امسح</button></div></div>`).join('')}</section>` : '<div class="card empty" style="padding:20px"><b>الدفتر فاضي</b></div>';
      box.querySelectorAll('[data-cid]').forEach((row) => (row.querySelector('[data-del]').onclick = (e) => run(e.target, async () => {
        draw(await api('POST', '/api/team/notebook/sync', { ops: [{ op: 'remove', client_id: row.dataset.cid }] }));
      })));
    };
    document.getElementById('nb-add').onclick = (e) => run(e.target, async () => {
      const name = document.getElementById('nb-name').value.trim();
      if (name.length < 2) throw new Error('اكتب اسم الصنف');
      const cid = 't-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
      draw(await api('POST', '/api/team/notebook/sync', { ops: [{ op: 'add', client_id: cid, name, unit, qty: parseInt(qty.value, 10) || 1 }] }));
      document.getElementById('nb-name').value = ''; qty.value = 1;
      toast(`اتسجّل: ${name}`);
    });
    const seq = seqN;
    const data = await run(null, () => api('GET', '/api/team/notebook'));
    if (data && !stale(seq)) draw(data);
  }

  // ------------------------------------------------------------- notices
  async function renderNotices() {
    view.innerHTML = '<div class="page-title"><h2>التنبيهات</h2></div><div id="n-list"><p class="empty">بيحمّل…</p></div>';
    const seq = seqN;
    const data = await run(null, () => api('GET', '/api/team/notices'));
    if (!data || stale(seq)) return;
    document.getElementById('n-list').innerHTML = data.items.length ? data.items.map((n) => `
      <div class="notice${n.read_at ? '' : ' unread'}"><b>${esc(n.title)}</b>${n.body ? `<span>${esc(n.body)}</span>` : ''}<time>${when(n.created_at)}</time></div>`).join('') : '<div class="empty"><b>مفيش تنبيهات</b></div>';
    document.getElementById('notice-count').hidden = true;
    if (state.me) state.me.unread = 0;
  }

  route();
})();
