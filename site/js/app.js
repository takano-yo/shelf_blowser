'use strict';

/* shelf_blowser — 本棚ビュー
 * build が出力した site/data/books.json（ownerCount 降順）を読み込み、
 * シリーズ（親書誌）でまとめて表紙を本棚状に描画する。
 * 詳細検索・件名/分類は現フェーズ対象外（books.json 内の項目のみで表示）。
 */

const DATA_URL = 'data/books.json';
const META_URL = 'data/meta.json';

// 多巻もの判定の閾値（プロトタイプ）。巻数（hasPart 由来の ISBN 数）がこの値以上の
// ものだけを「多巻のシリーズ的書籍」として扱う。上下(2)/上中下(3)/正続(2) のような
// 1 冊を少数に分冊しただけのものは閾値未満となり、シリーズ以外（単独）へ回す。
const MULTI_VOLUME_MIN = 4;

// シリーズ:シリーズ以外 の混在ピッチ。シリーズ的 1 件ごとに、その手前へ置く
// シリーズ以外の冊数を [MIN, MAX] からランダムに選ぶ（平均約 6）。
// 乱数は固定シードで決め打ち（リフレッシュしても並びは不変）。
const MIX_GAP_MIN = 4;
const MIX_GAP_MAX = 8;
const MIX_SEED = 0x9e3779b9;

const els = {
  shelf: document.getElementById('shelf'),
  loading: document.getElementById('shelf-loading'),
  stats: document.getElementById('shelf-stats'),
  overlay: document.getElementById('overlay'),
  overlayBody: document.getElementById('overlay-body'),
  searchForm: document.getElementById('search-form'),
};

// ホバー時に表紙と交代して詳細を表示する使い回しレイヤー（1個を貼り替える）。
const coverDetail = document.createElement('div');
coverDetail.className = 'cover-detail';
coverDetail.setAttribute('aria-hidden', 'true');

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

// 表紙下メタの著者欄用。寄与情報（著・編・校注 など）込みの CiNii Books 生データ
// （creatorRaw）をそのまま表示する。生データが無ければ整形済みの著者名にフォールバック。
function authorRawText(book) {
  if (book.creatorRaw) return book.creatorRaw;
  if (book.creators && book.creators.length) return book.creators.join('、');
  return '著者不明';
}

function publisherText(book) {
  return (book.publishers && book.publishers.length) ? book.publishers.join('、') : '';
}

/* ---------- 出版年→色（年ごとのグラデーション） ----------
 * 出版年に応じてプレースホルダーの地色グラデーション・下辺の帯・表紙下メタの
 * 出版年を着色する。年代でカテゴリ分けせず 1 年ごとに連続変化させる。
 *  - 色相は 古=赤 → 中=緑 → 新=水色。境界は 2000 年（水色↔緑）/ 1950 年（緑↔赤）。
 *  - 鮮やかさ（彩度）は 赤 < 緑 < 水色 の順。古いほど暗く地味（明度を下げる）。
 *  - 地色グラデーションは濃くしすぎず、淡い明るさを保ったまま色相（系統）だけ変える。
 * 出版年が未定義のものは最も古い年（1792）と同等に扱う。
 * HSL 各成分をアンカー（YEAR_STOPS）間で線形補間して求める。 */
const YEAR_OLD = 1792;
const YEAR_STOPS = [
  { y: 2026, h: 190, s: 100, l: 46 }, // 最新: 鮮やかな水色
  { y: 2000, h: 153, s: 43, l: 37 }, // 2000: 水色↔緑の境界（水色側）
  { y: 1975, h: 96, s: 29, l: 35 }, // 1950: 緑↔赤の境界（緑側）
  { y: 1974, h: 16,  s: 52, l: 36 }, // 1950 直下で赤へ切り替え（短い遷移）
  { y: 1950, h: 8,   s: 48, l: 33 }, // 赤
  { y: 1925, h: 5,   s: 42, l: 28 }, // 暗い赤
  { y: 1792, h: 2,   s: 34, l: 22 }, // 最古: 暗く地味な赤
];

function lerp(a, b, t) { return a + (b - a) * t; }

// 未定義は最古年扱い。
function yearOr(year) { return (year == null) ? YEAR_OLD : year; }

function yearHsl(year) {
  const y = yearOr(year);
  const stops = YEAR_STOPS;
  if (y >= stops[0].y) return { ...stops[0] };
  const last = stops[stops.length - 1];
  if (y <= last.y) return { ...last };
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1]; // a.y > b.y
    if (y <= a.y && y >= b.y) {
      const t = (a.y - y) / (a.y - b.y);
      return { h: lerp(a.h, b.h, t), s: lerp(a.s, b.s, t), l: lerp(a.l, b.l, t) };
    }
  }
  return { ...last };
}

