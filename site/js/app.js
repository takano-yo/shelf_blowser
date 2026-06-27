'use strict';

/* shelf_blowser — 本棚ビュー
 * build が出力した site/data/books.json（ownerCount 降順）を読み込み、
 * シリーズ（親書誌）でまとめて表紙を本棚状に描画する。
 * 詳細検索・件名/分類は現フェーズ対象外（books.json 内の項目のみで表示）。
 */

const DATA_URL = 'data/books.json';
const META_URL = 'data/meta.json';

const els = {
  shelf: document.getElementById('shelf'),
  loading: document.getElementById('shelf-loading'),
  stats: document.getElementById('shelf-stats'),
  tooltip: document.getElementById('tooltip'),
  overlay: document.getElementById('overlay'),
  overlayBody: document.getElementById('overlay-body'),
  searchForm: document.getElementById('search-form'),
};

let shelfItems = []; // 描画単位（単独本 or シリーズ束）

/* ---------- ユーティリティ ---------- */

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function authorText(book) {
  if (book.creators && book.creators.length) return book.creators.join('、');
  if (book.creatorRaw) return book.creatorRaw;
  return '著者不明';
}

function publisherText(book) {
  return (book.publishers && book.publishers.length) ? book.publishers.join('、') : '';
}

/* シリーズ名を巻号情報から切り出す（"岩波新書, 青版-434" → "岩波新書"、
 * "越境する知 / 栗原彬 [ほか] 編, 5" → "越境する知"） */
function seriesName(item) {
  const raw = (item.series && item.series[0] && item.series[0].title) || '';
  const cut = raw.split(/\s*[／/,，、]\s*/)[0].trim();
  return cut || raw;
}

/* ---------- データ整形（シリーズまとめ） ---------- */

function buildShelfItems(books) {
  const groups = new Map(); // 親NCID -> グループ
  const items = [];

  for (const b of books) {
    if (b.series && b.series.length && b.series[0].id) {
      const pid = b.series[0].id;
      let g = groups.get(pid);
      if (!g) {
        g = { type: 'series', parentId: pid, books: [], rep: null, maxOwner: 0 };
        groups.set(pid, g);
        items.push(g);
      }
      g.books.push(b);
    } else {
      // 単独レコード。hasPart 由来の ISBN が複数 = 多巻もの（巻数 = ISBN 数）。
      items.push({ type: 'single', book: b, volumes: (b.isbn && b.isbn.length) || 0 });
    }
  }

  // 各シリーズの代表 = 所蔵館数（ownerCount）最大の1冊。位置基準も同値。
  for (const g of groups.values()) {
    g.books.sort((a, b) =>
      (b.ownerCount - a.ownerCount) || (a.ncid < b.ncid ? -1 : a.ncid > b.ncid ? 1 : 0)
    );
    g.rep = g.books[0];
    g.maxOwner = g.rep.ownerCount;
  }

  // 並び替えキー（所蔵館数降順、同値は ncid 昇順 = build と同じ決定規則）
  const ownerOf = (it) => it.type === 'series' ? it.maxOwner : it.book.ownerCount;
  const ncidOf = (it) => it.type === 'series' ? it.rep.ncid : it.book.ncid;
  const byHoldings = (a, b) => {
    const d = ownerOf(b) - ownerOf(a);
    if (d) return d;
    const na = ncidOf(a), nb = ncidOf(b);
    return na < nb ? -1 : na > nb ? 1 : 0;
  };

  // 「シリーズ的」= シリーズ束（1冊のみも含む） or 多巻単独（ISBN が複数）。
  // 単独単巻と分け、それぞれ所蔵館数順に整列したうえで 単独5 : シリーズ的1 で混ぜる。
  const seriesBucket = items.filter(isSeriesLike).sort(byHoldings);
  const soloBucket = items.filter(it => !isSeriesLike(it)).sort(byHoldings);
  return interleave(soloBucket, seriesBucket, 5, 1);
}

/* シリーズ的か = シリーズ束 or 多巻単独（複数巻 = ISBN 複数）。
 * 枠・冊数表示の対象、かつ 5:1 混在の「1」側バケットの判定に使う。 */
function isSeriesLike(it) {
  return it.type === 'series' || (it.type === 'single' && it.volumes > 1);
}

/* many を manyN 個、few を fewN 個ずつ交互に取り出して 1 本の配列にする。
 * 片方が尽きたら、残りはもう片方をそのまま流し込む（= 末尾に偏る）。 */
function interleave(many, few, manyN, fewN) {
  const out = [];
  let i = 0, j = 0;
  while (i < many.length || j < few.length) {
    for (let k = 0; k < manyN && i < many.length; k++) out.push(many[i++]);
    for (let k = 0; k < fewN && j < few.length; k++) out.push(few[j++]);
  }
  return out;
}

