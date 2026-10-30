// روز جملة: the trader's mobile screen.
(function () {
  'use strict';

  const UNITS = [['piece', 'قطعة'], ['dozen', 'دستة'], ['carton', 'كرتونة']];
  const UNIT = Object.fromEntries(UNITS);
  const CART_KEY = 'rose-wholesale-cart-v1';
  const view = document.getElementById('view');
  const state = { me: null, q: '', section: '', page: 1, catalog: null, choice: {}, scope: 'all', checkout: null, payMethod: '' };

  // ------------------------------------------------------------- helpers
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (v, d = 2) => Number(v || 0).toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: d, maximumFractionDigits: 2 });
  const money = (v) => `${fmt(v)} ج`;
  const int = (v) => Number(v || 0).toLocaleString('ar-EG-u-nu-latn');
  const when = (v) => (v ? new Date(v).toLocaleString('ar-EG-u-nu-latn', { dateStyle: 'medium', timeStyle: 'short' }) : '');

  async function api(method, url, body) {
    let res;
    try {
      res = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (_) {
      const err = new Error('مفيش نت. جرّب تاني لما النت يرجع.');
      err.offline = true;
      throw err;
    }
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (res.status === 401) { state.me = { logged_in: false }; route(); throw new Error('سجّل دخول الأول'); }
    if (!res.ok) {
      const err = new Error(data && typeof data.detail === 'string' ? data.detail : 'حصلت مشكلة. جرّب تاني.');
      err.status = res.status;
      throw err;
    }
    return data;
  }

  let toastTimer;
  function toast(msg, error) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = 'toast' + (error ? ' error' : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, error ? 5000 : 2500);
  }
  async function run(btn, fn) {
    if (btn) btn.disabled = true;
    try { return await fn(); } catch (e) { toast(e.message, true); return undefined; } finally { if (btn) btn.disabled = false; }
  }

  // --------------------------------------------------------------- cart
  function readCart() {
    try { const c = JSON.parse(localStorage.getItem(CART_KEY) || '[]'); return Array.isArray(c) ? c : []; } catch (_) { return []; }
  }
  let cart = readCart();
  function saveCart() {
    try { localStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch (_) { /* private mode: cart lives in memory */ }
    drawCartBar();
  }
  function addToCart(product, unit, qty) {
    const info = product.units[unit];
    if (!info) return;
    const line = cart.find((l) => l.product_id === product.id && l.unit === unit);
    if (line) { line.qty = Math.min(9999, line.qty + qty); line.price = info.price; }
    else cart.push({ product_id: product.id, unit, qty, note: '', name: product.name, price: info.price, pieces: info.pieces });
    saveCart();
    toast(`اتضاف للسلة: ${product.name}`);
  }
  const cartCount = () => cart.reduce((s, l) => s + l.qty, 0);
  const cartTotal = () => cart.reduce((s, l) => s + l.qty * l.price, 0);
  function drawCartBar() {
    const approved = state.me && state.me.trader_status === 'approved';
    const show = approved && cart.length && !['cart', 'done'].includes(currentRoute().name);
    document.getElementById('cart-bar').hidden = !show;
    document.getElementById('cart-bar-text').textContent = `${int(cart.length)} صنف · ${money(cartTotal())}`;
    const dot = document.getElementById('top-cart-count');
    dot.hidden = !cart.length;
    dot.textContent = int(cart.length);
  }
  document.getElementById('open-cart').onclick = () => { location.hash = '#cart'; };
  document.getElementById('top-cart').onclick = () => { location.hash = '#cart'; };
  document.getElementById('top-chat').onclick = () => { location.hash = '#chats'; };

  // ------------------------------------------------------------- routing
  function currentRoute() {
    const [name, id] = (location.hash.slice(1) || 'catalog').split('/');
    return { name, id };
  }
  window.addEventListener('hashchange', route);

  const ME_KEY = 'rose-wholesale-me-v1';
  async function loadMe() {
    try {
      state.me = await api('GET', '/api/wholesale/me');
      state.offline = false;
      try { if (state.me.logged_in) localStorage.setItem(ME_KEY, JSON.stringify(state.me)); else localStorage.removeItem(ME_KEY); } catch (_) { /* no storage */ }
    } catch (e) {
      let cached = null;
      try { cached = JSON.parse(localStorage.getItem(ME_KEY) || 'null'); } catch (_) { /* none */ }
      if (!e.offline || !cached) throw e;
      state.me = cached;
      state.offline = true;
    }
    document.body.classList.toggle('is-offline', !!state.offline);
    const approved = state.me.trader_status === 'approved';
    if (state.me.must_change_password) sessionStorage.setItem('ws-change-password', '1');
    document.getElementById('shop-name').textContent = state.me.shop_name || state.me.name || '';
    document.getElementById('bottom-nav').hidden = !approved;
    document.getElementById('top-cart').hidden = !approved;
    document.getElementById('search-row').hidden = !approved;
    drawCatBar(approved);
    const n = state.me.unread || 0;
    const badge = document.getElementById('notif-count');
    badge.hidden = !n;
    badge.textContent = n > 99 ? '99+' : int(n);
    document.getElementById('short-dot').hidden = !state.me.available_shortages;
    document.getElementById('account-dot').hidden = !state.me.credit_overdue && !state.me.team_pending;
    const chatBtn = document.getElementById('top-chat');
    chatBtn.hidden = !approved;
    document.getElementById('top-chat-count').hidden = !state.me.chat_unread;
    document.getElementById('top-chat-count').textContent = state.me.chat_unread > 99 ? '99+' : int(state.me.chat_unread || 0);
  }

  let routeSeq = 0;
  const stale = (seq) => seq !== routeSeq;
  async function route() {
    routeSeq += 1;
    if (typeof chatTimer !== 'undefined') clearInterval(chatTimer);
    window.scrollTo(0, 0);
    if (!state.me) {
      try { await loadMe(); } catch (e) { view.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
    }
    const r = currentRoute();
    document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('on', a.dataset.nav === r.name || (r.name === 'order' && a.dataset.nav === 'orders')));
    drawCartBar();
    view.classList.remove('wide');
    if (!state.me.logged_in) return renderLogin();
    if (state.me.trader_status !== 'approved') return renderGate();
    if (state.offline && r.name !== 'shortages') return renderOffline();
    const pages = { catalog: renderCatalog, cart: renderCart, orders: renderOrders, order: renderOrder, shortages: renderShortages, notifications: renderNotifications, account: renderAccount, done: renderDone, plus: renderPlus, nearby: renderNearby, chats: renderChats, chat: renderChat, team: renderTeam, 'team-requests': renderTeamRequests };
    (pages[r.name] || renderCatalog)(r.id);
  }

  // --------------------------------------------------------------- login
  function renderLogin(mode) {
    const signup = mode === 'signup';
    view.innerHTML = signup ? `
      <div class="gate card stack">
        <h2>حساب جديد في روز جملة</h2>
        <p class="muted" style="margin:0">اكتب بياناتك وبيانات محلك. الإدارة هتراجع الطلب، وأول ما يتقبل هتشوف الأسعار وتطلب.</p>
        <label class="field">اسمك<input type="text" id="s-name" maxlength="80" autocomplete="name"></label>
        <label class="field">رقم الموبايل<input type="tel" id="s-phone" inputmode="tel" autocomplete="tel" dir="ltr" placeholder="01xxxxxxxxx"></label>
        <label class="field">كلمة السر (8 حروف أو أرقام على الأقل)<input type="password" id="s-pass" autocomplete="new-password" dir="ltr" minlength="8"></label>
        <label class="field">اسم المحل<input type="text" id="s-shop" maxlength="120"></label>
        <label class="field">المنطقة<input type="text" id="s-area" maxlength="120"></label>
        <label class="field">العنوان بالتفصيل (للتوصيل)<input type="text" id="s-address" maxlength="300" placeholder="الشارع، علامة مميزة"></label>
        <button class="btn primary block" id="s-go">سجّل وابعت طلب الانضمام</button>
        <p class="small muted" style="margin:0">عندك حساب؟ <a href="#login" id="to-login">ادخل من هنا</a>.</p>
      </div>` : `
      <div class="gate card stack">
        <h2>أهلًا بيك في روز جملة</h2>
        <p class="muted" style="margin:0">ادخل برقم موبايلك وكلمة السر.</p>
        <label class="field">رقم الموبايل<input type="tel" id="l-phone" inputmode="tel" autocomplete="username tel" dir="ltr"></label>
        <label class="field">كلمة السر<input type="password" id="l-pass" autocomplete="current-password" dir="ltr"></label>
        <button class="btn primary block" id="l-go">دخول</button>
        <button class="btn block" id="to-signup">تاجر جديد؟ سجّل حساب</button>
        <p class="small muted" style="margin:0">نسيت كلمة السر؟ كلّمنا، وهنبعتلك كلمة سر مؤقتة.</p>
      </div>`;
    if (signup) {
      document.getElementById('to-login').onclick = (e) => { e.preventDefault(); renderLogin(); };
      document.getElementById('s-go').onclick = (e) => run(e.target, async () => {
        const v = (id) => document.getElementById(id).value.trim();
        await api('POST', '/api/wholesale/auth/signup', { name: v('s-name'), phone: v('s-phone'), password: document.getElementById('s-pass').value, shop_name: v('s-shop'), area: v('s-area'), address: v('s-address') });
        state.me = null;
        route();
      });
      return;
    }
    document.getElementById('to-signup').onclick = () => renderLogin('signup');
    const go = () => run(document.getElementById('l-go'), async () => {
      const phone = document.getElementById('l-phone').value.trim();
      const password = document.getElementById('l-pass').value;
      if (!phone || !password) throw new Error('اكتب رقم الموبايل وكلمة السر');
      const res = await api('POST', '/api/wholesale/auth/login', { phone, password });
      state.me = null;
      if (res.must_change_password) sessionStorage.setItem('ws-change-password', '1');
      route();
    });
    document.getElementById('l-go').onclick = go;
    document.getElementById('l-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  }

  async function logout() {
    await fetch('/api/wholesale/auth/logout', { method: 'POST', credentials: 'same-origin' });
    state.me = null;
    location.hash = '#catalog';
    route();
  }

  // Before approval the trader only sees these gate screens, so each one needs its own way out.
  const gateLogout = '<button class="btn ghost block" id="gate-logout">خروج (أو الدخول بحساب تاني)</button>';
  function bindGateLogout() {
    document.getElementById('gate-logout').onclick = (e) => run(e.target, logout);
  }

  function renderGate() {
    const s = state.me.trader_status;
    if (s === 'pending') {
      view.innerHTML = `<div class="gate card stack"><h2>طلبك بيتراجع</h2><p class="muted" style="margin:0">استلمنا طلب انضمامك للجملة باسم «${esc(state.me.shop_name)}». أول ما يتقبل هتقدر تشوف الأسعار وتطلب.</p><button class="btn" id="refresh">تحديث</button>${gateLogout}</div>`;
      document.getElementById('refresh').onclick = () => { state.me = null; route(); };
      bindGateLogout();
      return;
    }
    if (s === 'suspended') {
      view.innerHTML = `<div class="gate card stack"><h2>حسابك في الجملة واقف مؤقتًا</h2><p class="muted" style="margin:0">كلّمنا علشان نرجّعه.</p>${gateLogout}</div>`;
      bindGateLogout();
      return;
    }
    view.innerHTML = `
      <div class="gate card stack">
        <h2>انضم لروز جملة</h2>
        <p class="muted" style="margin:0">${s === 'rejected' ? 'طلبك اللي فات ماتقبلش. تقدر تبعت طلب جديد ببيانات أوضح.' : 'اكتب بيانات محلك، والإدارة هتراجع الطلب.'}</p>
        <label class="field">اسم المحل<input type="text" id="j-shop" maxlength="120"></label>
        <label class="field">المنطقة<input type="text" id="j-area" maxlength="120"></label>
        <label class="field">العنوان بالتفصيل (للتوصيل)<input type="text" id="j-address" maxlength="300" placeholder="الشارع، علامة مميزة"></label>
        <label class="field">ملاحظة (اختياري)<textarea id="j-note" maxlength="400" placeholder="مثلًا: نوع البضاعة اللي بتشتغل فيها"></textarea></label>
        <button class="btn primary block" id="j-go">ابعت طلب الانضمام</button>
        ${gateLogout}
      </div>`;
    bindGateLogout();
    document.getElementById('j-go').onclick = (e) => run(e.target, async () => {
      await api('POST', '/api/wholesale/join', { shop_name: document.getElementById('j-shop').value.trim(), area: document.getElementById('j-area').value.trim(), address: document.getElementById('j-address').value.trim(), note: document.getElementById('j-note').value.trim() });
      state.me = null;
      route();
    });
  }

  // ------------------------------------------------------------- catalog
  let searchTimer;
  document.getElementById('search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const q = e.target.value.trim();
      if (q === state.q) return;
      state.q = q;
      state.page = 1;
      if (currentRoute().name === 'catalog') loadCatalog();
      else if (q) location.hash = '#catalog';
    }, 300);
  });
  document.getElementById('search-row').addEventListener('submit', (e) => {
    e.preventDefault();
    clearTimeout(searchTimer);
    const input = document.getElementById('search');
    input.blur();
    state.q = input.value.trim();
    state.page = 1;
    if (currentRoute().name === 'catalog') loadCatalog();
    else location.hash = '#catalog';
  });

  // Amazon-style department strip under the header; shown on every page.
  function drawCatBar(approved) {
    const bar = document.getElementById('cat-bar');
    const sections = (state.me && state.me.sections) || [];
    bar.hidden = !approved || sections.length < 2;
    if (bar.hidden) return;
    bar.innerHTML = `<div class="cat-inner"><button data-s="" aria-pressed="${!state.section}">الكل</button>${sections.map((s) => `<button data-s="${s.id}" aria-pressed="${String(state.section) === String(s.id)}">${esc(s.name)}</button>`).join('')}</div>`;
    bar.querySelectorAll('button').forEach((b) => (b.onclick = () => {
      state.section = b.dataset.s; state.page = 1;
      bar.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      if (currentRoute().name === 'catalog') loadCatalog();
      else location.hash = '#catalog';
    }));
  }

  function renderCatalog() {
    const me = state.me;
    const ann = me.announcement;
    view.classList.add('wide');
    view.innerHTML = `
      ${plusStrip(me)}
      ${ann ? `<section class="promo"><b>${esc(ann.title)}</b><p>${esc(ann.text)}</p>${ann.request_pending ? '<span class="sent">طلبك وصل، والإدارة بتراجعه.</span>' : '<button class="btn sm" id="sub-req">اطلب الاشتراك</button>'}</section>` : ''}
      <div id="hero"></div>
      <div id="results-bar"></div>
      <div id="products"><p class="empty">بيحمّل…</p></div>`;
    const sub = document.getElementById('sub-req');
    if (sub) sub.onclick = () => run(sub, async () => { await api('POST', '/api/wholesale/subscription-request', {}); toast('طلب الاشتراك وصل'); state.me = null; route(); });
    loadCatalog();
  }

  // Welcome banner on the plain first page only; searches and departments go straight to results.
  function drawHero() {
    const box = document.getElementById('hero');
    if (!box) return;
    const home = !state.q && !state.section && state.page === 1 && !state.me.announcement;
    box.innerHTML = home ? `<section class="hero"><span class="hero-kicker">أسعار جملة لمحلك</span><b>أهلًا ${esc(state.me.shop_name || state.me.name || '')}</b><p>اطلب بالقطعة أو الدستة أو الكرتونة، وكل ما تكبّر الوحدة توفّر أكتر.</p></section>` : '';
  }

  function drawResultsBar(data) {
    const box = document.getElementById('results-bar');
    if (!box) return;
    const section = (state.me.sections || []).find((s) => String(s.id) === String(state.section));
    const title = state.q ? `نتايج «${esc(state.q)}»` : section ? esc(section.name) : 'كل الأصناف';
    const range = `${int(data.total)} صنف${data.pages > 1 ? ` · صفحة ${int(data.page)} من ${int(data.pages)}` : ''}`;
    box.innerHTML = data.total ? `<div class="results-bar"><h2>${title}</h2><span class="muted small">${range}</span>${state.q ? '<button class="btn ghost sm" id="clear-q">امسح البحث</button>' : ''}</div>` : '';
    const clear = document.getElementById('clear-q');
    if (clear) clear.onclick = () => { state.q = ''; state.page = 1; document.getElementById('search').value = ''; loadCatalog(); };
  }

  async function loadCatalog() {
    const box = document.getElementById('products');
    if (!box) return;
    const seq = routeSeq;
    const params = new URLSearchParams({ page: state.page });
    if (state.q) params.set('q', state.q);
    if (state.section) params.set('section_id', state.section);
    let data;
    try { data = await api('GET', '/api/wholesale/catalog?' + params); } catch (e) { box.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; return; }
    if (stale(seq) || !box.isConnected) return;
    state.catalog = data;
    drawHero();
    drawResultsBar(data);
    if (!data.items.length) {
      box.innerHTML = state.q
        ? `<div class="empty"><b>مفيش «${esc(state.q)}» عندنا دلوقتي</b>سجّله طلب توفير، وهنبلغك لو وفّرناه.<br><br><button class="btn primary" id="to-request">سجّله طلب توفير</button></div>`
        : '<div class="empty"><b>مفيش أصناف هنا لسه</b></div>';
      const tr = document.getElementById('to-request');
      if (tr) tr.onclick = () => { sessionStorage.setItem('ws-request', state.q); location.hash = '#shortages'; };
      return;
    }
    box.innerHTML = `<div class="grid">${data.items.map(productCard).join('')}</div>
      ${data.pages > 1 ? `<div class="pager"><button class="btn sm" id="pg-prev" ${data.page <= 1 ? 'disabled' : ''}>السابق</button><span class="muted small">${int(data.page)} من ${int(data.pages)}</span><button class="btn sm" id="pg-next" ${data.page >= data.pages ? 'disabled' : ''}>التالي</button></div>` : ''}`;
    data.items.forEach((p) => bindCard(p));
    const prev = document.getElementById('pg-prev');
    if (prev) { prev.onclick = () => { state.page -= 1; loadCatalog(); window.scrollTo(0, 0); }; document.getElementById('pg-next').onclick = () => { state.page += 1; loadCatalog(); window.scrollTo(0, 0); }; }
  }

  function defaultUnit(p) {
    if (state.choice[p.id] && p.units[state.choice[p.id]]) return state.choice[p.id];
    return (UNITS.find(([u]) => p.units[u]) || ['piece'])[0];
  }

  function priceBlock(p, unit) {
    const info = p.units[unit];
    const extra = unit !== 'piece' && info.per_piece != null
      ? `<span class="per">القطعة جوه ال${UNIT[unit]} بـ ${money(info.per_piece)}${info.pieces ? ` · ${int(info.pieces)} قطعة` : ''}</span>`
      : '';
    return `<div class="price-line"><span class="price num">${priceParts(info.price)} <small>ج / ${UNIT[unit]}</small></span>${info.saving_percent ? `<span class="save">وفّر ${int(info.saving_percent)}٪</span>` : ''}</div>${extra}`;
  }

  // Big pounds, small piasters, like a shop price tag.
  function priceParts(v) {
    const n = Math.round(Number(v || 0) * 100);
    const piasters = String(n % 100).padStart(2, '0');
    return `<span class="pounds">${int(Math.floor(n / 100))}</span>${piasters !== '00' ? `<sup>.${piasters}</sup>` : ''}`;
  }

  function productCard(p) {
    const unit = defaultUnit(p);
    const available = UNITS.filter(([u]) => p.units[u]);
    const inCart = cart.filter((l) => l.product_id === p.id).map((l) => `${int(l.qty)} ${UNIT[l.unit]}`).join(' + ');
    return `
      <article class="product" data-id="${p.id}">
        <div class="p-media">${p.image_url ? `<img src="${esc(p.image_url)}" alt="" loading="lazy">` : `<span class="p-letter">${esc(p.name.trim().charAt(0))}</span>`}</div>
        <div class="p-body">
          <h3 class="p-name">${esc(p.name)}</h3>
          ${p.section_name ? `<div class="p-section">${esc(p.section_name)}</div>` : ''}
          ${available.length > 1 ? `<div class="seg" role="group" aria-label="الوحدة">${available.map(([u, l]) => `<button data-u="${u}" aria-pressed="${u === unit}">${l}</button>`).join('')}</div>` : ''}
          <div class="p-price">${priceBlock(p, unit)}</div>
          ${inCart ? `<div class="in-cart">في السلة: ${inCart}</div>` : ''}
          <div class="add-row">
            <div class="stepper"><button data-step="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="1" max="9999" value="1" aria-label="الكمية"><button data-step="-1" aria-label="قلّل">−</button></div>
            <button class="btn add-btn" data-add>أضف للسلة</button>
          </div>
        </div>
      </article>`;
  }

  function bindCard(p) {
    const el = view.querySelector(`.product[data-id="${p.id}"]`);
    if (!el) return;
    const qty = el.querySelector('.stepper input');
    el.querySelectorAll('.seg button').forEach((b) => (b.onclick = () => {
      state.choice[p.id] = b.dataset.u;
      el.querySelectorAll('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      el.querySelector('.p-price').innerHTML = priceBlock(p, b.dataset.u);
    }));
    el.querySelectorAll('[data-step]').forEach((b) => (b.onclick = () => { qty.value = Math.max(1, Math.min(9999, (parseInt(qty.value, 10) || 1) + Number(b.dataset.step))); }));
    el.querySelector('[data-add]').onclick = () => {
      const n = Math.max(1, Math.min(9999, parseInt(qty.value, 10) || 1));
      addToCart(p, defaultUnit(p), n);
      qty.value = 1;
      const fresh = document.createElement('div');
      fresh.innerHTML = productCard(p);
      el.replaceWith(fresh.firstElementChild);
      bindCard(p);
    };
  }

  // ---------------------------------------------------------------- cart
  async function checkCart() {
    const res = await api('POST', '/api/wholesale/cart/check', { items: cart.map((l) => ({ product_id: l.product_id, unit: l.unit, qty: l.qty })) });
    const notes = [];
    cart = cart.filter((l) => {
      const p = res.items[l.product_id];
      const info = p && p.units[l.unit];
      if (!info) { notes.push(`«${l.name}» بال${UNIT[l.unit]} مش متاح دلوقتي واتشال من السلة`); return false; }
      if (info.price !== l.price) notes.push(`سعر «${l.name}» اتغيّر من ${money(l.price)} لـ ${money(info.price)}`);
      l.price = info.price; l.name = p.name; l.pieces = info.pieces;
      return true;
    });
    saveCart();
    state.checkout = res.checkout;
    return notes;
  }

  async function renderCart() {
    if (!cart.length) {
      view.innerHTML = '<div class="empty"><b>السلة فاضية</b>اختار الأصناف من شاشة الجملة.<br><br><a class="btn primary" href="#catalog">روح للجملة</a></div>';
      return;
    }
    view.innerHTML = '<p class="empty">بيحدّث الأسعار…</p>';
    const seq = routeSeq;
    let notes = [];
    try { notes = await checkCart(); } catch (e) { toast(e.message, true); }
    if (stale(seq)) return;
    drawCart(notes);
  }

  let refreshTimer;
  function refreshCart() {
    clearTimeout(refreshTimer);
    const seq = routeSeq;
    refreshTimer = setTimeout(async () => {
      const note = document.getElementById('order-note');
      const kept = note ? note.value : '';
      const y = window.scrollY;
      try { await checkCart(); } catch (e) { toast(e.message, true); return; }
      if (stale(seq)) return;
      drawCart([], kept);
      window.scrollTo(0, y);
    }, 350);
  }

  function methodPicker(co) {
    const usable = co.methods.filter((m) => m.available);
    if (!usable.find((m) => m.code === state.payMethod)) state.payMethod = usable.length ? usable[0].code : '';
    if (!co.methods.length) return '<div class="alert bad">مفيش طرق دفع متاحة دلوقتي. كلّمنا.</div>';
    return `<fieldset class="pay-methods"><legend>طريقة الدفع</legend>${co.methods.map((m) => `
      <label class="pay-option${m.available ? '' : ' off'}">
        <input type="radio" name="pay" value="${m.code}" ${m.code === state.payMethod ? 'checked' : ''} ${m.available ? '' : 'disabled'}>
        <span><b>${esc(m.label)}</b><small>${m.available ? esc(m.fee_text) + (m.code === 'credit' ? ` · المتاح ${money(co.credit.available)}` : '') : esc(m.reason)}</small></span>
        <span class="num">${money(m.grand)}</span>
      </label>`).join('')}</fieldset>`;
  }

  function drawCart(notes, keptNote) {
    const co = state.checkout;
    const chosen = co ? co.methods.find((m) => m.code === state.payMethod) : null;
    view.innerHTML = `
      <div class="page-title"><h2>السلة</h2><button class="btn ghost sm" id="clear">فضّي السلة</button></div>
      ${(notes || []).map((n) => `<div class="changed" style="margin-bottom:8px">${esc(n)}</div>`).join('')}
      ${cart.length ? `
      <section class="card">${cart.map((l, i) => `
        <div class="line" data-i="${i}">
          <div class="line-top"><div><div class="line-name">${esc(l.name)}</div><div class="small muted">${money(l.price)} لل${UNIT[l.unit]}${l.unit !== 'piece' && l.pieces ? ` · القطعة ${money(l.price / l.pieces)}` : ''}</div></div><b class="num">${money(l.qty * l.price)}</b></div>
          <div class="row">
            <div class="stepper"><button data-step="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="1" max="9999" value="${l.qty}" aria-label="الكمية"><button data-step="-1" aria-label="قلّل">−</button></div>
            <span class="small muted">${UNIT[l.unit]}</span><span class="spacer"></span>
            <button class="btn ghost sm danger" data-remove>شيل</button>
          </div>
          <input type="text" class="note" maxlength="200" placeholder="ملاحظة للصنف ده (مثلًا: لون معين)" value="${esc(l.note || '')}">
        </div>`).join('')}
      </section>
      <section class="card stack">
        ${co && co.blocked ? `<div class="alert bad">${esc(co.blocked_reason)} <a href="#account">حسابي</a></div>` : ''}
        ${co && co.below_min ? `<div class="alert warn">أقل قيمة للطلب ${money(co.min_order)}. فاضل ${money(co.min_order - cartTotal())}.</div>` : ''}
        ${co ? methodPicker(co) : ''}
        <label class="field">ملاحظة على الطلب (اختياري)<textarea id="order-note" maxlength="500" placeholder="أي حاجة عايز تقولها لنا">${esc(keptNote || '')}</textarea></label>
        <div class="totals">
          <div><span>الأصناف (${int(cart.length)})</span><span class="num">${money(cartTotal())}</span></div>
          ${co && co.delivery ? `<div><span>التوصيل</span><span class="num">${money(co.delivery)}</span></div>` : ''}
          ${chosen && chosen.fee && chosen.fee_payer === 'customer' ? `<div><span>عمولة ${esc(chosen.label)}</span><span class="num">${money(chosen.fee)}</span></div>` : ''}
          <div class="grand"><span>الإجمالي المبدئي</span><span class="num">${money(chosen ? chosen.grand : cartTotal())}</span></div>
        </div>
        ${co && co.plus.active && (co.plus.delivery_saved + (chosen ? chosen.fee_saved : 0)) > 0 ? `<div class="alert good">وفّرت ${money(co.plus.delivery_saved + (chosen ? chosen.fee_saved : 0))} مع روز بلس${co.plus.delivery_saved ? ': التوصيل مجاني' : ''}.</div>` : ''}
        ${co && co.plus.offer && co.plus.offer_delivery > 0 ? `<a class="plus-strip" href="#plus"><span class="plus-badge">بلس</span><span>مع روز بلس التوصيل ده (${money(co.plus.offer_delivery)}) يبقى مجاني</span><span class="plus-go">اعرف أكتر</span></a>` : ''}
        <div class="alert info">${chosen && ['instapay', 'wallet'].includes(chosen.code) ? 'هنراجع الطلب الأول، وبعدها تحوّل الإجمالي النهائي وترفع صورة التحويل من صفحة الطلب.' : chosen && chosen.code === 'credit' ? 'المبلغ هيتسجّل على حسابك لما الطلب يتسلّم.' : 'هتدفع كاش لما الطلب يوصلك.'} لو صنف طلع مش متوفر هنشيله ونسجّله في نواقصك.</div>
        <button class="btn primary block" id="submit" ${!co || co.blocked || co.below_min || !state.payMethod ? 'disabled' : ''}>ابعت الطلب</button>
      </section>` : '<div class="empty"><b>السلة فاضية</b><a class="btn primary" href="#catalog">روح للجملة</a></div>'}`;
    const clear = document.getElementById('clear');
    if (clear) clear.onclick = () => { if (confirm('تفضّي السلة كلها؟')) { cart = []; saveCart(); drawCart(); } };
    view.querySelectorAll('[name=pay]').forEach((r) => (r.onchange = () => { state.payMethod = r.value; drawCart([], document.getElementById('order-note').value); }));
    view.querySelectorAll('.line').forEach((row) => {
      const i = Number(row.dataset.i);
      const qty = row.querySelector('.stepper input');
      const update = (v) => {
        cart[i].qty = Math.max(1, Math.min(9999, v || 1));
        saveCart();
        qty.value = cart[i].qty;
        row.querySelector('.line-top b').textContent = money(cart[i].qty * cart[i].price);
        refreshCart();
      };
      row.querySelectorAll('[data-step]').forEach((b) => (b.onclick = () => update((parseInt(qty.value, 10) || 1) + Number(b.dataset.step))));
      qty.onchange = () => update(parseInt(qty.value, 10));
      row.querySelector('[data-remove]').onclick = () => { cart.splice(i, 1); saveCart(); if (cart.length) refreshCart(); else drawCart(); };
      row.querySelector('.note').onchange = (e) => { cart[i].note = e.target.value.trim(); saveCart(); };
    });
    const submit = document.getElementById('submit');
    if (submit) submit.onclick = () => run(submit, async () => {
      view.querySelectorAll('.line .note').forEach((n, i) => { cart[i].note = n.value.trim(); });
      try {
        const order = await api('POST', '/api/wholesale/orders', {
          items: cart.map((l) => ({ product_id: l.product_id, unit: l.unit, qty: l.qty, note: l.note || '' })),
          note: document.getElementById('order-note').value.trim(),
          payment_method: state.payMethod,
          team_request_ids: (() => { try { return JSON.parse(sessionStorage.getItem('ws-team-requests') || '[]'); } catch (_) { return []; } })(),
        });
        sessionStorage.removeItem('ws-team-requests');
        cart = []; saveCart();
        sessionStorage.setItem('ws-last-order', JSON.stringify({ id: order.id, number: order.number, total: order.payment.grand, method: order.payment.method }));
        state.me = null;
        location.hash = '#done';
      } catch (e) {
        if (e.status === 409) { toast(e.message, true); renderCart(); return; }
        throw e;
      }
    });
  }

  function renderDone() {
    let last = null;
    try { last = JSON.parse(sessionStorage.getItem('ws-last-order') || 'null'); } catch (_) { /* none */ }
    if (!last) { location.hash = '#orders'; return; }
    const payText = ['instapay', 'wallet'].includes(last.method) ? ' بعد المراجعة هتحوّل الإجمالي النهائي من صفحة الطلب.' : '';
    view.innerHTML = `<div class="success card"><span class="muted">طلبك وصل</span><span class="big num">رقم ${last.number}</span><p style="margin:0">الإجمالي المبدئي ${money(last.total)}. هنراجع الطلب ونبعتلك إشعار بالإجمالي النهائي.${payText}</p><a class="btn primary" href="#order/${last.id}">تفاصيل الطلب</a><a class="btn ghost" href="#catalog">كمّل تسوّق</a></div>`;
  }

  // -------------------------------------------------------------- orders
  async function renderOrders() {
    view.innerHTML = `
      <div class="page-title"><h2>طلباتي</h2></div>
      <div class="chips" id="scope">${[['all', 'الكل'], ['open', 'شغّالة'], ['done', 'اتقفلت']].map(([k, l]) => `<button class="chip" data-k="${k}" aria-pressed="${state.scope === k}">${l}</button>`).join('')}</div>
      <div id="orders-list"><p class="empty">بيحمّل…</p></div>`;
    view.querySelectorAll('#scope .chip').forEach((b) => (b.onclick = () => { state.scope = b.dataset.k; renderOrders(); }));
    const seq = routeSeq;
    const list = await run(null, () => api('GET', '/api/wholesale/orders?scope=' + state.scope));
    if (!list || stale(seq)) return;
    document.getElementById('orders-list').innerHTML = list.length ? list.map((o) => `
      <a class="order-row" href="#order/${o.id}">
        <div style="flex:1"><b class="num">طلب ${o.number}</b><div class="small muted">${when(o.created_at)} · ${int(o.items)} صنف</div>${o.needs_payment ? '<span class="status pay">مستني تحويلك</span>' : ''}</div>
        <div style="text-align:left"><div class="num" style="font-weight:700">${money(o.total)}</div><span class="status ${o.status === 'cancelled' ? 'cancelled' : o.is_open ? 'open' : 'done'}">${esc(o.status_label)}</span></div>
      </a>`).join('') : '<div class="empty"><b>مفيش طلبات</b><a class="btn primary" href="#catalog">اطلب دلوقتي</a></div>';
  }

  const STEPS = [['submitted', 'وصلنا'], ['reviewed', 'اتراجع'], ['preparing', 'بيتجهز'], ['ready', 'جاهز'], ['delivered', 'اتسلّم']];
  const STEP_INDEX = { submitted: 0, reviewed: 1, received: 1, preparing: 2, ready: 3, out_for_delivery: 3, delivered: 4 };

  function readImage(file) {
    return new Promise((resolve, reject) => {
      if (!file) { reject(new Error('اختار صورة التحويل')); return; }
      if (file.size > 6 * 1024 * 1024) { reject(new Error('الصورة أكبر من 6 ميجا')); return; }
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('تعذّر قراءة الصورة'));
      reader.readAsDataURL(file);
    });
  }

  function paymentBox(o) {
    const p = o.payment;
    const statusClass = { paid: 'done', pending: 'open', unpaid: 'pay', cod: 'open', credit: 'open' }[p.status] || 'open';
    return `
      <section class="card stack" id="pay-box">
        <div class="row"><h3 style="flex:1">الدفع</h3><span class="status ${statusClass}">${esc(p.status_label)}</span></div>
        <div class="totals">
          <div><span>طريقة الدفع</span><span>${esc(p.method_label)}</span></div>
          <div><span>الأصناف</span><span class="num">${money(p.products)}</span></div>
          ${p.delivery ? `<div><span>التوصيل</span><span class="num">${money(p.delivery)}</span></div>` : ''}
          ${p.fee && p.fee_payer === 'customer' ? `<div><span>عمولة الدفع</span><span class="num">${money(p.fee)}</span></div>` : ''}
          <div class="grand"><span>${o.reviewed ? 'المطلوب' : 'الإجمالي المبدئي'}</span><span class="num">${money(p.grand)}</span></div>
          ${p.paid ? `<div><span>اللي دفعته</span><span class="num">${money(p.paid)}</span></div>` : ''}
        </div>
        ${p.refund_due ? `<div class="alert good">ليك ${money(p.refund_due)} هنرجّعهملك كاش.</div>` : ''}
        ${p.is_plus && p.plus_saved ? `<div class="alert good">وفّرت ${money(p.plus_saved)} على الطلب ده مع روز بلس.</div>` : ''}
        ${p.points_awarded ? `<div class="alert good">كسبت ${int(p.points_awarded)} نقطة جملة من الطلب ده.</div>` : ''}
        ${p.pending_payment ? `<div class="alert info">بعتّ صورة تحويل بـ ${money(p.pending_payment.amount)}، وبنراجعها.</div>` : ''}
        ${!o.reviewed && ['instapay', 'wallet'].includes(p.method) ? '<div class="alert info">هتحوّل بعد ما نراجع الطلب ونبلغك بالإجمالي النهائي.</div>' : ''}
        ${p.can_pay ? `
          ${p.last_rejection ? `<div class="alert bad">التحويل اللي فات ماتقبلش: ${esc(p.last_rejection.reject_reason)}</div>` : ''}
          <div class="alert warn">حوّل <b class="num">${money(p.to_pay)}</b> على ${esc(p.method_label)}: <b class="num" dir="ltr">${esc(p.account)}</b>${p.instructions ? `<br>${esc(p.instructions)}` : ''}</div>
          <label class="field">صورة التحويل<input type="file" id="proof" accept="image/*"></label>
          <label class="field">اسم أو رقم المحوّل منه (اختياري)<input type="text" id="sender" maxlength="80"></label>
          <button class="btn primary block" id="send-proof">ابعت صورة التحويل</button>` : ''}
        ${p.can_change_method && o.payment_options && o.payment_options.length > 1 ? `
          <details><summary class="small" style="cursor:pointer;color:var(--accent);min-height:36px">غيّر طريقة الدفع</summary>
            <div class="row" style="margin-top:8px"><select id="new-method" style="flex:1">${o.payment_options.map((m) => `<option value="${m.code}" ${m.code === p.method ? 'selected' : ''}>${esc(m.label)} (${money(m.grand)})</option>`).join('')}</select><button class="btn sm" id="change-method">غيّر</button></div>
          </details>` : ''}
      </section>`;
  }

  async function renderOrder(id) {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const o = await run(null, () => api('GET', '/api/wholesale/orders/' + Number(id)));
    if (stale(seq)) return;
    if (!o) { view.innerHTML = '<div class="empty"><b>الطلب مش موجود</b><a class="btn" href="#orders">طلباتي</a></div>'; return; }
    const step = STEP_INDEX[o.status];
    const stateLabel = { available: 'متوفر', partial: 'متوفر جزء', unavailable: 'مش متوفر', pending: 'بيتراجع', cancelled: 'اتلغى' };
    const missing = o.items.filter((i) => i.state === 'partial' || i.state === 'unavailable');
    view.innerHTML = `
      <div class="page-title"><h2 class="num">طلب ${o.number}</h2><a class="btn ghost sm" href="#orders">رجوع</a></div>
      <section class="card">
        <span class="status ${o.status === 'cancelled' ? 'cancelled' : o.is_open ? 'open' : 'done'}">${esc(o.status_label)}</span>
        ${o.status !== 'cancelled' ? `<div class="steps">${STEPS.map((_, i) => `<span class="${i <= step ? 'on' : ''}"></span>`).join('')}</div><div class="steps-labels">${STEPS.map(([, l]) => `<span>${l}</span>`).join('')}</div>` : `<p class="small muted" style="margin:8px 0 0">${esc(o.cancel_reason)}</p>`}
        <p class="small muted" style="margin:8px 0 0">${when(o.created_at)}</p>
      </section>
      ${o.delivery ? `
      <section class="card stack delivery-box" style="margin-top:12px">
        <div class="row"><h3 style="flex:1">${o.delivery.arrived ? 'المندوب وصل' : 'المندوب في الطريق'}</h3>${o.delivery.driver_phone ? `<a class="btn sm dark" href="tel:${esc(o.delivery.driver_phone)}">اتصل بالمندوب</a>` : ''}</div>
        <div>${esc(o.delivery.driver_name)}${o.delivery.eta ? ` · الوقت المتوقع: ${esc(o.delivery.eta)}` : ''}</div>
        <div class="code-box"><span class="small muted">كود الاستلام، اديه للمندوب</span><b class="num">${esc(o.delivery.code)}</b></div>
        ${o.delivery.cash_due ? `<div class="alert warn">جهّز <b class="num">${money(o.delivery.cash_due)}</b> كاش.</div>` : ''}
      </section>` : ''}
      ${o.payment.can_pay ? '<div class="alert warn" style="margin-top:12px">الطلب اتراجع. حوّل المبلغ وابعت صورة التحويل من تحت.</div>' : ''}
      ${missing.length && o.status !== 'cancelled' ? `<div class="alert warn" style="margin-top:12px">${int(missing.length)} صنف مش متوفر بالكامل، واتسجلوا في <a href="#shortages">نواقصك</a>. هنبلغك أول ما يتوفروا.</div>` : ''}
      <section class="card">
        ${o.items.map((i) => `
          <div class="line">
            <div class="line-top"><div><div class="line-name">${esc(i.name)}</div>
              <div class="small muted">${i.final_qty !== i.qty ? `<span class="strike num">${int(i.qty)}</span> ` : ''}<span class="num">${int(i.final_qty)}</span> ${UNIT[i.unit]} × ${money(i.unit_price)}</div>
              ${i.note ? `<div class="small">ملاحظتك: ${esc(i.note)}</div>` : ''}</div>
              <div style="text-align:left"><b class="num">${money(i.line_total)}</b><br><span class="item-state ${i.state}">${stateLabel[i.state] || ''}</span></div></div>
          </div>`).join('')}
        ${o.customer_note ? `<p class="small muted">ملاحظتك على الطلب: ${esc(o.customer_note)}</p>` : ''}
      </section>
      ${paymentBox(o)}
      ${afterDelivery(o)}
      <div class="row" style="margin-top:12px">
        <button class="btn dark" id="again" style="flex:1">اطلب تاني</button>
        ${o.can_cancel ? '<button class="btn danger" id="cancel">إلغاء الطلب</button>' : ''}
      </div>`;
    bindAfterDelivery(o);
    const send = document.getElementById('send-proof');
    if (send) send.onclick = () => run(send, async () => {
      const image = await readImage(document.getElementById('proof').files[0]);
      await api('POST', `/api/wholesale/orders/${o.id}/pay`, { method: o.payment.method, image, sender: document.getElementById('sender').value.trim() });
      toast('صورة التحويل وصلت، وهنراجعها');
      renderOrder(o.id);
    });
    const change = document.getElementById('change-method');
    if (change) change.onclick = () => run(change, async () => {
      await api('POST', `/api/wholesale/orders/${o.id}/payment-method`, { method: document.getElementById('new-method').value });
      toast('طريقة الدفع اتغيّرت');
      renderOrder(o.id);
    });
    document.getElementById('again').onclick = (e) => run(e.target, async () => {
      const ids = [...new Set(o.items.map((i) => i.product_id).filter(Boolean))];
      const fresh = (await api('POST', '/api/wholesale/cart/check', { items: ids.map((pid) => ({ product_id: pid, unit: 'piece', qty: 1 })) })).items;
      let added = 0;
      o.items.forEach((i) => {
        const p = fresh[i.product_id];
        if (p && p.units[i.unit]) {
          const line = cart.find((l) => l.product_id === p.id && l.unit === i.unit);
          if (line) line.qty = Math.min(9999, line.qty + i.qty);
          else cart.push({ product_id: p.id, unit: i.unit, qty: i.qty, note: i.note || '', name: p.name, price: p.units[i.unit].price, pieces: p.units[i.unit].pieces });
          added += 1;
        }
      });
      saveCart();
      toast(added ? `اتضاف ${int(added)} صنف للسلة` : 'الأصناف دي مش متاحة دلوقتي', !added);
      if (added) location.hash = '#cart';
    });
    const cancel = document.getElementById('cancel');
    if (cancel) cancel.onclick = () => { if (confirm('تلغي الطلب ده؟')) run(cancel, async () => { await api('POST', `/api/wholesale/orders/${o.id}/cancel`); toast('الطلب اتلغى'); renderOrder(o.id); }); };
  }

  // ------------------------------------------------ returns and rating
  const RETURN_STEPS = ['اتبعت', 'اتقبل', 'المندوب استلم', 'اتفحص', 'اترجعلك المبلغ'];
  const RETURN_INDEX = { requested: 0, accepted: 1, picked_up: 2, inspected: 3, refunded: 4 };
  const REASONS = { damaged: 'تالف', wrong_item: 'صنف غلط', near_expiry: 'قرب ينتهي', not_needed: 'مش مطلوب' };

  function afterDelivery(o) {
    if (o.status !== 'delivered') return '';
    const rated = o.rating;
    return `
      ${(o.returns || []).map((r) => `
        <section class="card stack">
          <div class="row"><h3 style="flex:1">مرتجع</h3><span class="status ${r.status === 'rejected' ? 'cancelled' : r.status === 'refunded' ? 'done' : 'open'}">${esc(r.status_label)}</span></div>
          ${r.status !== 'rejected' ? `<div class="steps">${RETURN_STEPS.map((_, i) => `<span class="${i <= RETURN_INDEX[r.status] ? 'on' : ''}"></span>`).join('')}</div><div class="steps-labels">${RETURN_STEPS.map((l) => `<span>${l}</span>`).join('')}</div>` : `<div class="alert bad">${esc(r.reject_reason)}</div>`}
          ${r.items.map((i) => `<div class="small">${esc(i.name)}: ${int(i.qty)} ${esc(i.unit_label)} · ${esc(i.reason_label)}</div>`).join('')}
          ${r.status === 'refunded' ? `<div class="alert good">${r.refund_cash ? `رجعلك ${money(r.refund_cash)} كاش. ` : ''}${r.refund_credit ? `اتخصم ${money(r.refund_credit)} من الآجل. ` : ''}${r.points_removed ? `اتشال ${int(r.points_removed)} نقطة.` : ''}</div>` : ''}
        </section>`).join('')}
      ${o.return_open ? `
        <details class="card" id="return-box"><summary class="btn block" style="list-style:none">طلب إرجاع</summary>
          <div class="stack" style="margin-top:12px">
            <p class="small muted" style="margin:0">الإرجاع متاح لحد ${new Date(o.return_deadline).toLocaleDateString('ar-EG-u-nu-latn', { dateStyle: 'medium' })}. الفلوس بترجع كاش بس، بعد ما نستلم الأصناف ونفحصها.</p>
            <div id="return-items"><p class="muted small">بيحمّل…</p></div>
            <label class="field">ملاحظة (اختياري)<input type="text" id="r-note" maxlength="400"></label>
            <label class="field">صورة (اختياري)<input type="file" id="r-photo" accept="image/*"></label>
            <button class="btn primary block" id="r-send">ابعت طلب الإرجاع</button>
          </div>
        </details>` : ''}
      ${rated ? `<section class="card"><div class="row"><h3 style="flex:1">تقييمك</h3><span class="stars-view">${'★'.repeat(rated.stars)}<span class="muted">${'★'.repeat(5 - rated.stars)}</span></span></div>${rated.improve ? `<p class="small muted" style="margin:6px 0 0">${esc(rated.improve)}</p>` : ''}</section>` : `
        <section class="card stack" id="rate-box">
          <h3>إيه رأيك في الطلب ده؟</h3>
          <div class="stars" role="radiogroup" aria-label="التقييم">${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-star="${n}" aria-label="${n} من 5">★</button>`).join('')}</div>
          <div class="chips" style="flex-wrap:wrap">${(o.liked_options || []).map((x) => `<button type="button" class="chip" data-like="${esc(x)}" aria-pressed="false">${esc(x)}</button>`).join('')}</div>
          <label class="field">عايزنا نحسّن إيه؟ (اختياري)<textarea id="rate-text" maxlength="600"></textarea></label>
          <button class="btn primary block" id="rate-send" disabled>ابعت التقييم</button>
        </section>`}
      <p class="small" style="text-align:center"><a href="#account" data-feedback="${o.id}">عندك شكوى أو اقتراح؟</a></p>`;
  }

  function bindAfterDelivery(o) {
    const fb = view.querySelector('[data-feedback]');
    if (fb) fb.onclick = () => sessionStorage.setItem('ws-feedback-order', String(o.id));
    const box = document.getElementById('return-box');
    if (box) box.addEventListener('toggle', async () => {
      if (!box.open || box.dataset.loaded) return;
      const info = await run(null, () => api('GET', `/api/wholesale/orders/${o.id}/returnable`));
      if (!info) return;
      box.dataset.loaded = '1';
      document.getElementById('return-items').innerHTML = info.items.filter((i) => i.returnable > 0).map((i) => `
        <div class="line" data-item="${i.id}">
          <div class="line-top"><div><div class="line-name">${esc(i.name)}</div><div class="small muted">استلمت ${int(i.delivered)} ${esc(i.unit_label)} · ينفع ترجّع لحد ${int(i.returnable)}</div></div></div>
          <div class="row"><div class="stepper"><button data-step="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="0" max="${i.returnable}" value="0" aria-label="الكمية"><button data-step="-1" aria-label="قلّل">−</button></div>
            <select class="r-reason" style="flex:1">${Object.entries(info.reasons).map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></div>
        </div>`).join('');
      box.querySelectorAll('.line').forEach((row) => {
        const input = row.querySelector('input');
        row.querySelectorAll('[data-step]').forEach((b) => (b.onclick = () => { input.value = Math.max(0, Math.min(Number(input.max), (parseInt(input.value, 10) || 0) + Number(b.dataset.step))); }));
      });
    });
    const send = document.getElementById('r-send');
    if (send) send.onclick = () => run(send, async () => {
      const items = [...document.querySelectorAll('#return-items .line')].map((row) => ({ item_id: Number(row.dataset.item), qty: parseInt(row.querySelector('input').value, 10) || 0, reason: row.querySelector('.r-reason').value })).filter((x) => x.qty > 0);
      if (!items.length) throw new Error('اختار الكمية اللي هترجّعها');
      const file = document.getElementById('r-photo').files[0];
      const photo = file ? await readImage(file) : null;
      await api('POST', `/api/wholesale/orders/${o.id}/returns`, { items, note: document.getElementById('r-note').value.trim(), photo });
      toast('طلب الإرجاع وصلنا');
      renderOrder(o.id);
    });
    const rate = document.getElementById('rate-box');
    if (rate) {
      let stars = 0;
      const liked = new Set();
      rate.querySelectorAll('[data-star]').forEach((b) => (b.onclick = () => {
        stars = Number(b.dataset.star);
        rate.querySelectorAll('[data-star]').forEach((x) => x.classList.toggle('on', Number(x.dataset.star) <= stars));
        document.getElementById('rate-send').disabled = false;
      }));
      rate.querySelectorAll('[data-like]').forEach((b) => (b.onclick = () => {
        const v = b.dataset.like;
        if (liked.has(v)) liked.delete(v); else liked.add(v);
        b.setAttribute('aria-pressed', String(liked.has(v)));
      }));
      document.getElementById('rate-send').onclick = (e) => run(e.target, async () => {
        await api('POST', `/api/wholesale/orders/${o.id}/rating`, { stars, liked: [...liked], improve: document.getElementById('rate-text').value.trim() });
        toast('شكرًا على تقييمك');
        renderOrder(o.id);
      });
    }
  }

  // ----------------------------------------------------------- my team
  const TEAM_KEY = 'ws-team-requests';
  const PERMS = [['can_see_prices', 'يشوف الأسعار'], ['can_send_direct', 'يبعت لروز من غير موافقتي'], ['can_use_notebook', 'يسجّل في دفتر النواقص']];

  function pinBox(s) {
    const link = `${location.origin}/wholesale/team/`;
    const msg = `أهلًا ${s.name}، ده رابط طلبات ${state.me.shop_name || 'المحل'} من روز جملة: ${link}\nادخل برقم موبايلك ${s.phone} والكود: ${s.pin}\nمتشاركش الكود مع حد.`;
    return `<div class="alert good stack" style="gap:8px">
      <div>كود <b>${esc(s.name)}</b>: <b class="num" style="font-size:22px;letter-spacing:4px">${esc(s.pin)}</b></div>
      <div class="small">الكود بيظهر مرة واحدة بس. ابعته للموظف دلوقتي.</div>
      <a class="btn sm primary" href="https://wa.me/2${esc(s.phone)}?text=${encodeURIComponent(msg)}" target="_blank" rel="noopener">ابعته واتساب</a></div>`;
  }

  async function renderTeam() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const staff = await run(null, () => api('GET', '/api/wholesale/team/staff'));
    if (!staff || stale(seq)) return;
    view.innerHTML = `
      <div class="page-title"><h2>فريقي</h2><a class="btn ghost sm" href="#team-requests">طلبات الفريق${state.me.team_pending ? ` (${int(state.me.team_pending)})` : ''}</a></div>
      <p class="small muted" style="margin-top:-6px">موظفينك بيدخلوا من <b dir="ltr">${esc(location.origin)}/wholesale/team/</b> برقم موبايلهم وكود من 4 أرقام، ويختاروا الأصناف والكميات. إنت بتحدد لكل واحد مسموحله بإيه.</p>
      <div id="pin-area"></div>
      ${staff.map((s) => `
        <section class="card stack" data-sid="${s.id}">
          <div class="row"><span class="avatar" aria-hidden="true">${esc(s.name.slice(0, 1))}</span><div style="flex:1"><b>${esc(s.name)}</b>${s.is_active ? '' : ' <span class="status cancelled">موقوف</span>'}<div class="small muted num">${esc(s.phone)}${s.pending ? ` · ${int(s.pending)} قايمة مستنية` : ''}</div></div></div>
          ${PERMS.map(([k, l]) => `<label class="switch"><input type="checkbox" data-perm="${k}" ${s[k] ? 'checked' : ''} ${s.is_active ? '' : 'disabled'}><span class="track"></span><span>${l}</span></label>`).join('')}
          ${s.can_send_direct ? `<label class="field">طلباته المباشرة تتدفع<select data-method><option value="cash" ${s.direct_method === 'cash' ? 'selected' : ''}>كاش عند الاستلام</option><option value="credit" ${s.direct_method === 'credit' ? 'selected' : ''}>على حسابي (آجل)</option></select></label>` : ''}
          <div class="row"><button class="btn sm" data-pin ${s.is_active ? '' : 'disabled'}>كود جديد</button><span class="spacer"></span><button class="btn ghost sm ${s.is_active ? 'danger' : ''}" data-active>${s.is_active ? 'وقّف الحساب' : 'رجّع الحساب'}</button></div>
        </section>`).join('')}
      <section class="card stack">
        <h3>أضف موظف</h3>
        <label class="field">الاسم<input type="text" id="t-name" maxlength="60"></label>
        <label class="field">رقم الموبايل<input type="tel" id="t-phone" maxlength="20" dir="ltr" placeholder="01xxxxxxxxx"></label>
        ${PERMS.map(([k, l]) => `<label class="switch"><input type="checkbox" id="t-${k}" ${k === 'can_use_notebook' ? 'checked' : ''}><span class="track"></span><span>${l}</span></label>`).join('')}
        <button class="btn primary block" id="t-add">أضف</button>
      </section>`;
    const showPin = (s) => { document.getElementById('pin-area').innerHTML = pinBox(s); window.scrollTo(0, 0); };
    document.getElementById('t-add').onclick = (e) => run(e.target, async () => {
      const body = { name: document.getElementById('t-name').value.trim(), phone: document.getElementById('t-phone').value.trim() };
      PERMS.forEach(([k]) => { body[k] = document.getElementById(`t-${k}`).checked; });
      const s = await api('POST', '/api/wholesale/team/staff', body);
      await renderTeam(); showPin(s);
    });
    view.querySelectorAll('[data-sid]').forEach((sec) => {
      const id = sec.dataset.sid;
      const s = staff.find((x) => String(x.id) === id);
      const patch = (body, msg) => run(null, async () => { await api('PATCH', `/api/wholesale/team/staff/${id}`, body); if (msg) toast(msg); renderTeam(); });
      sec.querySelectorAll('[data-perm]').forEach((c) => (c.onchange = () => patch({ [c.dataset.perm]: c.checked }, 'اتحفظ')));
      const method = sec.querySelector('[data-method]');
      if (method) method.onchange = () => patch({ direct_method: method.value }, 'اتحفظ');
      sec.querySelector('[data-active]').onclick = () => {
        if (s.is_active && !confirm(`توقف حساب ${s.name}؟ هيخرج من الموقع فورًا.`)) return;
        patch({ is_active: !s.is_active });
      };
      sec.querySelector('[data-pin]').onclick = (e) => run(e.target, async () => {
        if (!confirm('الكود القديم هيبطل. تكمل؟')) return;
        const res = await api('POST', `/api/wholesale/team/staff/${id}/pin`);
        showPin(res);
      });
    });
  }

  async function renderTeamRequests() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const list = await run(null, () => api('GET', '/api/wholesale/team/requests?status=pending'));
    if (!list || stale(seq)) return;
    view.innerHTML = `
      <div class="page-title"><h2>طلبات الفريق</h2><a class="btn ghost sm" href="#team">فريقي</a></div>
      ${list.length ? list.map((r) => `
        <section class="card stack" data-rid="${r.id}">
          <div class="row"><b style="flex:1">${esc(r.staff_name)}</b><span class="small muted">${when(r.created_at)}</span></div>
          ${r.note ? `<div class="alert info">${esc(r.note)}</div>` : ''}
          ${r.items.map((i) => `
            <div class="line" data-iid="${i.id}">
              <div class="line-top"><div><div class="line-name">${esc(i.name)}</div><div class="small muted">${i.price != null ? `${money(i.price)} لل${esc(i.unit_label)}` : 'مش متاح دلوقتي'}${i.note ? ' · ' + esc(i.note) : ''}${i.waiting ? ' · مستني يتوفر' : ''}</div></div>${i.price != null ? `<b class="num" data-line>${money(i.price * i.qty)}</b>` : ''}</div>
              <div class="row"><div class="stepper"><button data-q="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="1" max="9999" value="${i.qty}" aria-label="الكمية"><button data-q="-1" aria-label="قلّل">−</button></div>
                <span class="small muted">${esc(i.unit_label)}</span><span class="spacer"></span>
                <label class="check small"><input type="checkbox" data-remove ${i.price == null || i.waiting ? 'checked' : ''}> شيله</label></div>
            </div>`).join('')}
          <div class="totals"><div class="grand"><span>الإجمالي</span><span class="num" data-total>${money(r.total)}</span></div></div>
          <input type="text" data-note maxlength="300" placeholder="ملاحظة للموظف (اختياري)">
          <div class="row"><button class="btn primary" data-approve style="flex:1">وافق وحطّه في السلة</button><button class="btn ghost danger" data-reject>ارفض</button></div>
        </section>`).join('') : '<div class="card empty" style="padding:24px"><b>مفيش قوايم مستنية</b><span class="small muted">لما موظف يبعت قايمة هتظهر هنا.</span></div>'}`;
    view.querySelectorAll('[data-rid]').forEach((sec) => {
      const r = list.find((x) => String(x.id) === sec.dataset.rid);
      const recalc = () => {
        let total = 0;
        sec.querySelectorAll('[data-iid]').forEach((row) => {
          const i = r.items.find((x) => String(x.id) === row.dataset.iid);
          const qty = parseInt(row.querySelector('input[type=number]').value, 10) || 1;
          const removed = row.querySelector('[data-remove]').checked;
          row.classList.toggle('removed', removed);
          if (i.price != null) { row.querySelector('[data-line]').textContent = money(i.price * qty); if (!removed) total += i.price * qty; }
        });
        sec.querySelector('[data-total]').textContent = money(total);
      };
      sec.querySelectorAll('[data-iid]').forEach((row) => {
        const input = row.querySelector('input[type=number]');
        row.querySelectorAll('[data-q]').forEach((b) => (b.onclick = () => { input.value = Math.max(1, Math.min(9999, (parseInt(input.value, 10) || 1) + Number(b.dataset.q))); recalc(); }));
        input.onchange = recalc;
        row.querySelector('[data-remove]').onchange = recalc;
      });
      recalc();
      sec.querySelector('[data-approve]').onclick = (e) => run(e.target, async () => {
        const changes = [...sec.querySelectorAll('[data-iid]')].map((row) => ({ item_id: Number(row.dataset.iid), qty: parseInt(row.querySelector('input[type=number]').value, 10) || 1, remove: row.querySelector('[data-remove]').checked }));
        const res = await api('POST', `/api/wholesale/team/requests/${r.id}/decide`, { approve: true, changes, note: sec.querySelector('[data-note]').value.trim() });
        res.items.forEach((i) => { const src = r.items.find((x) => x.id === i.id); if (src && src.product) { addToCart(src.product, i.unit, i.qty); const line = cart.find((l) => l.product_id === src.product.id && l.unit === i.unit); if (line && i.note && !line.note) line.note = i.note; } });
        saveCart();
        let ids = []; try { ids = JSON.parse(sessionStorage.getItem(TEAM_KEY) || '[]'); } catch (_) { /* none */ }
        sessionStorage.setItem(TEAM_KEY, JSON.stringify([...new Set([...ids, r.id])]));
        state.me = null;
        toast(`قايمة ${r.staff_name} في السلة`);
        location.hash = '#cart';
      });
      sec.querySelector('[data-reject]').onclick = (e) => run(e.target, async () => {
        if (!confirm('ترفض القايمة دي؟')) return;
        await api('POST', `/api/wholesale/team/requests/${r.id}/decide`, { approve: false, note: sec.querySelector('[data-note]').value.trim() });
        state.me = null; toast('اترفضت، والموظف اتبلّغ'); renderTeamRequests();
      });
    });
  }

  // ----------------------------------------------- nearby traders and chats
  function splitCard(s, ctx) {
    const pct = Math.round((s.taken / s.pieces_total) * 100);
    const statusText = { open: `فاضل ${int(s.remaining)} قطعة`, full: 'الكرتونة اكتملت', ordered: 'اتطلبت', cancelled: 'اتلغى' }[s.status];
    return `<div class="split" data-split="${s.id}">
      <div class="row"><b style="flex:1">${esc(s.product_name)}</b><span class="status ${s.status === 'open' ? 'open' : s.status === 'cancelled' ? 'cancelled' : 'done'}">${statusText}</span></div>
      <div class="small muted">كرتونة ${int(s.pieces_total)} قطعة${s.piece_price ? ` · القطعة ≈ ${money(s.piece_price)}` : ''} · صاحب التقسيم: ${esc(s.organizer.name)}</div>
      ${s.note ? `<div class="small">${esc(s.note)}</div>` : ''}
      <div class="split-bar" role="img" aria-label="اتحجز ${int(s.taken)} من ${int(s.pieces_total)}"><span style="width:${pct}%"></span></div>
      <div class="small muted">${s.members.map((m) => `${esc(m.name)}: ${int(m.pieces)}`).join(' · ')}</div>
      ${s.my_pieces ? `<div class="small"><b>نصيبك ${int(s.my_pieces)} قطعة${s.my_cost ? ` ≈ ${money(s.my_cost)}` : ''}</b></div>` : ''}
      ${ctx === 'chat' ? '' : s.is_organizer ? `
        <div class="row">${s.status === 'full' ? '<button class="btn primary sm" data-sorder>ضيف الكرتونة لطلبي</button>' : ''}${['open', 'full'].includes(s.status) ? '<button class="btn ghost sm danger" data-scancel>إلغاء التقسيم</button>' : ''}</div>`
        : s.status === 'open' ? `
        <div class="row"><div class="stepper"><button data-sstep="1" aria-label="زوّد" type="button">+</button><input type="number" class="s-pieces" inputmode="numeric" min="1" max="${s.remaining + s.my_pieces}" value="${s.my_pieces || 1}" aria-label="عدد القطع"><button data-sstep="-1" aria-label="قلّل" type="button">−</button></div>
          <button class="btn primary sm" data-sjoin>${s.my_pieces ? 'عدّل نصيبي' : 'احجز'}</button>${s.my_pieces ? '<button class="btn ghost sm" data-sleave>اخرج</button>' : ''}</div>` : ''}
    </div>`;
  }

  function bindSplits(root, list, after) {
    root.querySelectorAll('[data-split]').forEach((el) => {
      const s = list.find((x) => x.id === Number(el.dataset.split));
      if (!s) return;
      const input = el.querySelector('.s-pieces');
      el.querySelectorAll('[data-sstep]').forEach((b) => (b.onclick = () => { input.value = Math.max(1, Math.min(Number(input.max), (parseInt(input.value, 10) || 1) + Number(b.dataset.sstep))); }));
      const act = (sel, fn) => { const b = el.querySelector(sel); if (b) b.onclick = () => run(b, fn); };
      act('[data-sjoin]', async () => { await api('POST', `/api/wholesale/splits/${s.id}/join`, { pieces: parseInt(input.value, 10) || 1 }); toast('اتحجز نصيبك، وصاحب التقسيم اتبلّغ'); after(); });
      act('[data-sleave]', async () => { await api('POST', `/api/wholesale/splits/${s.id}/leave`); toast('خرجت من التقسيم'); after(); });
      act('[data-scancel]', async () => { if (!confirm('تلغي التقسيم؟ كل اللي حاجزين هيتبلّغوا.')) return; await api('POST', `/api/wholesale/splits/${s.id}/cancel`); after(); });
      act('[data-sorder]', async () => {
        const res = await api('POST', `/api/wholesale/splits/${s.id}/order`);
        addToCart(res.product, 'carton', 1);
        toast('الكرتونة في السلة. الحساب مع الباقيين بينكم.');
        location.hash = '#cart';
      });
    });
  }

  async function renderNearby() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const d = await run(null, () => api('GET', '/api/wholesale/nearby'));
    if (!d || stale(seq)) return;
    const me = d.me;
    view.innerHTML = `
      <div class="page-title"><h2>تجار قريبين</h2><a class="btn ghost sm" href="#chats">المحادثات</a></div>
      <section class="card stack">
        <label class="switch"><input type="checkbox" id="so-visible" ${me.visible ? 'checked' : ''}><span class="track"></span><span>ظهوري للتجار اللي في ${me.area ? `«${esc(me.area)}»` : 'منطقتي'}</span></label>
        <label class="switch" ${me.visible ? '' : 'hidden'}><input type="checkbox" id="so-phone" ${me.show_phone ? 'checked' : ''}><span class="track"></span><span>اعرض رقم موبايلي</span></label>
        <label class="field" ${me.visible ? '' : 'hidden'}>كلمة عن محلك (اختياري)<input type="text" id="so-bio" maxlength="160" value="${esc(me.bio)}" placeholder="مثلًا: بقالة وجملة صغيرة"></label>
        <p class="small muted" style="margin:0">${me.visible ? `ظاهر باسم «${esc(me.display_name)}». مش بنعرض عنوانك، ولا رقمك إلا لو اخترت.` : 'إنت مش ظاهر لحد. شغّل الظهور علشان التجار القريبين يشوفوك وتقدروا تتكلموا وتقسّموا كراتين.'}</p>
        ${me.chat_suspended ? '<div class="alert bad">المحادثات واقفة عندك. كلّمنا لو فيه حاجة.</div>' : ''}
      </section>
      <section class="card stack">
        <div class="row"><h3 style="flex:1">اطلبوا مع بعض</h3><button class="btn sm dark" id="sp-new" ${me.visible ? '' : 'disabled'}>قسّم كرتونة</button></div>
        <p class="small muted" style="margin:0">كرتونة كبيرة عليك؟ خد نصيبك والباقي يحجزه تجار منطقتك. لما تكمل، صاحب التقسيم بيطلبها والحساب بينكم.</p>
        <div id="sp-form"></div>
        ${d.splits.length ? d.splits.map((s) => splitCard(s, 'list')).join('') : '<p class="small muted" style="margin:0">مفيش تقسيمات مفتوحة في منطقتك دلوقتي.</p>'}
      </section>
      <section class="card">
        <h3 style="margin-bottom:6px">التجار</h3>
        ${d.traders.length ? d.traders.map((t) => `
          <div class="trader-row"><span class="avatar" aria-hidden="true">${esc(t.name.slice(0, 1))}</span>
            <div style="flex:1;min-width:0"><b>${esc(t.name)}</b><div class="small muted">${esc(t.area)}${t.bio ? ` · ${esc(t.bio)}` : ''}</div>${t.phone ? `<a class="small num" href="tel:${esc(t.phone)}" dir="ltr">${esc(t.phone)}</a>` : ''}</div>
            <button class="btn sm" data-talk="${t.customer_id}" ${me.visible || t.thread_id ? '' : 'disabled'}>${t.thread_id ? 'المحادثة' : 'كلّمه'}</button></div>`).join('') : `<p class="small muted" style="margin:0">${me.visible ? 'لسه مفيش تجار ظاهرين في منطقتك.' : 'شغّل الظهور علشان تشوف التجار القريبين.'}</p>`}
      </section>`;
    const save = (patch) => run(null, async () => { await api('PUT', '/api/wholesale/social-profile', patch); renderNearby(); });
    document.getElementById('so-visible').onchange = (e) => save({ visible: e.target.checked });
    document.getElementById('so-phone').onchange = (e) => save({ show_phone: e.target.checked });
    document.getElementById('so-bio').onchange = (e) => save({ bio: e.target.value.trim() });
    view.querySelectorAll('[data-talk]').forEach((b) => (b.onclick = () => run(b, async () => {
      const t = await api('POST', '/api/wholesale/chats', { customer_id: Number(b.dataset.talk) });
      location.hash = `#chat/${t.id}`;
    })));
    bindSplits(view, d.splits, renderNearby);
    document.getElementById('sp-new').onclick = () => drawSplitForm();
  }

  function drawSplitForm() {
    const box = document.getElementById('sp-form');
    let picked = null;
    box.innerHTML = `<div class="stack split-form">
      <input type="text" id="sp-q" placeholder="دوّر على الصنف" autocomplete="off" aria-label="الصنف">
      <div id="sp-results" class="nb-suggest" hidden></div>
      <div id="sp-picked"></div>
    </div>`;
    const q = document.getElementById('sp-q');
    let timer;
    q.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const res = document.getElementById('sp-results');
        if (q.value.trim().length < 2) { res.hidden = true; return; }
        const data = await api('GET', '/api/wholesale/catalog?' + new URLSearchParams({ q: q.value.trim() })).catch(() => null);
        if (!data) return;
        const cartons = data.items.filter((p) => p.units.carton && p.units.carton.pieces > 1).slice(0, 6);
        res.hidden = false;
        res.innerHTML = cartons.length ? cartons.map((p) => `<button type="button" data-p="${p.id}"><span>${esc(p.name)}</span><small class="num">كرتونة ${int(p.units.carton.pieces)} · ${money(p.units.carton.price)}</small></button>`).join('') : '<p class="small muted" style="padding:8px 12px;margin:0">مفيش صنف بكرتونة بالاسم ده.</p>';
        res.querySelectorAll('[data-p]').forEach((b) => (b.onclick = () => {
          picked = cartons.find((p) => p.id === Number(b.dataset.p));
          res.hidden = true; q.value = picked.name;
          const c = picked.units.carton;
          document.getElementById('sp-picked').innerHTML = `
            <div class="stack">
              <div class="small muted">الكرتونة ${int(c.pieces)} قطعة بـ ${money(c.price)} (القطعة ≈ ${money(c.price / c.pieces)})</div>
              <div class="row"><span>نصيبي</span><div class="stepper"><button data-ps="1" type="button" aria-label="زوّد">+</button><input type="number" id="sp-mine" inputmode="numeric" min="1" max="${c.pieces - 1}" value="${Math.floor(c.pieces / 2)}" aria-label="نصيبي"><button data-ps="-1" type="button" aria-label="قلّل">−</button></div><span class="small muted">قطعة</span></div>
              <input type="text" id="sp-note" maxlength="200" placeholder="ملاحظة (اختياري): مثلًا هطلبها يوم الخميس">
              <button class="btn primary block" id="sp-go">افتح التقسيم لتجار منطقتي</button>
              <p class="small muted" style="margin:0">التقسيم بيفضل مفتوح 3 أيام.</p>
            </div>`;
          const mine = document.getElementById('sp-mine');
          document.querySelectorAll('[data-ps]').forEach((x) => (x.onclick = () => { mine.value = Math.max(1, Math.min(c.pieces - 1, (parseInt(mine.value, 10) || 1) + Number(x.dataset.ps))); }));
          document.getElementById('sp-go').onclick = (e) => run(e.target, async () => {
            await api('POST', '/api/wholesale/splits', { product_id: picked.id, pieces: parseInt(mine.value, 10) || 1, note: document.getElementById('sp-note').value.trim() });
            toast('التقسيم اتفتح لتجار منطقتك');
            renderNearby();
          });
        }));
      }, 250);
    };
    q.focus();
  }

  async function renderChats() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const d = await run(null, () => api('GET', '/api/wholesale/chats'));
    if (!d || stale(seq)) return;
    view.innerHTML = `
      <div class="page-title"><h2>المحادثات</h2><a class="btn ghost sm" href="#nearby">تجار قريبين</a></div>
      ${d.profile.chat_suspended ? '<div class="alert bad">المحادثات واقفة عندك. كلّمنا لو فيه حاجة.</div>' : ''}
      ${d.threads.length ? `<section class="card">${d.threads.map((t) => `
        <a class="thread-row" href="#chat/${t.id}"><span class="avatar" aria-hidden="true">${esc(t.other.name.slice(0, 1))}</span>
          <span style="flex:1;min-width:0"><b>${esc(t.other.name)}</b><span class="small muted thread-last">${t.blocked ? 'محظور' : t.last ? `${t.last.mine ? 'إنت: ' : ''}${esc(t.last.body)}` : ''}</span></span>
          <span class="thread-meta"><time class="small muted">${t.last ? when(t.last.at).split('،')[0] : ''}</time>${t.unread ? `<i class="badge-num static">${int(t.unread)}</i>` : ''}</span></a>`).join('')}</section>`
        : `<div class="card empty" style="padding:24px"><b>مفيش محادثات</b><span class="small muted">ابدأ من «تجار قريبين».</span><a class="btn primary" href="#nearby">تجار قريبين</a></div>`}`;
  }

  let chatTimer = null;
  async function renderChat(id) {
    clearInterval(chatTimer);
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const d = await run(null, () => api('GET', `/api/wholesale/chats/${id}`));
    if (!d || stale(seq)) return;
    let lastId = d.messages.length ? d.messages[d.messages.length - 1].id : 0;
    const splits = d.splits;
    const bubble = (m) => m.kind === 'text'
      ? `<div class="bubble ${m.mine ? 'mine' : 'theirs'}" data-mid="${m.id}"><span>${esc(m.body)}</span><time>${when(m.at).split('، ').pop()}</time></div>`
      : `<div class="sys-msg">${esc(m.body)}${m.split_id && splits[m.split_id] ? splitCard(splits[m.split_id], 'chat') : ''}</div>`;
    view.innerHTML = `
      <div class="chat-head"><a class="btn ghost sm" href="#chats" aria-label="رجوع">›</a>
        <span class="avatar" aria-hidden="true">${esc(d.other.name.slice(0, 1))}</span>
        <div style="flex:1;min-width:0"><b>${esc(d.other.name)}</b><div class="small muted">${esc(d.other.area)}${d.other.phone ? ` · <a href="tel:${esc(d.other.phone)}" dir="ltr">${esc(d.other.phone)}</a>` : ''}</div></div>
        <details class="chat-menu"><summary class="btn ghost sm" aria-label="خيارات">…</summary>
          <div class="chat-menu-list"><button id="c-report" type="button">إبلاغ</button><button id="c-block" type="button">${d.i_blocked ? 'شيل الحظر' : 'حظر'}</button></div></details>
      </div>
      <div class="chat-log" id="chat-log">${d.messages.length ? d.messages.map(bubble).join('') : '<p class="small muted" style="text-align:center">ابدأ الكلام. خلّي الحساب والاتفاقات واضحة.</p>'}</div>
      ${d.can_send ? `<form class="chat-send" id="chat-form"><textarea id="chat-text" rows="1" maxlength="1000" placeholder="اكتب رسالة" aria-label="الرسالة"></textarea><button class="btn primary" aria-label="ابعت">ابعت</button></form>`
        : `<div class="alert info">${d.suspended ? 'المحادثات واقفة عندك.' : d.i_blocked ? 'إنت حاظر التاجر ده.' : 'المحادثة دي مقفولة.'}</div>`}`;
    const log = document.getElementById('chat-log');
    log.scrollTop = log.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
    const form = document.getElementById('chat-form');
    if (form) {
      const text = document.getElementById('chat-text');
      text.oninput = () => { text.style.height = 'auto'; text.style.height = Math.min(120, text.scrollHeight) + 'px'; };
      form.onsubmit = (e) => {
        e.preventDefault();
        const body = text.value.trim();
        if (!body) return;
        run(form.querySelector('button'), async () => {
          await api('POST', `/api/wholesale/chats/${id}/messages`, { body });
          text.value = ''; text.style.height = 'auto';
          await poll();
          text.focus();
        });
      };
    }
    document.getElementById('c-block').onclick = (e) => run(e.target, async () => {
      if (!d.i_blocked && !confirm(`تحظر ${d.other.name}؟ مش هيقدر يبعتلك ولا هتشوفوا بعض في «تجار قريبين».`)) return;
      await api('POST', `/api/wholesale/chats/${id}/block`, { on: !d.i_blocked });
      renderChat(id);
    });
    document.getElementById('c-report').onclick = () => {
      const theirs = d.messages.filter((m) => !m.mine && m.kind === 'text');
      const reason = prompt('إيه المشكلة؟ الإدارة هتشوف الرسالة دي واللي حواليها بس.');
      if (!reason) return;
      run(null, async () => {
        await api('POST', `/api/wholesale/chats/${id}/report`, { reason, message_id: theirs.length ? theirs[theirs.length - 1].id : null });
        toast('البلاغ وصل للإدارة');
      });
    };
    async function poll() {
      if (stale(seq) || document.hidden) return;
      const more = await api('GET', `/api/wholesale/chats/${id}?after=${lastId}`).catch(() => null);
      if (!more || stale(seq) || !more.messages.length) return;
      Object.assign(splits, more.splits);
      const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
      const empty = log.querySelector('p');
      if (empty) empty.remove();
      log.insertAdjacentHTML('beforeend', more.messages.map(bubble).join(''));
      lastId = more.messages[more.messages.length - 1].id;
      if (atBottom) { log.scrollTop = log.scrollHeight; window.scrollTo(0, document.body.scrollHeight); }
    }
    chatTimer = setInterval(() => { if (stale(seq)) clearInterval(chatTimer); else poll(); }, 4000);
  }

  // ------------------------------------------------------------ Rose Plus
  const dateText = (iso) => new Date(iso).toLocaleDateString('ar-EG-u-nu-latn', { dateStyle: 'medium' });

  async function renderPlus() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const p = await run(null, () => api('GET', '/api/wholesale/plus'));
    if (!p || stale(seq)) return;
    state.plusPlan = state.plusPlan && p.plans.find((x) => x.code === state.plusPlan) ? state.plusPlan : (p.plans[0] || {}).code;
    state.plusMethod = state.plusMethod || 'cash';
    const month = p.plans.find((x) => x.code === 'month');
    const perks = [
      p.free_delivery ? ['التوصيل مجاني', p.delivery_fee ? `بدل ${money(p.delivery_fee)} على كل طلب.` : 'على كل طلباتك.'] : null,
      p.fee_share_percent < 100 ? ['عمولة دفع أقل', p.fee_share_percent ? `بتدفع ${int(p.fee_share_percent)}٪ بس من عمولة المحفظة وانستاباي.` : 'من غير عمولة على المحفظة وانستاباي.'] : null,
      ['طلبك بيتجهز الأول', 'طلبات روز بلس ليها أولوية عند فريق التجهيز.'],
    ].filter(Boolean);
    const canRequest = p.enabled && p.plans.length && !p.pending;
    view.innerHTML = `
      <div class="page-title"><h2>روز بلس</h2><a class="btn ghost sm" href="#account">رجوع</a></div>
      <section class="plus-hero">
        ${p.active ? `<span class="plus-badge">مشترك</span><b>اشتراكك شغّال لحد ${dateText(p.active.ends_at)}</b><p>باقي ${int(p.active.days_left)} يوم${p.upcoming ? ` · والتجديد بيبدأ بعده لحد ${dateText(p.upcoming.ends_at)}` : ''}.</p>` : '<b>اشتراك شهري أو 3 شهور</b><p>وفّر على كل طلب جملة.</p>'}
        ${p.saved_total ? `<div class="plus-saved">وفّرت لحد دلوقتي <span class="num">${money(p.saved_total)}</span></div>` : ''}
      </section>
      <section class="card stack">
        ${perks.map(([t, d]) => `<div class="perk"><span class="perk-mark" aria-hidden="true"></span><div><b>${t}</b><div class="small muted">${d}</div></div></div>`).join('')}
      </section>
      ${p.pending ? `<div class="alert info">طلب اشتراك ${esc(p.pending.plan_label)} (${money(p.pending.price)}، ${esc(p.pending.method_label)}) وصلنا وبيتراجع. هيتفعّل أول ما الدفع يتأكد.</div>` : ''}
      ${!p.pending && p.last_rejection ? `<div class="alert bad">طلب الاشتراك اللي فات ماتقبلش: ${esc(p.last_rejection.reject_reason)}</div>` : ''}
      ${!p.enabled || !p.plans.length ? '<div class="alert info">روز بلس مش متاح دلوقتي.</div>' : ''}
      ${canRequest ? `
        <section class="card stack">
          <h3>${p.active ? 'جدّد الاشتراك' : 'اختار المدة'}</h3>
          <fieldset class="pay-methods"><legend class="sr">المدة</legend>${p.plans.map((x) => `
            <label class="pay-option"><input type="radio" name="plan" value="${x.code}" ${x.code === state.plusPlan ? 'checked' : ''}>
              <span><b>${esc(x.label)}</b><small>${x.code === 'quarter' && month ? (month.price * 3 > x.price ? `بتوفّر ${money(month.price * 3 - x.price)} عن 3 شهور منفصلين` : `${int(x.days)} يوم`) : `${int(x.days)} يوم`}</small></span>
              <span class="num">${money(x.price)}</span></label>`).join('')}
          </fieldset>
          <fieldset class="pay-methods"><legend>هتدفع إزاي</legend>
            <label class="pay-option"><input type="radio" name="plus-pay" value="cash" ${state.plusMethod === 'cash' ? 'checked' : ''}><span><b>كاش في المحل</b><small>الاشتراك بيتفعّل لما تدفع</small></span></label>
            ${p.transfer_methods.map((m) => `<label class="pay-option"><input type="radio" name="plus-pay" value="${m.code}" ${state.plusMethod === m.code ? 'checked' : ''}><span><b>${esc(m.label)}</b><small dir="ltr" style="text-align:right">${esc(m.account)}</small></span></label>`).join('')}
          </fieldset>
          <div id="plus-transfer"></div>
          <button class="btn primary block" id="plus-send">${p.active ? 'ابعت طلب التجديد' : 'اشترك'}</button>
          <p class="small muted" style="margin:0">${p.active ? 'التجديد بيبدأ من يوم ما اشتراكك الحالي يخلص، فمش هتخسر أيام.' : 'المزايا بتشتغل على الطلبات اللي تبعتها بعد ما الاشتراك يتفعّل.'}</p>
        </section>` : ''}
      ${p.history.length ? `<section class="card"><h3 style="margin-bottom:6px">اشتراكاتك</h3>${p.history.map((h) => `<div class="row small" style="padding:6px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(h.plan_label)} · ${money(h.price)}${h.starts_at ? `<br><span class="muted">${dateText(h.starts_at)} لحد ${dateText(h.ends_at)}</span>` : ''}</span><span class="status ${h.status === 'active' ? 'done' : h.status === 'requested' ? 'open' : 'cancelled'}">${esc(h.status_label)}</span></div>`).join('')}</section>` : ''}`;
    const drawTransfer = () => {
      const box = document.getElementById('plus-transfer');
      if (!box) return;
      const m = p.transfer_methods.find((x) => x.code === state.plusMethod);
      const plan = p.plans.find((x) => x.code === state.plusPlan);
      box.innerHTML = m && plan ? `
        <div class="stack">
          <div class="alert warn">حوّل <b class="num">${money(plan.price)}</b> على ${esc(m.label)}: <b class="num" dir="ltr">${esc(m.account)}</b>${m.instructions ? `<br>${esc(m.instructions)}` : ''}</div>
          <label class="field">صورة التحويل<input type="file" id="plus-proof" accept="image/*"></label>
          <label class="field">اسم أو رقم المحوّل منه (اختياري)<input type="text" id="plus-sender" maxlength="80"></label>
        </div>` : '';
    };
    drawTransfer();
    view.querySelectorAll('[name=plan]').forEach((r) => (r.onchange = () => { state.plusPlan = r.value; drawTransfer(); }));
    view.querySelectorAll('[name=plus-pay]').forEach((r) => (r.onchange = () => { state.plusMethod = r.value; drawTransfer(); }));
    const send = document.getElementById('plus-send');
    if (send) send.onclick = () => run(send, async () => {
      let proof = null;
      if (state.plusMethod !== 'cash') {
        const file = (document.getElementById('plus-proof') || {}).files;
        if (!file || !file[0]) throw new Error('ارفع صورة التحويل');
        proof = await readImage(file[0]);
      }
      await api('POST', '/api/wholesale/plus', { plan: state.plusPlan, method: state.plusMethod, proof, sender: ((document.getElementById('plus-sender') || {}).value || '').trim() });
      toast('طلب الاشتراك وصلنا');
      state.me = null;
      renderPlus();
    });
  }

  function plusStrip(me) {
    const p = me.plus;
    if (!p || (!p.enabled && !p.active)) return '';
    if (p.active) return `<a class="plus-strip on" href="#plus"><span class="plus-badge">بلس</span><span>التوصيل مجاني لحد ${dateText(p.ends_at)}</span></a>`;
    return `<a class="plus-strip" href="#plus"><span class="plus-badge">بلس</span><span>اشترك في روز بلس: التوصيل مجاني وطلبك بيتجهز الأول</span><span class="plus-go">اعرف أكتر</span></a>`;
  }

  // ------------------------------------------------------------- account
  async function renderAccount() {
    view.innerHTML = '<p class="empty">بيحمّل…</p>';
    const seq = routeSeq;
    const a = await run(null, () => api('GET', '/api/wholesale/account'));
    if (!a || stale(seq)) return;
    const c = a.credit;
    const hasCredit = c.limit > 0 || c.balance > 0;
    const statusClass = { ok: 'done', near_limit: 'open', overdue: 'pay' }[c.status];
    view.innerHTML = `
      <div class="page-title"><h2>حسابي</h2><button class="btn ghost sm" id="logout">خروج</button></div>
      <section class="card">
        <div class="row"><div style="flex:1"><div class="muted small">نقاط الجملة</div><div class="price num">${int(a.points_balance)} <small>نقطة</small></div></div></div>
        ${a.points_rate ? `<p class="small muted" style="margin:6px 0 0">بتكسب نقطة على كل ${money(a.points_rate)} في طلباتك، بعد ما الطلب يتسلّم.</p>` : ''}
        ${a.redemptions.filter((d) => d.status === 'pending').map((d) => `<div class="alert good" style="margin-top:8px">كود <b class="num" dir="ltr" style="letter-spacing:2px">${esc(d.code)}</b>: ${esc(d.reward_name)}. قوله للمندوب أو في المحل.</div>`).join('')}
        ${a.rewards.length ? `<h3 style="margin:12px 0 6px">المكافآت</h3>${a.rewards.map((w) => `<div class="row" style="padding:8px 0;border-top:1px solid var(--line)"><span style="flex:1"><b>${esc(w.name)}</b>${w.description ? `<br><span class="small muted">${esc(w.description)}</span>` : ''}</span><button class="btn sm ${a.points_balance >= w.cost_points ? 'primary' : ''}" data-redeem="${w.id}" ${a.points_balance >= w.cost_points ? '' : 'disabled'}>${int(w.cost_points)} نقطة</button></div>`).join('')}` : ''}
        ${a.points.length ? `<details style="margin-top:8px"><summary class="small" style="cursor:pointer;color:var(--accent);min-height:36px">سجل النقاط</summary>${a.points.map((pt) => `<div class="row small" style="padding:4px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(pt.description)}<br><span class="muted">${when(pt.created_at)}</span></span><b class="num" dir="ltr">${pt.points > 0 ? '+' : ''}${int(pt.points)}</b></div>`).join('')}</details>` : ''}
      </section>
      <section class="card stack" id="pw-box">
        <details ${sessionStorage.getItem('ws-change-password') ? 'open' : ''}><summary class="small" style="cursor:pointer;color:var(--accent);min-height:36px">${sessionStorage.getItem('ws-change-password') ? '<b>غيّر كلمة السر المؤقتة دلوقتي</b>' : 'تغيير كلمة السر'}</summary>
          <div class="stack" style="margin-top:8px">
            <label class="field">كلمة السر الحالية<input type="password" id="pw-old" autocomplete="current-password" dir="ltr"></label>
            <label class="field">كلمة السر الجديدة<input type="password" id="pw-new" autocomplete="new-password" dir="ltr" minlength="8"></label>
            <button class="btn dark block" id="pw-save">حفظ</button>
          </div></details>
      </section>
      ${state.me.plus && (state.me.plus.enabled || state.me.plus.active) ? `<a class="card row plus-link" href="#plus"><span class="plus-badge">بلس</span><span style="flex:1"><b>روز بلس</b><br><span class="small muted">${state.me.plus.active ? `شغّال لحد ${dateText(state.me.plus.ends_at)}` : 'التوصيل مجاني وطلبك بيتجهز الأول'}</span></span><span aria-hidden="true">‹</span></a>` : ''}
      <section class="card account-links">
        <a href="#team"><b>فريقي</b><span class="small muted">${state.me.team_pending ? `${int(state.me.team_pending)} قايمة مستنية موافقتك` : 'موظفين المحل وصلاحياتهم'}</span></a>
        <a href="#nearby"><b>تجار قريبين</b><span class="small muted">اطلبوا مع بعض وقسّموا كرتونة</span></a>
        <a href="#chats"><b>المحادثات</b><span class="small muted">${state.me.chat_unread ? `${int(state.me.chat_unread)} رسالة جديدة` : 'كلامك مع التجار'}</span></a>
      </section>
      <section class="card stack">
        <h3>عنوان التوصيل</h3>
        ${state.me.area ? `<div class="small muted">المنطقة: ${esc(state.me.area)}</div>` : ''}
        <div class="row"><input type="text" id="addr" maxlength="300" value="${esc(state.me.address || '')}" placeholder="الشارع، علامة مميزة" style="flex:1"><button class="btn sm" id="addr-save">حفظ</button></div>
        <p class="small muted" style="margin:0">العنوان الجديد بيتطبّق على الطلبات الجاية.</p>
      </section>
      <section class="card stack" id="fb-box">
        <h3>شكوى أو اقتراح</h3>
        <div class="seg" role="group"><button type="button" data-kind="complaint" aria-pressed="true">شكوى</button><button type="button" data-kind="suggestion" aria-pressed="false">اقتراح</button></div>
        <textarea id="fb-text" maxlength="1000" placeholder="اكتب اللي عايز تقوله"></textarea>
        <button class="btn dark block" id="fb-send">ابعت</button>
        <div id="fb-list"></div>
      </section>
      ${hasCredit ? `
      <section class="card stack">
        <div class="row"><h3 style="flex:1">الآجل</h3><span class="status ${statusClass}">${esc(c.status_label)}</span></div>
        <div class="totals">
          <div class="grand"><span>عليك</span><span class="num">${money(c.balance)}</span></div>
          <div><span>الحد</span><span class="num">${money(c.limit)}</span></div>
          ${c.reserved ? `<div><span>طلبات لسه ماتسلّمتش</span><span class="num">${money(c.reserved)}</span></div>` : ''}
          <div><span>المتاح</span><span class="num">${money(c.available)}</span></div>
          ${c.next_due ? `<div><span>ميعاد السداد</span><span class="num">${new Date(c.next_due).toLocaleDateString('ar-EG-u-nu-latn', { dateStyle: 'medium' })}</span></div>` : ''}
        </div>
        ${c.overdue ? '<div class="alert bad">عليك مبلغ متأخر. الطلبات الجديدة واقفة لحد ما تسدّد.</div>' : ''}
        ${a.pending_credit_payments.map((p) => `<div class="alert info">تحويل بـ ${money(p.amount)} بيتراجع.</div>`).join('')}
        ${c.balance > 0 && a.transfer_methods.length ? `
          <details${c.overdue ? ' open' : ''}><summary class="btn primary block" style="list-style:none">سدّد دلوقتي</summary>
            <div class="stack" style="margin-top:12px">
              <label class="field">طريقة التحويل<select id="c-method">${a.transfer_methods.map((m) => `<option value="${m.code}">${esc(m.label)}: ${esc(m.account)}</option>`).join('')}</select></label>
              <label class="field">المبلغ<input type="number" id="c-amount" min="1" step="0.01" value="${c.balance}" inputmode="decimal"></label>
              <label class="field">صورة التحويل<input type="file" id="c-proof" accept="image/*"></label>
              <label class="field">اسم أو رقم المحوّل منه (اختياري)<input type="text" id="c-sender" maxlength="80"></label>
              <button class="btn primary block" id="c-send">ابعت صورة التحويل</button>
              <p class="small muted" style="margin:0">أو ادفع كاش في المحل، والإدارة هتسجّله.</p>
            </div>
          </details>` : ''}
        ${c.entries.length ? `<div>${c.entries.map((e) => `<div class="row small" style="padding:6px 0;border-top:1px solid var(--line)"><span style="flex:1">${esc(e.label)}${e.order_number ? ` · طلب ${e.order_number}` : ''}${e.note && !e.order_number ? ` · ${esc(e.note)}` : ''}<br><span class="muted">${when(e.created_at)}</span></span><b class="num">${e.kind === 'payment' ? '−' : ''}${money(Math.abs(e.amount))}</b></div>`).join('')}</div>` : ''}
      </section>` : ''}`;
    let fbKind = 'complaint';
    const fbOrder = sessionStorage.getItem('ws-feedback-order');
    document.querySelectorAll('#fb-box [data-kind]').forEach((b) => (b.onclick = () => { fbKind = b.dataset.kind; document.querySelectorAll('#fb-box [data-kind]').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); }));
    if (fbOrder) { document.getElementById('fb-box').scrollIntoView({ block: 'center' }); document.getElementById('fb-text').placeholder = `بخصوص طلب ${1000 + Number(fbOrder)}…`; }
    document.getElementById('fb-send').onclick = (e) => run(e.target, async () => {
      await api('POST', '/api/wholesale/feedback', { kind: fbKind, text: document.getElementById('fb-text').value.trim(), order_id: fbOrder ? Number(fbOrder) : null });
      sessionStorage.removeItem('ws-feedback-order');
      toast('وصلتنا، وهنرد عليك');
      renderAccount();
    });
    api('GET', '/api/wholesale/feedback').then((list) => {
      const box = document.getElementById('fb-list');
      if (!box || !list.length) return;
      box.innerHTML = list.map((f) => `<div class="notice" style="margin-top:8px"><b>${f.kind === 'complaint' ? 'شكوى' : 'اقتراح'}${f.order_number ? ` · طلب ${f.order_number}` : ''} <span class="status ${f.status === 'resolved' ? 'done' : 'open'}">${f.status === 'resolved' ? 'اتحلّت' : 'مفتوحة'}</span></b><span class="small">${esc(f.text)}</span>${f.reply ? `<span class="alert good small">ردّنا: ${esc(f.reply)}</span>` : ''}<time>${when(f.created_at)}</time></div>`).join('');
    }).catch(() => {});
    document.getElementById('addr-save').onclick = (e) => run(e.target, async () => {
      await api('PUT', '/api/wholesale/address', { address: document.getElementById('addr').value.trim() });
      state.me.address = document.getElementById('addr').value.trim();
      toast('العنوان اتحفظ');
    });
    view.querySelectorAll('[data-redeem]').forEach((b) => (b.onclick = () => {
      const w = a.rewards.find((x) => String(x.id) === b.dataset.redeem);
      if (!confirm(`تستبدل ${w.cost_points} نقطة بـ «${w.name}»؟`)) return;
      run(b, async () => { const r = await api('POST', '/api/wholesale/points/redeem', { reward_id: w.id }); toast(`كود المكافأة ${r.code}`); renderAccount(); });
    }));
    document.getElementById('pw-save').onclick = (e) => run(e.target, async () => {
      await api('POST', '/api/wholesale/auth/password', { current: document.getElementById('pw-old').value, new: document.getElementById('pw-new').value });
      sessionStorage.removeItem('ws-change-password');
      toast('كلمة السر اتغيّرت');
      renderAccount();
    });
    document.getElementById('logout').onclick = (e) => run(e.target, logout);
    const send = document.getElementById('c-send');
    if (send) send.onclick = () => run(send, async () => {
      const image = await readImage(document.getElementById('c-proof').files[0]);
      await api('POST', '/api/wholesale/credit/pay', { method: document.getElementById('c-method').value, amount: Number(document.getElementById('c-amount').value), image, sender: document.getElementById('c-sender').value.trim() });
      toast('صورة التحويل وصلت، وهنراجعها');
      renderAccount();
    });
  }

  // ----------------------------------------------------------- shortages
  // ---------------------------------------------------- shortages notebook
  // The notebook is kept on the phone and works offline. Changes queue up in
  // `nb.ops` and go to the server when the internet is back.
  const NB_KEY = 'rose-wholesale-notebook-v1';
  const INDEX_KEY = 'rose-wholesale-index-v1';
  const VOICE_MAX_SECONDS = 45;
  const norm = (t) => String(t || '').toLowerCase()
    .replace(/[٠-٩]/g, (d) => '٠١٢٣٤٥٦٧٨٩'.indexOf(d))
    .replace(/[ً-ٰٟ]/g, '').replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
    .replace(/[^\p{L}\p{N}.]+/gu, ' ').replace(/\s+/g, ' ').trim();
  const newId = () => 'n-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);

  function loadNb() {
    try { const v = JSON.parse(localStorage.getItem(NB_KEY) || 'null'); if (v && Array.isArray(v.items)) return v; } catch (_) { /* fresh */ }
    return { items: [], ops: [], shortages: [], frequent: [], reminder: { day: null, hour: 10, days: [] }, synced_at: null, owner: null };
  }
  let nb = loadNb();
  function saveNb() {
    try { localStorage.setItem(NB_KEY, JSON.stringify(nb)); return true; } catch (_) {
      toast('مساحة الموبايل مليانة. اتصل بالنت علشان الدفتر يتبعت.', true); return false;
    }
  }
  let index = null;
  function loadIndex() {
    if (index) return index;
    try { const v = JSON.parse(localStorage.getItem(INDEX_KEY) || 'null'); if (v && Array.isArray(v.items)) index = v; } catch (_) { /* none */ }
    if (index) index.items.forEach((r) => { r.k = norm(r[1]); });
    return index;
  }
  async function refreshIndex(force) {
    const cur = loadIndex();
    if (!force && cur && Date.now() - cur.at < 6 * 3600 * 1000) return;
    try {
      const data = await api('GET', '/api/wholesale/notebook/index');
      index = { at: Date.now(), items: data.items };
      try { localStorage.setItem(INDEX_KEY, JSON.stringify(index)); } catch (_) { /* keep in memory */ }
      index.items.forEach((r) => { r.k = norm(r[1]); });
    } catch (_) { /* offline: keep the old one */ }
  }

  function applyLocal(items, op) {
    const i = items.findIndex((x) => x.client_id === op.client_id);
    if (op.op === 'add' && i < 0) {
      items.push({ client_id: op.client_id, product_id: op.product_id || null, name: op.name, unit: op.unit, qty: op.qty, note: op.note || '',
        voice: op.voice || null, has_voice: !!op.voice, units: op.units || null, price: op.price ?? null, orderable: !!(op.units && op.units[op.unit] != null), pending: true });
    } else if (op.op === 'update' && i >= 0) {
      Object.assign(items[i], { unit: op.unit ?? items[i].unit, qty: op.qty ?? items[i].qty, pending: true });
      if (items[i].units) { items[i].price = items[i].units[items[i].unit] ?? null; items[i].orderable = items[i].price != null; }
    } else if ((op.op === 'remove' || op.op === 'moved') && i >= 0) {
      items.splice(i, 1);
    }
  }
  function queue(op) {
    const { units, price, ...wire } = op;
    nb.ops.push(wire);
    applyLocal(nb.items, op);
    saveNb();
    syncNb();
  }

  let syncing = null;
  async function syncNb() {
    if (syncing) return syncing;
    if (!navigator.onLine && nb.ops.length) { drawNbStatus(); return null; }
    syncing = (async () => {
      const sent = nb.ops.slice(0, 200);
      try {
        const data = sent.length ? await api('POST', '/api/wholesale/notebook/sync', { ops: sent }) : await api('GET', '/api/wholesale/notebook');
        nb.ops = nb.ops.slice(sent.length);
        const items = data.items;
        nb.ops.forEach((op) => applyLocal(items, op));
        Object.assign(nb, { items, shortages: data.shortages, frequent: data.frequent, reminder: data.reminder, synced_at: data.synced_at, estimate: data.estimate, owner: state.me && state.me.name });
        saveNb();
        state.offline = false;
      } catch (e) {
        if (!e.offline) toast(e.message, true);
      } finally {
        syncing = null;
      }
      if (nb.ops.length && navigator.onLine) return syncNb();
      if (currentRoute().name === 'shortages') drawNotebook();
      return null;
    })();
    return syncing;
  }
  window.addEventListener('online', () => {
    const wasOffline = state.offline;
    state.offline = false;
    document.body.classList.remove('is-offline');
    syncNb();
    if (wasOffline && currentRoute().name !== 'shortages') { state.me = null; route(); }
  });
  window.addEventListener('offline', () => { state.offline = true; document.body.classList.add('is-offline'); drawNbStatus(); });

  function drawNbStatus() {
    const el = document.getElementById('nb-status');
    if (!el) return;
    const waiting = nb.ops.length;
    el.className = 'nb-status ' + (!navigator.onLine ? 'off' : waiting ? 'wait' : 'ok');
    el.textContent = !navigator.onLine ? `محفوظ على الموبايل${waiting ? ` · ${int(waiting)} مستني النت` : ''}` : waiting ? 'بيتبعت…' : 'متسجّل';
  }

  let nbChoice = null; // product picked from the suggestions: [id, name, key, {unit: price}]
  let nbUnit = 'piece';

  function renderShortages() {
    const preset = sessionStorage.getItem('ws-request') || '';
    sessionStorage.removeItem('ws-request');
    if (nb.owner && state.me && state.me.name && nb.owner !== state.me.name) nb = { ...loadNb(), items: [], ops: [], owner: state.me.name }; // another account on this phone
    nbChoice = null; nbUnit = 'piece';
    view.innerHTML = `
      <div class="page-title"><h2>دفتر النواقص</h2><span id="nb-status" class="nb-status"></span></div>
      <section class="card stack nb-add">
        <div class="nb-input-row">
          <input type="text" id="nb-name" maxlength="150" placeholder="اكتب الصنف اللي ناقصك" autocomplete="off" value="${esc(preset)}" aria-label="اسم الصنف">
          ${window.SpeechRecognition || window.webkitSpeechRecognition ? '<button class="icon-btn light" id="nb-dictate" aria-label="اكتب بصوتك" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>' : ''}
        </div>
        <div id="nb-suggest" class="nb-suggest" hidden></div>
        <div id="nb-hint" class="small muted" hidden></div>
        <div class="row">
          <div class="seg" id="nb-units" role="group" aria-label="الوحدة"></div>
          <div class="stepper"><button data-nbstep="1" aria-label="زوّد" type="button">+</button><input type="number" id="nb-qty" inputmode="numeric" min="1" max="9999" value="1" aria-label="الكمية"><button data-nbstep="-1" aria-label="قلّل" type="button">−</button></div>
        </div>
        <div class="row">
          <button class="btn primary" id="nb-save" style="flex:1">سجّل في الدفتر</button>
          <button class="btn" id="nb-voice" type="button">سجّل بصوتك</button>
        </div>
        <div id="nb-rec" hidden></div>
      </section>
      <div id="nb-frequent"></div>
      <div id="nb-list"></div>
      <div id="nb-tools"></div>
      <div id="nb-shortages"></div>`;
    const name = document.getElementById('nb-name');
    name.oninput = () => { nbChoice = null; suggest(); };
    name.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); document.getElementById('nb-save').click(); } };
    document.querySelectorAll('[data-nbstep]').forEach((b) => (b.onclick = () => { const q = document.getElementById('nb-qty'); q.value = Math.max(1, Math.min(9999, (parseInt(q.value, 10) || 1) + Number(b.dataset.nbstep))); }));
    document.getElementById('nb-save').onclick = saveLine;
    document.getElementById('nb-voice').onclick = recordVoice;
    const dictate = document.getElementById('nb-dictate');
    if (dictate) dictate.onclick = () => startDictation(name);
    drawUnits();
    if (preset) suggest();
    drawNotebook();
    refreshIndex().then(() => { if (document.getElementById('nb-name') === name && name.value) suggest(); });
    syncNb();
  }

  function drawUnits() {
    const box = document.getElementById('nb-units');
    if (!box) return;
    const prices = nbChoice ? nbChoice[3] : null;
    const allowed = UNITS.filter(([u]) => !prices || prices[u] != null);
    if (!allowed.find(([u]) => u === nbUnit)) nbUnit = allowed[0][0];
    box.innerHTML = allowed.map(([u, l]) => `<button type="button" data-u="${u}" aria-pressed="${u === nbUnit}">${l}${prices ? `<small>${money(prices[u])}</small>` : ''}</button>`).join('');
    box.querySelectorAll('[data-u]').forEach((b) => (b.onclick = () => { nbUnit = b.dataset.u; drawUnits(); }));
  }

  function suggest() {
    const input = document.getElementById('nb-name');
    const box = document.getElementById('nb-suggest');
    const hint = document.getElementById('nb-hint');
    if (!input || !box) return;
    const q = norm(input.value);
    const idx = loadIndex();
    drawUnits();
    if (q.length < 2 || !idx) { box.hidden = true; hint.hidden = !(q.length >= 2 && !idx); hint.textContent = 'هندوّر عليه أول ما النت يرجع.'; return; }
    const words = q.split(' ');
    const hits = idx.items.filter((r) => words.every((w) => r.k.includes(w))).slice(0, 6);
    box.hidden = !hits.length || !!nbChoice;
    box.innerHTML = hits.map((r) => `<button type="button" data-pid="${r[0]}"><span>${esc(r[1])}</span><small class="num">${money(Object.values(r[3])[0])}</small></button>`).join('');
    box.querySelectorAll('[data-pid]').forEach((b) => (b.onclick = () => {
      nbChoice = idx.items.find((r) => r[0] === Number(b.dataset.pid));
      input.value = nbChoice[1];
      box.hidden = true; hint.hidden = true;
      drawUnits();
    }));
    hint.hidden = !!hits.length || !!nbChoice;
    hint.textContent = 'الصنف ده مش عندنا. هيتسجّل «طلب توفير» ونبلغك لو وفّرناه.';
  }

  function saveLine() {
    const input = document.getElementById('nb-name');
    const label = input.value.trim();
    if (label.length < 2) { toast('اكتب اسم الصنف', true); input.focus(); return; }
    const qty = Math.max(1, Math.min(9999, parseInt(document.getElementById('nb-qty').value, 10) || 1));
    const choice = nbChoice || (loadIndex() && loadIndex().items.find((r) => r.k === norm(label)));
    const same = nb.items.find((x) => (choice && x.product_id === choice[0] || !choice && norm(x.name) === norm(label)) && x.unit === nbUnit);
    if (same) queue({ op: 'update', client_id: same.client_id, unit: same.unit, qty: Math.min(9999, same.qty + qty) });
    else queue({ op: 'add', client_id: newId(), product_id: choice ? choice[0] : null, name: choice ? choice[1] : label, unit: nbUnit, qty, units: choice ? choice[3] : null, price: choice ? choice[3][nbUnit] : null });
    toast(same ? `زوّدت ${label}` : `اتسجّل: ${label}`);
    input.value = ''; nbChoice = null; nbUnit = 'piece'; document.getElementById('nb-qty').value = 1;
    document.getElementById('nb-suggest').hidden = true; document.getElementById('nb-hint').hidden = true;
    drawUnits(); drawNotebook();
    input.focus();
  }

  function startDictation(input) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!navigator.onLine) { toast('الكتابة بالصوت محتاجة نت. استخدم «سجّل بصوتك».', true); return; }
    const rec = new SR();
    rec.lang = 'ar-EG'; rec.interimResults = false; rec.maxAlternatives = 1;
    const btn = document.getElementById('nb-dictate');
    btn.classList.add('on');
    rec.onresult = (e) => { input.value = e.results[0][0].transcript; nbChoice = null; suggest(); };
    rec.onerror = () => toast('ماسمعتش كويس. جرّب تاني.', true);
    rec.onend = () => btn.classList.remove('on');
    rec.start();
  }

  let recorder = null;
  async function recordVoice() {
    const box = document.getElementById('nb-rec');
    if (recorder) { recorder.stop(); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('الموبايل ده مش بيسجّل صوت من المتصفح.', true); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (_) { toast('محتاجين إذن المايك علشان نسجّل.', true); return; }
    const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || '';
    const chunks = [];
    recorder = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 24000 } : undefined);
    const btn = document.getElementById('nb-voice');
    const started = Date.now();
    btn.textContent = 'وقّف التسجيل'; btn.classList.add('recording');
    box.hidden = false;
    const tick = setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      box.innerHTML = `<div class="nb-recording"><span class="rec-dot" aria-hidden="true"></span> بيسجّل <b class="num">${s}</b> ث من ${VOICE_MAX_SECONDS}</div>`;
      if (s >= VOICE_MAX_SECONDS && recorder) recorder.stop();
    }, 250);
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      clearInterval(tick);
      stream.getTracks().forEach((t) => t.stop());
      recorder = null;
      btn.textContent = 'سجّل بصوتك'; btn.classList.remove('recording');
      box.hidden = true;
      const blob = new Blob(chunks, { type: (type || 'audio/webm').split(';')[0] });
      if (!blob.size) return;
      const reader = new FileReader();
      reader.onload = () => {
        const label = document.getElementById('nb-name').value.trim();
        queue({ op: 'add', client_id: newId(), name: label || 'ملاحظة صوتية', unit: nbUnit, qty: 1, voice: String(reader.result).replace(/;codecs=[^;,]+/, '') });
        document.getElementById('nb-name').value = '';
        toast('التسجيل اتحفظ في الدفتر');
        drawNotebook();
      };
      reader.readAsDataURL(blob);
    };
    recorder.start();
  }

  function shareText() {
    const lines = nb.items.map((x) => `- ${x.name}: ${x.qty} ${UNIT[x.unit]}${x.note ? ` (${x.note})` : ''}`);
    return `نواقص ${state.me.shop_name || state.me.name || 'المحل'}:\n${lines.join('\n')}`;
  }

  function drawNotebook() {
    const list = document.getElementById('nb-list');
    if (!list) return;
    drawNbStatus();
    const online = navigator.onLine;
    // most needed
    const freq = (nb.frequent || []).filter((f) => !nb.items.find((x) => (f.product_id ? x.product_id === f.product_id : x.name === f.name)));
    document.getElementById('nb-frequent').innerHTML = freq.length ? `<div class="nb-quick"><span class="small muted">الأكتر نقصًا عندك</span><div class="chips">${freq.map((f, i) => `<button class="chip" data-fq="${i}">+ ${esc(f.name)}</button>`).join('')}</div></div>` : '';
    document.querySelectorAll('[data-fq]').forEach((b) => (b.onclick = () => {
      const f = freq[Number(b.dataset.fq)];
      const row = loadIndex() && loadIndex().items.find((r) => r[0] === f.product_id);
      queue({ op: 'add', client_id: newId(), product_id: f.product_id, name: f.name, unit: f.unit, qty: 1, units: row ? row[3] : null, price: row ? row[3][f.unit] : null });
      toast(`اتسجّل: ${f.name}`); drawNotebook();
    }));
    // lines
    const estimate = nb.items.reduce((s, x) => s + (x.orderable && x.price ? x.price * x.qty : 0), 0);
    const ready = nb.items.filter((x) => x.orderable);
    list.innerHTML = nb.items.length ? `
      <section class="card">${nb.items.map((x) => `
        <div class="line nb-line" data-cid="${esc(x.client_id)}">
          <div class="line-top"><div><div class="line-name">${esc(x.name)}</div>
            <div class="small muted">${x.orderable && x.price != null ? `${money(x.price)} لل${UNIT[x.unit]}` : x.has_voice && !x.product_id ? 'تسجيل صوتي' : x.is_request ? 'مش عندنا · اتسجّل طلب توفير' : x.pending ? 'هيتراجع لما النت يرجع' : 'مش متاح دلوقتي'}${x.note ? ' · ' + esc(x.note) : ''}</div></div>
            ${x.orderable && x.price != null ? `<b class="num">${money(x.price * x.qty)}</b>` : ''}</div>
          ${x.has_voice ? `<audio controls preload="none" src="${x.voice ? x.voice : x.id ? `/api/wholesale/notebook/items/${x.id}/voice` : ''}"></audio>` : ''}
          <div class="row">
            <div class="stepper"><button data-q="1" aria-label="زوّد">+</button><input type="number" inputmode="numeric" min="1" max="9999" value="${x.qty}" aria-label="الكمية"><button data-q="-1" aria-label="قلّل">−</button></div>
            <span class="small muted">${UNIT[x.unit]}</span><span class="spacer"></span>
            <button class="btn ghost sm danger" data-del>امسح</button>
          </div>
        </div>`).join('')}
      </section>
      <section class="card stack">
        <div class="totals"><div class="grand"><span>تقدير اللي متاح عندنا (${int(ready.length)} صنف)</span><span class="num">${money(estimate)}</span></div></div>
        <button class="btn primary block" id="nb-to-cart" ${ready.length ? '' : 'disabled'}>حوّل الدفتر لطلب</button>
        <p class="small muted" style="margin:0">${online ? 'الأصناف المتاحة بتروح السلة، والباقي بيفضل في الدفتر.' : 'التحويل لطلب محتاج نت. الدفتر محفوظ لحد ما النت يرجع.'}</p>
      </section>` : '<div class="card empty" style="padding:24px"><b>الدفتر فاضي</b><span class="small muted">سجّل أي حاجة تخلص من المحل، حتى لو مفيش نت.</span></div>';
    list.querySelectorAll('.nb-line').forEach((row) => {
      const x = nb.items.find((i) => i.client_id === row.dataset.cid);
      const input = row.querySelector('input');
      const set = (v) => { const qty = Math.max(1, Math.min(9999, v || 1)); if (qty !== x.qty) { queue({ op: 'update', client_id: x.client_id, unit: x.unit, qty }); drawNotebook(); } };
      row.querySelectorAll('[data-q]').forEach((b) => (b.onclick = () => set((parseInt(input.value, 10) || 1) + Number(b.dataset.q))));
      input.onchange = () => set(parseInt(input.value, 10));
      row.querySelector('[data-del]').onclick = () => { queue({ op: 'remove', client_id: x.client_id }); drawNotebook(); };
    });
    const toCart = document.getElementById('nb-to-cart');
    if (toCart) toCart.onclick = () => run(toCart, async () => {
      if (!navigator.onLine) throw new Error('التحويل لطلب محتاج نت.');
      await syncNb();
      const moving = nb.items.filter((x) => x.orderable && x.product);
      if (!moving.length) throw new Error('مفيش أصناف متاحة في الدفتر دلوقتي.');
      moving.forEach((x) => addToCart(x.product, x.unit, x.qty));
      moving.forEach((x) => { nb.ops.push({ op: 'moved', client_id: x.client_id }); applyLocal(nb.items, { op: 'moved', client_id: x.client_id }); });
      saveNb();
      await syncNb();
      toast(`${int(moving.length)} صنف راحوا السلة`);
      location.hash = '#cart';
    });
    drawTools();
    drawSavedShortages();
  }

  function drawTools() {
    const box = document.getElementById('nb-tools');
    if (!box) return;
    const r = nb.reminder || { day: null, hour: 10 };
    const days = ['الأحد', 'الاتنين', 'التلات', 'الأربع', 'الخميس', 'الجمعة', 'السبت'];
    box.innerHTML = `
      <section class="card stack">
        <h3>أدوات الدفتر</h3>
        <button class="btn block" id="nb-share" ${nb.items.length ? '' : 'disabled'}>شارك القايمة على واتساب</button>
        <div class="nb-reminder">
          <span>فكّرني كل</span>
          <select id="nb-day" aria-label="يوم التذكير"><option value="">من غير تذكير</option>${days.map((d, i) => `<option value="${i}" ${r.day === i ? 'selected' : ''}>${d}</option>`).join('')}</select>
          <span>الساعة</span>
          <select id="nb-hour" aria-label="ساعة التذكير">${Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${r.hour === h ? 'selected' : ''}>${h === 0 ? '12 بالليل' : h < 12 ? `${h} الصبح` : h === 12 ? '12 الضهر' : `${h - 12} ${h < 17 ? 'العصر' : 'بالليل'}`}</option>`).join('')}</select>
        </div>
        <div class="row"><button class="btn sm dark" id="nb-rem-save">احفظ التذكير</button>${r.day != null ? '<a class="btn sm" id="nb-ics" href="/api/wholesale/notebook/reminder.ics">ضيفه لتقويم الموبايل</a>' : ''}</div>
        <p class="small muted" style="margin:0">التذكير بيوصلك إشعار هنا. لو ضفته لتقويم الموبايل، الموبايل نفسه هيفكّرك حتى لو مافتحتش الموقع.</p>
      </section>`;
    document.getElementById('nb-share').onclick = async () => {
      const text = shareText();
      if (navigator.share) { try { await navigator.share({ text }); return; } catch (_) { /* cancelled */ return; } }
      window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, '_blank', 'noopener');
    };
    document.getElementById('nb-rem-save').onclick = (e) => run(e.target, async () => {
      if (!navigator.onLine) throw new Error('حفظ التذكير محتاج نت.');
      const day = document.getElementById('nb-day').value;
      nb.reminder = await api('PUT', '/api/wholesale/notebook/reminder', { day: day === '' ? null : Number(day), hour: Number(document.getElementById('nb-hour').value) });
      saveNb();
      toast(nb.reminder.day == null ? 'التذكير اتلغى' : `هنفكّرك كل ${nb.reminder.day_label}`);
      drawTools();
    });
  }

  function drawSavedShortages() {
    const box = document.getElementById('nb-shortages');
    if (!box) return;
    const list = nb.shortages || [];
    const fromOrders = list.filter((s) => s.source !== 'request');
    const requests = list.filter((s) => s.source === 'request');
    const row = (s) => {
      const ready = s.status === 'available' && s.product && s.product.units[s.unit];
      const sourceText = s.source === 'request' ? 'طلب توفير' : s.order_number ? `من طلب ${s.order_number}` : '';
      return `<div class="line" data-sid="${s.id}">
        <div class="line-top"><div><div class="line-name">${esc(s.name)}</div><div class="small muted">${int(s.qty)} ${UNIT[s.unit] || ''} · ${sourceText}${s.note ? ' · ' + esc(s.note) : ''}</div></div>
        ${ready ? '<span class="item-state available">اتوفر</span>' : s.status === 'available' ? '<span class="item-state partial">اتوفر بوحدة تانية</span>' : '<span class="item-state pending">مستني يتوفر</span>'}</div>
        <div class="row">${ready ? `<button class="btn primary sm" data-add>أضف ${int(s.qty)} ${UNIT[s.unit]} للسلة (${money(s.product.units[s.unit].price)})</button>` : s.status === 'available' && s.product ? '<a class="btn sm" href="#catalog" data-find>شوفه في الجملة</a>' : ''}<span class="spacer"></span><button class="btn ghost sm" data-close>شيله</button></div>
      </div>`;
    };
    box.innerHTML = (fromOrders.length ? `<h3 class="nb-h">ماكانش متوفر في طلباتك</h3><section class="card">${fromOrders.map(row).join('')}</section>` : '')
      + (requests.length ? `<h3 class="nb-h">طلبات التوفير</h3><section class="card">${requests.map(row).join('')}</section>` : '');
    box.querySelectorAll('[data-sid]').forEach((el) => {
      const s = list.find((x) => x.id === Number(el.dataset.sid));
      const add = el.querySelector('[data-add]');
      if (add) add.onclick = () => run(add, async () => { addToCart(s.product, s.unit, s.qty); await api('DELETE', '/api/wholesale/shortages/' + s.id); syncNb(); });
      const find = el.querySelector('[data-find]');
      if (find) find.onclick = () => { state.q = s.product.name; document.getElementById('search').value = s.product.name; };
      el.querySelector('[data-close]').onclick = (e) => run(e.target, async () => { await api('DELETE', '/api/wholesale/shortages/' + s.id); syncNb(); });
    });
  }

  function renderOffline() {
    view.innerHTML = `<div class="empty card" style="padding:28px"><b>مفيش نت</b><span class="muted">الصفحة دي محتاجة نت. دفتر النواقص شغّال من غير نت، وهيتبعت أول ما النت يرجع.</span><a class="btn primary" href="#shortages">افتح دفتر النواقص</a></div>`;
  }

  // -------------------------------------------------------- notifications
  async function renderNotifications() {
    view.innerHTML = '<div class="page-title"><h2>الإشعارات</h2></div><div id="n-list"><p class="empty">بيحمّل…</p></div>';
    const seq = routeSeq;
    const data = await run(null, () => api('GET', '/api/wholesale/notifications'));
    if (!data || stale(seq)) return;
    document.getElementById('n-list').innerHTML = data.items.length ? data.items.map((n) => `
      <${n.order_id ? `a href="#order/${n.order_id}"` : n.kind === 'available' || n.kind === 'notebook' ? 'a href="#shortages"' : n.kind === 'split' ? 'a href="#nearby"' : n.kind === 'plus' ? 'a href="#plus"' : n.kind === 'team' ? 'a href="#team-requests"' : 'div'} class="notice${n.read_at ? '' : ' unread'}" style="text-decoration:none;color:inherit">
        <b>${esc(n.title)}</b>${n.body ? `<span>${esc(n.body)}</span>` : ''}<time>${when(n.created_at)}</time>
      </${n.order_id || ['available', 'notebook', 'split', 'plus', 'team'].includes(n.kind) ? 'a' : 'div'}>`).join('') : '<div class="empty"><b>مفيش إشعارات</b></div>';
    if (data.unread) {
      await api('POST', '/api/wholesale/notifications/read').catch(() => {});
      document.getElementById('notif-count').hidden = true;
      if (state.me) state.me.unread = 0;
    }
  }

  if ('serviceWorker' in navigator && location.pathname.startsWith('/wholesale')) {
    navigator.serviceWorker.register('/wholesale/sw.js', { scope: '/wholesale/' }).catch(() => { /* offline mode is a bonus */ });
  }
  route();
})();