/* プレースホルダーの地色グラデーション。濃くしすぎず、元の淡い地色の明るさを保った
 * まま、色相（系統＝赤/緑/水色）だけを出版年で変える。明度・彩度は年によらず一定。 */
function yearBg(year) {
  const h = Math.round(yearHsl(year).h);
  return `linear-gradient(160deg, hsl(${h} 30% 99%) 0%, `
    + `hsl(${h} 45% 97%) 55%, hsl(${h} 52% 93%) 100%)`;
}

/* 出版年から文字色 2 種を返す（未定義は最古扱いで常に色を返す）。
 *  - main: プレースホルダー下辺の帯（出版社/シリーズ名ラベル）と表紙下メタの出版年の文字色
 *  - soft: プレースホルダー下辺の区切り線（淡い同系色） */
function yearColors(year) {
  const c = yearHsl(year);
  const h = Math.round(c.h), s = Math.round(c.s), l = Math.round(c.l);
  const softS = Math.max(s - 24, 12);
  const softL = Math.min(l + 34, 88);
  return {
    main: `hsl(${h} ${s}% ${l}%)`,
    soft: `hsl(${h} ${softS}% ${softL}%)`,
  };
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

  // 「シリーズ的」= シリーズ束（1冊のみも含む） or 多巻単独（巻数 >= 閾値）。
  // 単独と分け、それぞれ所蔵館数順に整列したうえで、シリーズ以外を平均6冊ごとに
  // シリーズ的を1件挟む（間隔 4〜8、固定シード乱数）形で混ぜる。
  const seriesBucket = items.filter(isSeriesLike).sort(byHoldings);
  const soloBucket = items.filter(it => !isSeriesLike(it)).sort(byHoldings);
  return mixBuckets(soloBucket, seriesBucket, mulberry32(MIX_SEED));
}

/* シリーズ的か = シリーズ束 or 多巻単独（巻数 = ISBN 数が閾値以上）。
 * 枠・冊数・紙束の対象、かつ混在の「シリーズ側」バケット判定に使う。
 * 上下/上中下/正続のような少数分冊（閾値未満）は単独側へ回す。 */
function isSeriesLike(it) {
  return it.type === 'series'
    || (it.type === 'single' && it.volumes >= MULTI_VOLUME_MIN);
}

/* 決定的な疑似乱数（mulberry32）。固定シードで毎回同じ並びを再現する。 */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* solo を gap（4〜8）冊ごとに 1 件の series を挟みつつ連結する。
 * 片方が尽きたら、残りはそのまま流し込む（= series が余れば末尾に偏る）。 */
function mixBuckets(solo, series, rng) {
  const span = MIX_GAP_MAX - MIX_GAP_MIN + 1;
  const out = [];
  let i = 0, j = 0;
  while (i < solo.length || j < series.length) {
    const gap = MIX_GAP_MIN + Math.floor(rng() * span);
    for (let k = 0; k < gap && i < solo.length; k++) out.push(solo[i++]);
    if (j < series.length) out.push(series[j++]);
  }
  return out;
}

/* ---------- 描画 ---------- */

function coverHtml(book, label) {
  // label: プレースホルダー下辺の文字（出版社 or シリーズ名）
  if (book.coverUrl) {
    return `<div class="cover"><img class="cover__img" src="${escapeHtml(book.coverUrl)}" loading="lazy" alt="${escapeHtml(book.title)} の表紙"></div>`;
  }
  // プレースホルダーの地色グラデーションと下辺の帯・区切り線に出版年由来の色を適用。
  // タイトル/著者は読みやすさのため通常の濃色のまま（地色は明るめに保つ）。
  const yc = yearColors(book.year);
  const bgStyle = ` style="background:${yearBg(book.year)}"`;
  const bottomStyle = ` style="color:${yc.main};border-top-color:${yc.soft}"`;
  return `
    <div class="cover cover--placeholder"${bgStyle}>
      <div class="ph__top">
        <div class="ph__title">${escapeHtml(book.title)}</div>
        <div class="ph__author">${escapeHtml(authorText(book))}</div>
      </div>
      <div class="ph__spacer"></div>
      <div class="ph__bottom"${bottomStyle}>${escapeHtml(label)}</div>
    </div>`;
}