/* ---------- 描画 ---------- */

function coverHtml(book, label) {
  // label: プレースホルダー下辺の文字（出版社 or シリーズ名）
  if (book.coverUrl) {
    return `<div class="cover"><img class="cover__img" src="${escapeHtml(book.coverUrl)}" loading="lazy" alt="${escapeHtml(book.title)} の表紙"></div>`;
  }
  return `
    <div class="cover cover--placeholder">
      <div class="ph__top">
        <div class="ph__title">${escapeHtml(book.title)}</div>
        <div class="ph__author">${escapeHtml(authorText(book))}</div>
      </div>
      <div class="ph__spacer"></div>
      <div class="ph__bottom">${escapeHtml(label)}</div>
    </div>`;
}

function itemHtml(item, idx) {
  const isSeries = item.type === 'series';
  const book = isSeries ? item.rep : item.book;
  // 冊数: シリーズ束 = メンバー数、多巻単独 = 巻数（ISBN 数）
  const count = isSeries ? item.books.length : (item.volumes || 1);
  const seriesLike = isSeriesLike(item); // 枠・冊数表示の対象
  const stacked = count > 1;             // 紙束の影は 2 冊以上のみ（1 冊シリーズには付けない）
  const sName = isSeries ? seriesName(item.rep) : '';

  const classes = ['book'];
  if (seriesLike) classes.push('book--series');
  if (stacked) classes.push('book--stacked');

  // プレースホルダー下辺: シリーズならシリーズ名、単独なら出版社
  const phLabel = isSeries ? sName : publisherText(book);

  let cover = coverHtml(book, phLabel);
  if (seriesLike) {
    cover = cover.replace('</div>', `<span class="cover__count">${count}冊</span></div>`);
  }

  // 表紙下メタ: タイトル / 著者 / (出版社 or シリーズ名) / 出版年
  const sub = isSeries
    ? `<div class="meta__sub meta__sub--series">${escapeHtml(sName)}</div>`
    : `<div class="meta__sub">${escapeHtml(publisherText(book))}</div>`;
  const yearText = book.year != null ? `${book.year}年` : '';

  return `
    <article class="${classes.join(' ')}" data-idx="${idx}" tabindex="0" role="button"
             aria-label="${escapeHtml(book.title)} の詳細を開く">
      ${cover}
      <div class="meta">
        <div class="meta__title">${escapeHtml(book.title)}</div>
        <div class="meta__author">${escapeHtml(authorText(book))}</div>
        ${sub}
        ${yearText ? `<div class="meta__sub meta__year">${yearText}</div>` : ''}
      </div>
    </article>`;
}

function renderShelf() {
  const html = shelfItems.map((it, i) => itemHtml(it, i)).join('');
  els.shelf.innerHTML = html;
  els.shelf.setAttribute('aria-busy', 'false');
}

/* ---------- ホバー詳細ツールチップ ---------- */

function tooltipHtml(item) {
  const isSeries = item.type === 'series';
  const book = isSeries ? item.rep : item.book;
  const rows = [];
  rows.push(['著者', authorText(book)]);
  if (publisherText(book)) rows.push(['出版社', publisherText(book)]);
  if (book.year != null) rows.push(['出版年', `${book.year}年`]);
  rows.push(['所蔵館数', `${book.ownerCount}`]);
  if (isSeries) {
    rows.push(['シリーズ', `${seriesName(item.rep)}（${item.books.length}冊）`]);
  } else if (item.volumes > 1) {
    rows.push(['巻数', `${item.volumes}冊（多巻もの）`]);
  } else if (book.series && book.series.length) {
    rows.push(['シリーズ', book.series[0].title]);
  }
  if (book.isbn && book.isbn.length) rows.push(['ISBN', book.isbn[0]]);

  const dl = rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
  return `<div class="tooltip__title">${escapeHtml(book.title)}</div><dl>${dl}</dl>`;
}

function showTooltip(item, x, y) {
  els.tooltip.innerHTML = tooltipHtml(item);
  els.tooltip.hidden = false;
  positionTooltip(x, y);
}

function positionTooltip(x, y) {
  const pad = 14;
  const t = els.tooltip;
  const w = t.offsetWidth, h = t.offsetHeight;
  let left = x + pad, top = y + pad;
  if (left + w > window.innerWidth - pad) left = x - w - pad;
  if (top + h > window.innerHeight - pad) top = y - h - pad;
  t.style.left = Math.max(pad, left) + 'px';
  t.style.top = Math.max(pad, top) + 'px';
}

function hideTooltip() {
  els.tooltip.hidden = true;
}

/* ---------- 詳細オーバーレイ ---------- */

