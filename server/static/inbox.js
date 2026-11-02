/* inbox.js — 승인함 보조(CSP: 외부 파일). 체크 6칸이 차야 [승인]이 제출된다(aria-disabled + submit 차단). 서버가 6칸을 다시 검사한다. */
(function () {
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form[data-card-form]').forEach(function (form) {
      var boxes = form.querySelectorAll('.checks input[name^="chk_"]');
      var hint = form.querySelector('[data-chk-hint]');
      var approve = form.querySelector('[data-needs-checks]');
      if (!boxes.length || !approve) return;
      function sync() {
        var left = Array.prototype.filter.call(boxes, function (b) { return !b.checked; }).length;
        approve.setAttribute('aria-disabled', String(left > 0));
        if (hint) hint.textContent = left > 0 ? '체크 ' + left + '칸 남음' : '체크 완료 — 승인할 수 있습니다';
      }
      boxes.forEach(function (b) { b.addEventListener('change', sync); });
      sync();
    });
    // 캡처 단계: app.js 의 중복 제출 잠금보다 먼저 막아 폼이 잠기지 않게 한다
    document.addEventListener('submit', function (e) {
      if (e.submitter && e.submitter.getAttribute('aria-disabled') === 'true') {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      var card = e.target.closest && e.target.closest('[data-card]');
      if (card) card.setAttribute('aria-busy', 'true');
    }, true);
    // 영상은 한 번에 하나만 재생(카드·상세 동시 노출 방지)
    document.addEventListener('play', function (e) {
      document.querySelectorAll('video').forEach(function (v) { if (v !== e.target) v.pause(); });
    }, true);
  });
})();