/* 表紙下メタの「出版社（orシリーズ名）, 出版年」1行を組み立てる。
 * - pub: 出版社名 or シリーズ名（空なら出版年のみ）
 * - isSeries: true ならシリーズ名として水色表示
 * - year: 出版年（null なら年を出さない）
 * pub が長い場合は ellipsis で省略し、", 出版年" は常に末尾へ残す（年は折りたたまない）。
 * 出版年 span には data-year を付与し、将来の「年ごとの色分け」を CSS で拡張できるようにする。 */
function subYearHtml(pub, isSeries, year) {
  const hasYear = year != null;
  if (!pub && !hasYear) return '';
  const pubCls = isSeries ? 'meta__pub meta__pub--series' : 'meta__pub';
  const pubSpan = pub ? `<span class="${pubCls}">${escapeHtml(pub)}</span>` : '';
  // 出版社（orシリーズ名）と出版年の両方があるときだけ区切り ", " を入れる。
  // 区切りカンマは色を付けず（通常の濃色＝黒系）、出版年だけ年代色にする。
  const sepSpan = (pub && hasYear) ? `<span class="meta__sep">, </span>` : '';
  // 出版年はプレースホルダーの出版社ラベルと同じ濃さ（yc.main）で太字表示。
  const yc = yearColors(year);
  const yearSpan = hasYear
    ? `<span class="meta__year" data-year="${escapeHtml(String(year))}" style="color:${yc.main}">${escapeHtml(year + '年')}</span>`
    : '';
  return `<div class="meta__sub">${pubSpan}${sepSpan}${yearSpan}</div>`;
}

function itemHtml(item, idx) {
  const isSeries = item.type === 'series';
  const book = isSeries ? item.rep : item.book;
  // 冊数: シリーズ束 = メンバー数、多巻単独 = 巻数（ISBN 数）
  const count = isSeries ? item.books.length : (item.volumes || 1);
  const seriesLike = isSeriesLike(item); // 枠・冊数表示の対象
  const stacked = seriesLike && count > 1; // 紙束は「シリーズ的かつ2冊以上」のみ（1冊シリーズ・分冊単独には付けない）
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

  // 表紙下メタ: タイトル / 著者 / 「(出版社 or シリーズ名), 出版年」を1行に集約。
  // 出版社（orシリーズ名）が長い場合は ellipsis で省略し、出版年は常に末尾へ残す。
  return `
    <article class="${classes.join(' ')}" data-idx="${idx}" tabindex="0" role="button"
             aria-label="${escapeHtml(book.title)} の詳細を開く">
      ${cover}
      <div class="meta">
        <div class="meta__title">${escapeHtml(book.title)}</div>
        <div class="meta__author">${escapeHtml(authorRawText(book))}</div>
        ${subYearHtml(isSeries ? sName : publisherText(book), isSeries, book.year)}
      </div>
    </article>`;
}

function renderShelf() {
  const html = shelfItems.map((it, i) => itemHtml(it, i)).join('');
  els.shelf.innerHTML = html;
  els.shelf.setAttribute('aria-busy', 'false');
}

/* ---------- 棚板（各段の下のデザイン要素） ---------- */

// 現在の列数。auto-fill では列数が動的で「どこで段が変わるか」が CSS だけでは
// 取れないため、ウィンドウ幅から列数を算出して明示列に固定し、その境目に棚板を敷く。
let shelfCols = 0;

// 1 行に最低限並べる冊数。モバイル等で本来 2 冊以下になる幅では、本を縮めて
// この冊数まで列を増やす（閲覧性確保）。3 冊以上並ぶ幅ではデフォルトサイズのまま。
const MIN_COLUMNS = 3;

// auto-fill 相当の列数を算出: floor((幅 + 列ギャップ) / (最小列幅 + 列ギャップ))。
// ただし下限は MIN_COLUMNS（本来 2 冊以下になる幅でのみ列を増やして本を縮める）。
function computeShelfColumns() {
  const width = els.shelf.clientWidth;
  if (!width) return shelfCols || MIN_COLUMNS;
  const root = getComputedStyle(document.documentElement);
  const rootFont = parseFloat(root.fontSize) || 16;
  const bookWRem = parseFloat(root.getPropertyValue('--book-w')) || 9.5;
  const minW = bookWRem * rootFont;
  const colGap = parseFloat(getComputedStyle(els.shelf).columnGap) || 0;
  return Math.max(MIN_COLUMNS, Math.floor((width + colGap) / (minW + colGap)));
}