function overlayHtml(item) {
  const isSeries = item.type === 'series';
  const book = isSeries ? item.rep : item.book;
  const rows = [];
  if (publisherText(book)) rows.push(['出版社', escapeHtml(publisherText(book))]);
  if (book.year != null) rows.push(['出版年', `${book.year}年`]);
  rows.push(['所蔵館数', `${book.ownerCount} 館`]);
  if (book.isbn && book.isbn.length) rows.push(['ISBN', book.isbn.map(escapeHtml).join('<br>')]);
  if (book.creatorRaw) rows.push(['著者表記', escapeHtml(book.creatorRaw)]);
  rows.push(['NCID', escapeHtml(book.ncid)]);

  const dl = rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${v}</dd>`).join('');

  let seriesBlock = '';
  if (isSeries) {
    const list = item.books
      .map(b => `<li>${escapeHtml(b.title)}（所蔵 ${b.ownerCount}）</li>`)
      .join('');
    seriesBlock = `
      <div class="ov__badge">シリーズ：${escapeHtml(seriesName(item.rep))}（${item.books.length}冊）</div>
      <p class="ov__note">所蔵館数が最も多い1冊を代表表示しています。</p>
      <ul class="ov__series-list">${list}</ul>`;
  } else if (item.volumes > 1) {
    seriesBlock = `<div class="ov__badge">多巻もの：全${item.volumes}冊</div>`;
  } else if (book.series && book.series.length) {
    seriesBlock = `<div class="ov__badge">${escapeHtml(book.series[0].title)}</div>`;
  }

  return `
    <h2 class="ov__title" id="ov-title">${escapeHtml(book.title)}</h2>
    <p class="ov__author">${escapeHtml(authorText(book))}</p>
    ${seriesBlock}
    <dl class="ov__dl">${dl}</dl>
    <a class="ov__link" href="${escapeHtml(book.ciniiUrl)}" target="_blank" rel="noopener">CiNii で見る →</a>`;
}

let lastFocused = null;

function openOverlay(item) {
  hideTooltip();
  els.overlayBody.innerHTML = overlayHtml(item);
  els.overlay.hidden = false;
  document.body.style.overflow = 'hidden';
  lastFocused = document.activeElement;
  els.overlay.querySelector('.overlay__panel').focus();
}

function closeOverlay() {
  els.overlay.hidden = true;
  document.body.style.overflow = '';
  if (lastFocused && lastFocused.focus) lastFocused.focus();
}

/* ---------- イベント ---------- */

function bindEvents() {
  // カードを開く（クリック / Enter・Space）
  els.shelf.addEventListener('click', (e) => {
    const card = e.target.closest('.book');
    if (card) openOverlay(shelfItems[+card.dataset.idx]);
  });
  els.shelf.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.book');
    if (card) { e.preventDefault(); openOverlay(shelfItems[+card.dataset.idx]); }
  });

  // ホバー詳細
  els.shelf.addEventListener('mouseover', (e) => {
    const card = e.target.closest('.book');
    if (card) showTooltip(shelfItems[+card.dataset.idx], e.clientX, e.clientY);
  });
  els.shelf.addEventListener('mousemove', (e) => {
    if (!els.tooltip.hidden) positionTooltip(e.clientX, e.clientY);
  });
  els.shelf.addEventListener('mouseout', (e) => {
    const to = e.relatedTarget;
    if (!to || !to.closest || !to.closest('.book')) hideTooltip();
  });

  // オーバーレイを閉じる
  els.overlay.addEventListener('click', (e) => {
    if (e.target.hasAttribute('data-close')) closeOverlay();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !els.overlay.hidden) closeOverlay();
  });

  // 検索窓はダミー（送信しても何もしない）
  els.searchForm.addEventListener('submit', (e) => {
    e.preventDefault();
    els.stats.textContent = '検索機能は準備中です（現在は全件を所蔵館数の多い順に表示しています）。';
  });
}

/* ---------- 起動 ---------- */

async function init() {
  bindEvents();
  try {
    const [books, meta] = await Promise.all([
      fetch(DATA_URL).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }),
      fetch(META_URL).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);

    shelfItems = buildShelfItems(books);
    renderShelf();

    const seriesCount = shelfItems.filter(isSeriesLike).length;
    const total = (meta && meta.total) || books.length;
    els.stats.textContent =
      `全 ${total.toLocaleString()} 件（${shelfItems.length.toLocaleString()} の棚／うちシリーズ・多巻 ${seriesCount.toLocaleString()} 件）を所蔵館数順に、単独5:シリーズ1 の割合で配置。`;
  } catch (err) {
    els.shelf.setAttribute('aria-busy', 'false');
    els.shelf.innerHTML =
      `<p class="shelf__empty">データの読み込みに失敗しました（${escapeHtml(String(err.message || err))}）。<br>build を実行して <code>site/data/books.json</code> を生成してください。</p>`;
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
