/* status.js — 제작 현황 폴링(CSP: 외부 파일). 3초, 숨김 탭이면 중지, 실패 시 지수 대기(최대 30초). */
(function () {
  var box = document.getElementById('jobs');
  if (!box) return;
  var url = box.dataset.fragment, banner = document.getElementById('poll-banner'), label = document.getElementById('poll-status');
  var BASE = 3000, MAX = 30000, delay = BASE, timer = null, lastOk = new Date();

  function hms(d) { return [d.getHours(), d.getMinutes(), d.getSeconds()].map(function (n) { return String(n).padStart(2, '0'); }).join(':'); }
  // 사용자가 폼을 만지는 중(포커스·체크·처리 중)에는 목록을 갈아끼우지 않는다.
  function busy() {
    if (box.querySelector('[data-plan-form]')) return true; // 기획 승인 폼이 열려 있으면 유지
    if (box.contains(document.activeElement)) return true;
    return !!box.querySelector('form.is-busy, form input:checked');
  }
  function ok(html) {
    lastOk = new Date(); delay = BASE;
    if (banner) banner.hidden = true;
    if (label) label.textContent = '마지막 갱신 ' + hms(lastOk);
    if (!busy()) box.innerHTML = html;
  }
  // 실패 알림은 실패가 시작될 때 한 번만(banner 의 role=alert 가 hidden 해제 시 읽힘). 시각은 상단 표시에만 둔다.
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
