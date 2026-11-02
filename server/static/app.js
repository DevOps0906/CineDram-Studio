/* app.js — 공용 셸 보조(CSP: 외부 파일). 테마 토글·메뉴/드로어·POST 폼 중복 제출 잠금만 한다. */
(function () {
  var KEY = 'cd-theme';
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  var dark = stored() === 'dark';
  document.documentElement.classList.toggle('dark', dark);

  document.addEventListener('DOMContentLoaded', function () {
    var app = document.querySelector('.app');
    var themeBtn = document.querySelector('[data-act="theme"]');
    if (themeBtn) themeBtn.setAttribute('aria-pressed', String(dark));

    // 드로어: 열면 닫기 버튼으로 포커스, 닫으면 트리거로 복귀, aria-expanded 동기화
    var lastTrigger = null;
    function setDrawer(open) {
      app.classList.toggle('drawer-open', open);
      document.querySelectorAll('.topbar [data-act="drawer"]').forEach(function (b) { b.setAttribute('aria-expanded', String(open)); });
      if (open) {
        var closeBtn = app.querySelector('.aside-close [data-act="drawer"]');
        if (closeBtn) closeBtn.focus();
      } else if (lastTrigger) {
        lastTrigger.focus();
        lastTrigger = null;
      }
    }

    document.addEventListener('click', function (e) {
      var t = e.target.closest('[data-act]');
      if (!t) return;
      var a = t.dataset.act;
      if (a === 'theme') {
        var d = document.documentElement.classList.toggle('dark');
        try { localStorage.setItem(KEY, d ? 'dark' : 'light'); } catch (err) { /* 저장 불가해도 화면은 바뀐다 */ }
        t.setAttribute('aria-pressed', String(d));
      } else if (a === 'menu' && app) {
        t.setAttribute('aria-expanded', String(app.classList.toggle('menu-open')));
      } else if (a === 'drawer' && app) {
        var opening = !app.classList.contains('drawer-open');
        if (opening && t.closest('.topbar')) lastTrigger = t;
        setDrawer(opening);
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && app && app.classList.contains('drawer-open')) setDrawer(false);
    });

    // data-lock 폼: 첫 제출만 통과, 누른 버튼에 스피너, 나머지 버튼 비활성(DESIGN §3.3 처리 중 배리언트)
    document.addEventListener('submit', function (e) {
      var f = e.target;
      if (!f.matches || !f.matches('form[data-lock]')) return;
      if (f.classList.contains('is-busy')) { e.preventDefault(); return; }
      f.classList.add('is-busy');
      var pressed = e.submitter;
      if (pressed) pressed.setAttribute('aria-busy', 'true');
      setTimeout(function () {
        f.querySelectorAll('button').forEach(function (b) { if (b !== pressed) b.disabled = true; });
      }, 0);
    });
  });
})();
