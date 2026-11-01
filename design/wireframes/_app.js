/* _app.js — 시안 공통 셸: 아이콘 스프라이트(Lucide 모양 단순화), 메뉴·하단 탭, 라이트/다크, 드로어, 상태 전환.
   외부 URL 없음. 페이지는 <main> 과 선택적 <aside class="aside"> 만 쓴다. */
(function () {
  var P = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>',
    type: '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>',
    link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/>',
    home: '<path d="M3 10l9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
    close: '<path d="M18 6L6 18M6 6l12 12"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    warn: '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    x: '<path d="M18 6L6 18M6 6l12 12"/>',
    circlecheck: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    scaneye: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="1.5"/><path d="M18.5 12s-2.5 4.5-6.5 4.5S5.5 12 5.5 12 8 7.5 12 7.5s6.5 4.5 6.5 4.5z"/>',
    filex: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9.5 12.5l5 5M14.5 12.5l-5 5"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v4M12 16h.01"/>',
    disk: '<path d="M22 12H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/><path d="M6 16h.01M10 16h.01"/>',
    play: '<path d="M6 4l14 8-14 8z"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
    loader: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
    pencil: '<path d="M17 3l4 4L7 21H3v-4z"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',
    film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    layers: '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
    key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M16 7l3 3M19 4l2 2"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    tv: '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 9l5 3-5 3z"/>',
    up: '<path d="M18 15l-6-6-6 6"/>',
    down: '<path d="M6 9l6 6 6-6"/>',
    top: '<path d="M17 11l-5-5-5 5M17 18l-5-5-5 5"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    gauge: '<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0z"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
    star: '<path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>'
  };
  window.icon = function (n, extra) { return '<svg class="ico ' + (extra || '') + '" viewBox="0 0 24 24" aria-hidden="true">' + (P[n] || '') + '</svg>'; };
  function fillIcons(root) {
    root.querySelectorAll('i[data-i]').forEach(function (el) {
      var span = document.createElement('span'); span.innerHTML = icon(el.dataset.i, el.className); el.replaceWith(span.firstChild);
    });
  }

  // 라이트/다크 — ?dark 또는 저장값
  var qs = new URLSearchParams(location.search);
  var dark = qs.has('dark') || (localStorage.getItem('cd-theme') === 'dark' && !qs.has('light'));
  document.documentElement.classList.toggle('dark', dark);

  var page = document.body.dataset.page;
  // docs/15 §2-1 사이드바 정보구조: [그룹, [[페이지 id, href(null=준비 중), 아이콘, 이름, 개수|태그]]]
  // 관문 ①②는 메뉴가 아니라 제작 흐름 안의 단계라 메뉴에서 뺀다(해당 화면에서는 원클릭 제작을 현재로 표시).
  var GROUPS = [
    ['홈', [['dashboard', 'dashboard.html', 'home', '홈']]],
    ['제작', [['oneclick', 'oneclick.html', 'zap', '원클릭 제작'], ['create', 'create.html', 'plus', '만들기'], ['link', null, 'link', '링크로 만들기'],
      ['queue', 'queue.html', 'layers', '대기 큐', 7], ['status', 'status.html', 'activity', '제작 현황', 5]]],
    ['검수', [['inbox', 'inbox.html', 'inbox', '승인함', 3]]],
    ['소재', [['radar', 'radar.html', 'radar', '레이더', '후속']]],
    ['성과', [['archive', 'report.html?tab=archive', 'film', '영상 보관함', '후속'], ['report', 'report.html', 'activity', '채널 성적표', '후속']]],
    ['권리', [['risk', 'risk.html', 'shield', '리스크 기록']]],
    ['설정', [['channels', 'channels.html', 'tv', '채널 관리'], ['settings', 'settings.html', 'gear', '환경설정']]]
  ];
  var cur = ({ 'gate-topic': 'oneclick', 'gate-title': 'oneclick' })[page] || page;

  var main = document.querySelector('main');
  var aside = document.querySelector('aside.aside');
  var app = document.createElement('div'); app.className = 'app';

  var nav = '<nav class="sidebar" aria-label="주 메뉴"><div class="brand">' + icon('film') + ' <span class="brand-text">CineDram Studio</span></div>';
  GROUPS.forEach(function (g, gi) {
    nav += (gi ? '<div class="nav-sep"></div>' : '') + (g[0] !== '홈' ? '<span class="nav-group">' + g[0] + '</span>' : '');
    g[1].forEach(function (n) {
      if (!n[1]) { nav += '<a class="nav-item" aria-disabled="true" title="' + n[3] + ' · 준비 중">' + icon(n[2]) + '<span class="nav-text">' + n[3] + ' · 준비 중</span></a>'; return; }
      var tag = typeof n[4] === 'number' ? '<span class="count' + (n[0] === 'inbox' ? ' hot' : '') + '">' + n[4] + '</span>' : n[4] ? '<span class="count">' + n[4] + '</span>' : '';
      nav += '<a class="nav-item" href="' + n[1] + '"' + (n[0] === cur ? ' aria-current="page"' : '') + ' title="' + n[3] + '">' + icon(n[2]) + '<span class="nav-text">' + n[3] + '</span>' + tag + '</a>';
    });
  });
  nav += '</nav>';

  // 기본 우측 열 = 제작 현황 패널 (aside[data-status-panel])
  if (aside && aside.hasAttribute('data-status-panel')) {
    var STEPS = ['소재', '원본', '제작', '검수', '업로드'];
    var bar = function (now) {
      return '<ol class="progress" aria-label="진행 단계">' + STEPS.map(function (s, i) {
        return '<li class="' + (i < now ? 'done' : i === now ? 'now' : '') + '"' + (i === now ? ' aria-current="step"' : '') + '><span class="step-name">' + s + '</span></li>';
      }).join('') + '</ol>';
    };
    var job = function (name, comp, time, now, badge) {
      return '<div class="card"><div class="card-head"><span class="badge b-comp">' + comp + '</span><strong>' + name + '</strong><span class="end label muted num">' + time + '</span></div>' +
        bar(now) + (badge || '') + '</div>';
    };
    aside.innerHTML = '<div class="aside-inner"><div class="aside-head"><h2 class="section-title">제작 현황</h2><span class="label muted">3초마다 갱신</span></div>' +
      '<h3 class="label muted">진행 중 <span class="count">2</span></h3>' +
      job('달빛 수선소 · E04', '한드짜', '11분 7초', 2) +
      job('서쪽 정류장의 겨울 · E02', '감동 반전', '3분 40초', 1, '<span class="badge b-nosrc"><i data-i="filex"></i>원본 없음</span>') +
      '<h3 class="label muted">승인 대기 <span class="count hot">3</span></h3>' +
      job('유리정원 사무소 · E07', '한드짜', '49분', 3, '<span class="badge b-review"><i data-i="scaneye"></i>업로드 검토 필요</span>') +
      job('파도 위 도서관 · E11', '1인칭 나레이션', '1시간 2분', 3, '<span class="badge b-rights"><i data-i="shield"></i>권리 미확인</span>') +
      '<a class="btn btn-secondary btn-block" href="status.html">전체 현황 보기</a></div>';
  }

  var hasAside = !!aside;
  var asideLabel = (aside && aside.dataset.label) || '제작 현황';
  var top = '<header class="topbar">' +
    '<button class="icon-btn only-tablet" data-act="menu" aria-label="메뉴 펼치기" aria-expanded="false">' + icon('menu') + '</button>' +
    '<span class="brand">' + icon('film') + ' CineDram</span><span class="spacer"></span>' +
    (hasAside ? '<button class="icon-btn only-tablet" data-act="drawer" aria-expanded="false">' + icon('panel') + '<span class="label">' + asideLabel + '</span></button>' : '') +
    '<a class="icon-btn only-mobile" href="queue.html" aria-label="대기 큐"' + (page === 'queue' ? ' aria-current="page"' : '') + '>' + icon('layers') + '</a>' +
    '<a class="icon-btn only-mobile" href="settings.html" aria-label="환경설정"' + (page === 'settings' ? ' aria-current="page"' : '') + '>' + icon('gear') + '</a>' +
    '<button class="icon-btn" data-act="theme" aria-pressed="' + dark + '" aria-label="다크 모드">' + icon(dark ? 'sun' : 'moon') + '</button></header>';

  var tabs = '<nav class="tabs" aria-label="하단 탭">' +
    '<a href="dashboard.html"' + (['dashboard', 'oneclick', 'create', 'gate-topic', 'gate-title'].indexOf(page) > -1 ? ' aria-current="page"' : '') + '>' + icon('home') + '홈</a>' +
    '<a href="status.html"' + (page === 'status' ? ' aria-current="page"' : '') + '>' + icon('activity') + '제작 현황</a>' +
    '<a href="inbox.html"' + (page === 'inbox' ? ' aria-current="page"' : '') + '>' + icon('inbox') + '승인함<span class="count hot">3</span></a></nav>';

  app.innerHTML = nav + '<div class="center">' + top + '</div><div class="scrim" data-act="drawer"></div>' + tabs;
  app.querySelector('.center').appendChild(main);
  if (aside) {
    aside.insertAdjacentHTML('afterbegin', '<div class="only-tablet aside-close"><button class="icon-btn" data-act="drawer" aria-label="닫기">' + icon('close') + '</button></div>');
    app.insertBefore(aside, app.querySelector('.scrim'));
  }
  document.body.prepend(app);
  fillIcons(document);

  // 상태 전환(빈·로딩·오류) — ?state=empty|loading|error
  var st = qs.get('state') || 'normal';
  if (document.querySelector('[data-state-view]')) document.body.dataset.state = st;
  document.querySelectorAll('[data-set-state]').forEach(function (b) {
    b.setAttribute('aria-pressed', b.dataset.setState === st);
    b.addEventListener('click', function () {
      document.body.dataset.state = b.dataset.setState;
      document.querySelectorAll('[data-set-state]').forEach(function (x) { x.setAttribute('aria-pressed', x === b); });
    });
  });

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]'); if (!t) return;
    var a = t.dataset.act;
    if (a === 'theme') {
      var d = document.documentElement.classList.toggle('dark');
      localStorage.setItem('cd-theme', d ? 'dark' : 'light');
      t.setAttribute('aria-pressed', d); t.innerHTML = icon(d ? 'sun' : 'moon');
    } else if (a === 'menu') {
      var o = app.classList.toggle('menu-open'); t.setAttribute('aria-expanded', o);
    } else if (a === 'drawer') {
      var dOpen = app.classList.toggle('drawer-open');
      app.querySelectorAll('.topbar [data-act="drawer"]').forEach(function (b) { b.setAttribute('aria-expanded', dOpen); });
    } else if (a === 'sheet') {
      document.querySelectorAll('.sheet, .sheet-scrim').forEach(function (s) { s.classList.toggle('open'); });
    } else if (a === 'pill') {
      t.parentElement.querySelectorAll('[data-act="pill"]').forEach(function (x) { x.setAttribute('aria-pressed', x === t); });
    }
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { app.classList.remove('drawer-open'); document.querySelectorAll('.sheet.open, .sheet-scrim.open').forEach(function (s) { s.classList.remove('open'); }); } });
})();