// N 枚ごと（＝各段の末尾）に、全幅のグリッド行となる棚板を挿入する。
function layoutShelfBoards(cols) {
  els.shelf.querySelectorAll('.shelf-board').forEach(b => b.remove());
  const cards = els.shelf.querySelectorAll('.book');
  if (!cards.length) return;
  // 走査中の挿入で位置がずれないよう、挿入基準（次段の先頭カード）を先に集める。
  const refs = [];
  for (let i = cols; i < cards.length; i += cols) refs.push(cards[i]);
  for (const ref of refs) ref.before(makeShelfBoard());
  // 最終段の下にも一枚敷く。
  els.shelf.appendChild(makeShelfBoard());
}

function makeShelfBoard() {
  const board = document.createElement('div');
  board.className = 'shelf-board';
  board.setAttribute('aria-hidden', 'true');
  return board;
}

// 列数を明示列として適用し、列数が変わったときだけ棚板を再構築する。
function applyShelfLayout(force) {
  if (!els.shelf.querySelector('.book')) return; // 本が無い（読み込み失敗等）なら何もしない
  const cols = computeShelfColumns();
  els.shelf.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  if (force || cols !== shelfCols) {
    shelfCols = cols;
    layoutShelfBoards(cols);
  }
}

/* ---------- ホバー詳細（表紙と交代して同サイズで表示） ---------- */

function coverDetailHtml(item) {
  const isSeries = item.type === 'series';
  const book = isSeries ? item.rep : item.book;
  const rows = [];
  rows.push(['著者', authorText(book)]);
  if (publisherText(book)) rows.push(['出版社', publisherText(book)]);
  if (book.year != null) rows.push(['出版年', `${book.year}年`]);
  rows.push(['所蔵館数', `${book.ownerCount}`]);
  if (isSeries) {
    rows.push(['シリーズ', `${seriesName(item.rep)}（${item.books.length}冊）`]);
  } else if (item.volumes >= MULTI_VOLUME_MIN) {
    rows.push(['巻数', `${item.volumes}冊（多巻もの）`]);
  } else if (book.series && book.series.length) {
    rows.push(['シリーズ', book.series[0].title]);
  }
  if (book.isbn && book.isbn.length) rows.push(['ISBN', book.isbn[0]]);

  const dl = rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
  return `<div class="cover-detail__title">${escapeHtml(book.title)}</div><dl>${dl}</dl>`;
}

// 表紙(.cover)の上に inset:0 で重ね、フェードインで表紙画像と交代させる。
function showCoverDetail(item, card) {
  const cover = card.querySelector('.cover');
  if (!cover) return;
  // 同じカードに既に表示済みなら作り直さない（カード内の子要素跨ぎのちらつき防止）
  if (coverDetail.parentNode === cover) return;
  coverDetail.innerHTML = coverDetailHtml(item);
  cover.appendChild(coverDetail);
  // 強制リフローで opacity:0 の初期状態を即確定させ、同じフレーム内で is-on を付与。
  // requestAnimationFrame の 1 フレーム待ち（約16ms）を省きフェード開始のラグを削減する。
  void coverDetail.offsetWidth;
  coverDetail.classList.add('is-on');
}

function hideCoverDetail() {
  coverDetail.classList.remove('is-on');
  if (coverDetail.parentNode) coverDetail.parentNode.removeChild(coverDetail);
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
  } else if (item.volumes >= MULTI_VOLUME_MIN) {
    seriesBlock = `<div class="ov__badge">多巻もの：全${item.volumes}冊</div>`;
  } else if (book.series && book.series.length) {
    seriesBlock = `<div class="ov__badge">${escapeHtml(book.series[0].title)}</div>`;
  }

  const coverImg = book.coverUrl
    ? `<img class="ov__cover" src="${escapeHtml(book.coverUrl)}" alt="${escapeHtml(book.title)} の表紙">`
    : '';

  return `
    ${coverImg}
    <h2 class="ov__title" id="ov-title">${escapeHtml(book.title)}</h2>
    <p class="ov__author">${escapeHtml(authorText(book))}</p>
    ${seriesBlock}
    <dl class="ov__dl">${dl}</dl>
    <a class="ov__link" href="${escapeHtml(book.ciniiUrl)}" target="_blank" rel="noopener">CiNii で見る →</a>`;
}

let lastFocused = null;

function openOverlay(item) {
  hideCoverDetail();
  els.overlayBody.innerHTML = overlayHtml(item);
  els.overlay.hidden = false;
  document.body.style.overflow = 'hidden';
  lastFocused = document.activeElement;
  const panel = els.overlay.querySelector('.overlay__panel');
  // 直前のスワイプで残ったインライン変形/スクロール位置をリセット。
  panel.style.transition = '';
  panel.style.transform = '';
  panel.scrollTop = 0;
  panel.focus();
}

