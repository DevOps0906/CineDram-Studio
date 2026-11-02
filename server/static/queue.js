/* queue.js — 대기 큐 폴링·취소 확인(CSP: 외부 파일). 3초, 숨김 탭이면 중지, 실패 시 지수 대기(최대 30초). */
(function () {
  var box = document.getElementById('jobs');
  if (!box) return;
  var url = box.dataset.fragment, banner = document.getElementById('poll-banner'), label = document.getElementById('poll-status');
  var BASE = 3000, MAX = 30000, delay = BASE, timer = null;

  // 취소처럼 되돌릴 수 없는 버튼: 확인을 거절하면 제출(과 data-lock 잠금)을 막는다. 캡처 단계라 app.js 보다 먼저 돈다.
  document.addEventListener('submit', function (e) {
    var b = e.submitter, msg = b && b.dataset && b.dataset.confirm;
    if (msg && !window.confirm(msg)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  function hms(d) { return [d.getHours(), d.getMinutes(), d.getSeconds()].map(function (n) { return String(n).padStart(2, '0'); }).join(':'); }
  // 포커스가 목록 안에 있거나 폼이 처리 중이면 목록을 갈아끼우지 않는다.
  function busy() { return box.contains(document.activeElement) || !!box.querySelector('form.is-busy'); }
  function ok(html) {
    delay = BASE;
    if (banner) banner.hidden = true;
    if (label) label.textContent = '마지막 갱신 ' + hms(new Date());
    if (!busy()) box.innerHTML = html;
  }
  // 실패 알림은 실패가 시작될 때 한 번만(banner 의 role=alert 가 hidden 해제 시 읽힘).
  function fail() {
    delay = Math.min(delay * 2, MAX);
    if (banner) banner.hidden = false;
  }
  function schedule() { if (!timer && !document.hidden) timer = setTimeout(tick, delay); }
  function tick() {
    timer = null;
    if (document.hidden) return;
    fetch(url, { headers: { 'Accept': 'text/html' }, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(ok, fail)
      .then(schedule);
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { delay = BASE; schedule(); } });
  schedule();
})();
