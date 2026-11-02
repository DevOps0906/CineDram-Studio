/* app.js — 셸 보조(메뉴·드로어·다크 모드)와 제작 현황 폴링. 프레임워크 없음, 외부 URL 없음, GET만.
   폴링: 같은 URL을 다시 받아 [data-poll-part] 조각과 바뀐 작업 카드만 바꿔 끼운다.
   포커스·펼친 로그는 유지하고, 상태·개수가 바뀔 때만 스크린 리더에 알린다. */
(function () {
  var app = document.querySelector('.app');
  if (!app) return;
  var root = document.documentElement;
  var themeBtn = document.querySelector('[data-act="theme"]');
  if (themeBtn) themeBtn.setAttribute('aria-pressed', root.classList.contains('dark'));

  var opener = document.querySelector('[data-drawer-opener]');
  var closer = document.querySelector('.aside-close [data-act="drawer"]');
  var aside = document.getElementById('side-panel');
  var menuBtn = document.querySelector('[data-act="menu"]');
  var nav = document.getElementById('main-nav');
  // 드로어는 visibility 전환 중이라 열자마자 focus()가 무시된다 → 보일 때까지 다음 프레임에서 재시도.
  function focusWhenVisible(el, tries) {
    if (!el) return;
    el.focus();
    if (document.activeElement !== el && tries > 0) {
      requestAnimationFrame(function () { focusWhenVisible(el, tries - 1); });
    }
  }
  function setDrawer(open) {
    if (app.classList.contains('drawer-open') === open) return;
    app.classList.toggle('drawer-open', open);
    if (opener) opener.setAttribute('aria-expanded', open);
    if (open) focusWhenVisible(closer, 60);
    else if (opener && (!aside || aside.contains(document.activeElement) || document.activeElement === document.body)) opener.focus();
  }
  function setMenu(open) {
    if (app.classList.contains('menu-open') === open) return;
    app.classList.toggle('menu-open', open);
    if (menuBtn) menuBtn.setAttribute('aria-expanded', open);
    if (!open && menuBtn && nav && nav.contains(document.activeElement)) menuBtn.focus();
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]'); if (!t) return;
    var a = t.dataset.act;
    if (a === 'theme') {
      var d = root.classList.toggle('dark');
      try { localStorage.setItem('cd-theme', d ? 'dark' : 'light'); } catch (err) { /* 저장 불가: 이번 화면만 */ }
      t.setAttribute('aria-pressed', d);
    } else if (a === 'menu') {
      setMenu(!app.classList.contains('menu-open'));
    } else if (a === 'drawer') {
      setDrawer(!app.classList.contains('drawer-open'));
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (app.classList.contains('drawer-open')) setDrawer(false);
    else setMenu(false);
  });

  var area = document.querySelector('[data-poll]');
  if (!area || !window.fetch) return;
  var errBox = document.querySelector('[data-poll-error]');
  var note = document.querySelector('[data-poll-note]');
  var announce = document.querySelector('[data-poll-announce]');
  var last = Date.now();
  var FOCUSABLE = 'a[href], button, summary, input, select, textarea, [tabindex]';

  // 포커스 위치를 (컨테이너 안 n번째 포커스 가능 요소)로 기억한다.
  function rememberFocus(box) {
    var el = document.activeElement;
    if (!el || !box.contains(el)) return null;
    var card = el.closest('[data-job]');
    var scope = card || box;
    return { job: card ? card.dataset.job : null, index: Array.prototype.indexOf.call(scope.querySelectorAll(FOCUSABLE), el) };
  }
  function restoreFocus(box, mark) {
    if (!mark) return;
    var scope = mark.job ? box.querySelector('[data-job="' + mark.job + '"]') : box;
    if (!scope) return;
    var el = scope.querySelectorAll(FOCUSABLE)[mark.index] || scope.querySelector(FOCUSABLE);
    if (el) el.focus({ preventScroll: true });
  }
  function swapInner(box, html) {
    if (box.innerHTML === html) return;
    var mark = rememberFocus(box);
    box.innerHTML = html;
    restoreFocus(box, mark);
  }
  function swapCard(oldCard, newCard) {
    if (oldCard.outerHTML === newCard.outerHTML) return;
    var mark = rememberFocus(oldCard);
    var wasOpen = !!oldCard.querySelector('details[open]');
    oldCard.replaceWith(newCard);
    if (wasOpen && newCard.querySelector('details')) newCard.querySelector('details').open = true;
    restoreFocus(newCard.parentNode, mark);
  }
  function ids(box) {
    return Array.prototype.map.call(box.querySelectorAll('[data-job]'), function (c) { return c.dataset.job; }).join(',');
  }
  function apply(doc) {
    var next = doc.querySelector('[data-poll]');
    if (!next) throw new Error('no area');
    var oldSig = area.dataset.sig;
    if (ids(area) === ids(next) && ids(area)) {
      // 카드 구성이 같으면 바뀐 카드와 묶음 머리만 바꾼다.
      next.querySelectorAll('[data-job]').forEach(function (c) {
        swapCard(area.querySelector('[data-job="' + c.dataset.job + '"]'), document.importNode(c, true));
      });
      area.querySelectorAll('h2').forEach(function (h, i) { var n = next.querySelectorAll('h2')[i]; if (n) swapInner(h, n.innerHTML); });
    } else {
      var open = Array.prototype.map.call(area.querySelectorAll('[data-job] details[open]'), function (d) { return d.closest('[data-job]').dataset.job; });
      swapInner(area, next.innerHTML);
      open.forEach(function (id) { var d = area.querySelector('[data-job="' + id + '"] details'); if (d) d.open = true; });
    }
    area.dataset.sig = next.dataset.sig;
    area.dataset.summary = next.dataset.summary;
    document.querySelectorAll('[data-poll-part]').forEach(function (part) {
      var n = doc.querySelector('[data-poll-part="' + part.dataset.pollPart + '"]');
      if (n) swapInner(part, n.innerHTML);
    });
    if (announce && oldSig !== next.dataset.sig) announce.textContent = '제작 현황이 바뀌었습니다. ' + next.dataset.summary;
  }
  function tick() {
    fetch(location.href, { headers: { Accept: 'text/html' }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        apply(new DOMParser().parseFromString(html, 'text/html'));
        if (area.hasAttribute('data-stale')) { area.removeAttribute('data-stale'); if (announce) announce.textContent = '현황 연결이 복구되었습니다.'; }
        if (errBox) errBox.hidden = true;
        last = Date.now();
      })
      .catch(function () { area.setAttribute('data-stale', ''); if (errBox) errBox.hidden = false; })
      .then(function () {
        if (note) note.textContent = '3초마다 갱신 · 마지막 갱신 ' + Math.round((Date.now() - last) / 1000) + '초 전';
      });
  }
  setInterval(tick, Number(area.dataset.poll) || 3000);
})();