function closeOverlay() {
  els.overlay.hidden = true;
  document.body.style.overflow = '';
  const panel = els.overlay.querySelector('.overlay__panel');
  panel.style.transition = '';
  panel.style.transform = '';
  if (lastFocused && lastFocused.focus) lastFocused.focus();
}

// モバイルのボトムシートを下スワイプ（先頭までスクロール済みでの下方向ドラッグ）で
// 閉じられるようにする。内容が途中までスクロールされている間は通常スクロールを優先。
function bindSheetSwipe() {
  const panel = els.overlay.querySelector('.overlay__panel');
  if (!panel) return;
  const DISMISS_PX = 90;          // この距離以上ドラッグで閉じる
  const isSheet = () => window.matchMedia('(max-width: 600px)').matches;
  let startY = 0, startScroll = 0, dragging = false;

  panel.addEventListener('touchstart', (e) => {
    if (els.overlay.hidden || !isSheet() || e.touches.length !== 1) return;
    startY = e.touches[0].clientY;
    startScroll = panel.scrollTop;
    dragging = false;
  }, { passive: true });

  panel.addEventListener('touchmove', (e) => {
    if (els.overlay.hidden || !isSheet() || e.touches.length !== 1) return;
    const dy = e.touches[0].clientY - startY;
    // 先頭まで戻っている状態での下方向ドラッグのときだけシートを動かす。
    if (startScroll <= 0 && dy > 0) {
      dragging = true;
      panel.style.transition = 'none';
      panel.style.transform = `translateY(${dy}px)`;
      e.preventDefault(); // 内部スクロール/バウンスを抑止
    }
  }, { passive: false });

  panel.addEventListener('touchend', (e) => {
    if (!dragging) return;
    dragging = false;
    const dy = e.changedTouches[0].clientY - startY;
    panel.style.transition = 'transform 0.18s ease';
    if (dy > DISMISS_PX) {
      // シートを下へ送り出してから閉じる。
      panel.style.transform = 'translateY(100%)';
      const done = () => { panel.removeEventListener('transitionend', done); closeOverlay(); };
      panel.addEventListener('transitionend', done);
    } else {
      // しきい値未満は元位置へスナップバック。
      panel.style.transform = 'translateY(0)';
    }
  });
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

  // ホバー詳細（表紙と交代して同サイズで表示）。
  // 検出は表紙画像（.cover）の範囲のみ。表紙下の書誌テキストにホバーしても出さない。
  els.shelf.addEventListener('mouseover', (e) => {
    const cover = e.target.closest('.cover');
    if (!cover) return;
    const card = cover.closest('.book');
    if (card) showCoverDetail(shelfItems[+card.dataset.idx], card);
  });
  els.shelf.addEventListener('mouseout', (e) => {
    const cover = e.target.closest('.cover');
    if (!cover) return;
    const to = e.relatedTarget;
    // 表紙の外（書誌テキストや棚の余白、別の表紙）へ出たときに消す。
    // 別の表紙へ直接移った場合は mouseover 側で貼り替わる。
    if (!to || !to.closest || !to.closest('.cover')) hideCoverDetail();
  });

  // オーバーレイを閉じる
  els.overlay.addEventListener('click', (e) => {
    if (e.target.hasAttribute('data-close')) closeOverlay();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !els.overlay.hidden) closeOverlay();
  });
  // モバイル: 下スワイプでボトムシートを閉じる
  bindSheetSwipe();

  // 検索窓はダミー（送信しても何もしない）
  els.searchForm.addEventListener('submit', (e) => {
    e.preventDefault();
    els.stats.textContent = '検索機能は準備中です（現在は全件を所蔵館数の多い順に表示しています）。';
  });

  // ウィンドウ幅変化で列数が変わったら棚板を敷き直す（リサイズ確定後にだけ実行）。
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => applyShelfLayout(false), 120);
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
    applyShelfLayout(true);

    const seriesCount = shelfItems.filter(isSeriesLike).length;
    const total = (meta && meta.total) || books.length;
    els.stats.textContent =
      `全 ${total.toLocaleString()} 件（${shelfItems.length.toLocaleString()} の棚／うちシリーズ・多巻 ${seriesCount.toLocaleString()} 件）を所蔵館数順に、単独およそ6冊ごとにシリーズ・多巻を1件挟んで配置。`;
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
