'use strict';

/* shelf_blowser — 本棚ビュー
 * build が出力した site/data/books.json（ownerCount 降順）を読み込み、
 * シリーズ（親書誌）でまとめて表紙を本棚状に描画する。
 * 詳細検索・件名/分類は現フェーズ対象外（books.json 内の項目のみで表示）。
 */

const DATA_URL = 'data/books.json';

// 動的検索 API のエンドポイント（server/app.py が提供）。同一オリジン配信を既定とし、
// 別オリジンのバックエンド（例: GitHub Pages のフロント + 別ホストの API）に置く
// 場合はここを絶対 URL に変えるだけでよい。返る JSON は books.json と同一スキーマ。
const SEARCH_URL = 'api/search';

// 多巻もの判定の閾値（プロトタイプ）。巻数（hasPart 由来の ISBN 数）がこの値以上の
// ものだけを「多巻のシリーズ的書籍」として扱う。上下(2)/上中下(3)/正続(2) のような
// 1 冊を少数に分冊しただけのものは閾値未満となり、シリーズ以外（単独）へ回す。
const MULTI_VOLUME_MIN = 4;

// 混在方式（ブロック配分）。4〜8冊（平均6）を 1 ブロックとし、各ブロックへ
// 編集書 1 冊・シリーズ的 1 冊を入れ、残りを単著/共著で埋める（編集書・シリーズの
// ブロック内位置はランダム）。単著/共著が尽きるまでブロックを繰り返す＝この間
// 編集書とシリーズは同割合（1:1）で混ざる。単著/共著が尽きたら、残りの編集書と
// シリーズを冊数比率に合わせて混ぜ末尾へ付ける。乱数は固定シードで再現可能
// （リフレッシュしても並びは不変）。
const MIX_GAP_MIN = 4; // 1 ブロックの総冊数の下限
const MIX_GAP_MAX = 8; // 同・上限
const MIX_SEED = 0x9e3779b9;

// 無限スクロールの1ページあたりの件数。初回表示・タブ切替・並べ替え・検索後は
// 常に先頭からこの件数だけ描画し、センチネル（画面下の監視要素）が見えるたびに
// 次の PAGE_SIZE 件を追加描画する。
const PAGE_SIZE = 100;

const els = {
  shelf: document.getElementById('shelf'),
  loading: document.getElementById('shelf-loading'),
  sentinel: document.getElementById('shelf-sentinel'),
  overlay: document.getElementById('overlay'),
  overlayBody: document.getElementById('overlay-body'),
  searchbarInner: document.querySelector('.searchbar__inner'),
  searchForm: document.getElementById('search-form'),
  searchInput: document.getElementById('search-input'),
  tabs: document.getElementById('tabs'),
  sort: document.querySelector('.sort'),
  sortSelect: document.getElementById('sort-select'),
  seriesToggle: document.getElementById('series-toggle'),
  groupToggle: document.getElementById('series-group-toggle'),
};

// ホバー時に表紙と交代して詳細を表示する使い回しレイヤー（1個を貼り替える）。
const coverDetail = document.createElement('div');
coverDetail.className = 'cover-detail';
coverDetail.setAttribute('aria-hidden', 'true');

let shelfItems = [];     // 現在表示中タブ＋並べ替え適用後のアイテム（カードの data-idx の参照先）
let renderedCount = 0;   // shelfItems のうち現在 DOM に描画済みの件数（先頭からの累積）
let tabItems = { all: [], personal: [], editorial: [], series: [] }; // タブ別アイテム集合（並べ替え前の基準順）
let activeTab = 'all';
let sortMode = 'default'; // 並べ替えモード（default | year-asc | year-desc）。タブ切替後も保持。
// シリーズタブ専用: まとめ解除（シリーズ束を各巻に分け、枠・冊数・紙束を外す）。
// 多巻もの（単独の多巻）は分割できないため単位はそのままで枠・冊数のみ外す。
let seriesUngrouped = false;
const scrollByTab = Object.create(null); // タブ -> 直近のスクロール位置（切替時に保存/復元）

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

function authorName(book) {
  if (book.creators && book.creators.length) return book.creators.join('、');
  if (book.creatorRaw) return book.creatorRaw;
  return '';
}

function authorText(book) {
  return authorName(book) || '著者不明';
}

// 表紙下メタの著者欄用。寄与情報（著・編・校注 など）込みの CiNii Books 生データ
// （creatorRaw）をそのまま表示する。生データが無ければ整形済みの著者名にフォールバック。
function authorRawName(book) {
  if (book.creatorRaw) return book.creatorRaw;
  if (book.creators && book.creators.length) return book.creators.join('、');
  return '';
}

function authorRawText(book) {
  return authorRawName(book) || '著者不明';
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
 *  - main: 表紙下メタの出版年・シリーズ名の文字色
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

/* プレースホルダー下辺（出版社/シリーズ名ラベル）専用の文字色。
 * 色相は出版年の色相をそのまま基本色として使うが、彩度・明度を低く固定し、
 * 年によらず黒系の文字として読めるようにする（彩度・明度のばらつきを排除）。 */
function yearTextColor(year) {
  const h = Math.round(yearHsl(year).h);
  return `hsl(${h} 35% 20%)`;
}

/* シリーズ名を巻号情報から切り出す（"岩波新書, 青版-434" → "岩波新書"、
 * "越境する知 / 栗原彬 [ほか] 編, 5" → "越境する知"） */
function seriesName(item) {
  const raw = (item.series && item.series[0] && item.series[0].title) || '';
  const cut = raw.split(/\s*[／/,，、]\s*/)[0].trim();
  return cut || raw;
}

/* ---------- アイテムの並び替えキー ---------- */
/* 単独本／シリーズ束を同じ規則で扱うためのアクセサ。シリーズは代表（最多所蔵）1冊を
 * 基準にする（出版年・所蔵館数・ncid いずれも代表に揃える）。 */
function itemOwner(it) { return it.type === 'series' ? it.maxOwner : it.book.ownerCount; }
function itemNcid(it) { return it.type === 'series' ? it.rep.ncid : it.book.ncid; }
function itemYear(it) { return it.type === 'series' ? it.rep.year : it.book.year; }

/* 所蔵館数降順、同値は ncid 昇順（= build と同じ決定規則）。タイブレークにも使う。 */
function byHoldings(a, b) {
  const d = itemOwner(b) - itemOwner(a);
  if (d) return d;
  const na = itemNcid(a), nb = itemNcid(b);
  return na < nb ? -1 : na > nb ? 1 : 0;
}

/* 並べ替え。base（タブ固有の基準順）から sortMode に応じた配列を返す。
 *  - default          : 基準順そのまま（「すべて」＝混在順 / 他タブ＝所蔵館数順）
 *  - year-asc/year-desc: 出版年の昇順／降順。シリーズは代表（rep）の年で並べる。
 * 出版年が欠損（null）のものは昇順・降順とも末尾へ固定し、同年・両欠損は
 * byHoldings（所蔵館数降順→ncid 昇順）で安定化する。base は破壊しない（slice）。 */
function sortedItems(base, mode) {
  if (mode !== 'year-asc' && mode !== 'year-desc') return base;
  const dir = mode === 'year-asc' ? 1 : -1;
  return base.slice().sort((a, b) => {
    const ya = itemYear(a), yb = itemYear(b);
    const ua = ya == null, ub = yb == null;
    if (ua !== ub) return ua ? 1 : -1;       // 欠損年は常に末尾
    if (!ua && ya !== yb) return (ya - yb) * dir;
    return byHoldings(a, b);                  // 同年・両欠損のタイブレーク
  });
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

  // 並び替えキー（所蔵館数降順、同値は ncid 昇順 = build と同じ決定規則）は
  // モジュールレベルの byHoldings を共用する（並べ替え機能でも同じ規則を使うため）。

  // 棚は 3 種に分かれる:
  //  - 単著/共著（personal）= 土台。ブロックの残り冊を埋める主たる流れ。
  //  - 編集書（editorial）  = 第1寄与者が著以外（編・訳・校注・編著…）＋著者表記なし。
  //  - シリーズ的           = シリーズ束 or 多巻単独（巻数 >= 閾値）。
  // 各バケットを所蔵館数順に整列する。タブ別表示はこの 3 バケットをそのまま用い
  //（各タブ＝該当書籍のみ・所蔵館数順）、「すべて」はブロック配分（mixBlocks）で混ぜる。
  const solo = items.filter(it => !isSeriesLike(it));
  const personalBucket = solo.filter(it => !isEditorial(it)).sort(byHoldings);
  const editorialBucket = solo.filter(isEditorial).sort(byHoldings);
  const seriesBucket = items.filter(isSeriesLike).sort(byHoldings);
  return {
    all: mixBlocks(personalBucket, seriesBucket, editorialBucket, mulberry32(MIX_SEED)),
    personal: personalBucket,
    editorial: editorialBucket,
    series: seriesBucket,
    // シリーズタブ「まとめ解除」用。束は各巻へ分け、多巻単独はそのまま。所蔵館数順。
    seriesUngrouped: ungroupSeriesItems(seriesBucket),
  };
}

/* シリーズタブのまとめを解除したアイテム列を作る。
 *  - シリーズ束（type:'series'）= 各巻を単独アイテム（type:'single'）へ展開する。
 *    展開した巻は枠・冊数・紙束を付けない（描画側で ungroup フラグにより抑止）。
 *  - 多巻単独（type:'single' で volumes>=閾値）= 分割できないため単位はそのまま残す。
 * 全体を所蔵館数降順（同値は ncid 昇順）の byHoldings で並べる。base は破壊しない。 */
function ungroupSeriesItems(seriesItems) {
  const out = [];
  for (const it of seriesItems) {
    if (it.type === 'series') {
      for (const b of it.books) {
        out.push({ type: 'single', book: b, volumes: (b.isbn && b.isbn.length) || 0 });
      }
    } else {
      out.push(it); // 多巻単独はそのまま
    }
  }
  return out.sort(byHoldings);
}

/* 単独本の寄与者種別が「編集書」か。build が各レコードへ付与した contribKind を読む。
 * 'editorial' = 第1寄与者が著以外（編・訳・校注・編著・編集委員…）または著者表記なし。
 * シリーズ的アイテムは挟む側へ回るため、ここでは単独本のみを対象にする。 */
function isEditorial(it) {
  return it.type === 'single' && it.book.contribKind === 'editorial';
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

/* ブロック配分。4〜8冊（rng で決定）を 1 ブロックとし、編集書 1 冊・シリーズ的 1 冊を
 * ブロック内ランダム位置へ入れ、残りを単著/共著で埋める。単著/共著が尽きるまで
 * ブロックを繰り返す（この間、編集書とシリーズは 1:1 の同割合で消費）。尽きたら、
 * 残った編集書とシリーズを冊数比率で混ぜて末尾へ連結する。各バケットは所蔵館数順
 * に整列済みで、先頭から消費する。 */
function mixBlocks(personal, series, editorial, rng) {
  const span = MIX_GAP_MAX - MIX_GAP_MIN + 1;
  const out = [];
  let pi = 0, si = 0, ei = 0;
  while (pi < personal.length) {
    const size = MIX_GAP_MIN + Math.floor(rng() * span); // ブロック総冊数 4〜8
    const block = [];
    for (let k = 0; k < size - 2 && pi < personal.length; k++) block.push(personal[pi++]);
    // 編集書・シリーズを 1 冊ずつ、ブロック内のランダム位置へ挿入する。
    if (si < series.length) block.splice(Math.floor(rng() * (block.length + 1)), 0, series[si++]);
    if (ei < editorial.length) block.splice(Math.floor(rng() * (block.length + 1)), 0, editorial[ei++]);
    for (const it of block) out.push(it);
  }
  // 単著/共著が尽きた後の残り。編集書とシリーズを冊数比率に合わせて混ぜる。
  for (const it of interleaveByRatio(series.slice(si), editorial.slice(ei))) out.push(it);
  return out;
}

/* 2 つの整列済み配列を、それぞれの冊数比率に合わせて均等に交互へ混ぜる
 * （所蔵館数順は各配列内で保持）。一方が空ならもう一方をそのまま返す。 */
function interleaveByRatio(a, b) {
  const na = a.length, nb = b.length;
  const out = [];
  let i = 0, j = 0;
  while (i < na || j < nb) {
    // 進捗割合（(i+0.5)/na ⇔ (j+0.5)/nb）が小さい＝遅れている側を先に出す。
    if (j >= nb || (i < na && (i + 0.5) * nb <= (j + 0.5) * na)) out.push(a[i++]);
    else out.push(b[j++]);
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
  const bottomStyle = ` style="color:${yearTextColor(book.year)};border-top-color:${yc.soft}"`;
  return `
    <div class="cover cover--placeholder"${bgStyle}>
      <div class="ph__top">
        <div class="ph__title">${escapeHtml(book.title)}</div>
        <div class="ph__author">${escapeHtml(authorName(book))}</div>
      </div>
      <div class="ph__spacer"></div>
      <div class="ph__bottom"${bottomStyle}>${escapeHtml(label)}</div>
    </div>`;
}

/* 表紙下メタの「出版社（orシリーズ名）, 出版年」1行を組み立てる。
 * - pub: 出版社名 or シリーズ名（空なら出版年のみ）
 * - isSeries: true ならシリーズ名として出版年と同色・太字で表示（区切りカンマも同色）
 * - year: 出版年（null なら年を出さない）
 * pub が長い場合は ellipsis で省略し、", 出版年" は常に末尾へ残す（年は折りたたまない）。
 * 出版年 span には data-year を付与し、将来の「年ごとの色分け」を CSS で拡張できるようにする。 */
function subYearHtml(pub, isSeries, year) {
  const hasYear = year != null;
  if (!pub && !hasYear) return '';
  const yc = yearColors(year);
  // シリーズ名は出版年と同じ色（yc.main）で揃える。シリーズ名でない出版社名は通常色のまま。
  const pubCls = isSeries ? 'meta__pub meta__pub--series' : 'meta__pub';
  const pubStyle = isSeries ? ` style="color:${yc.main}"` : '';
  const pubSpan = pub ? `<span class="${pubCls}"${pubStyle}>${escapeHtml(pub)}</span>` : '';
  // 出版社（orシリーズ名）と出版年の両方があるときだけ区切り ", " を入れる。
  // シリーズ名の場合はカンマも出版年と同色に揃え、それ以外は通常の濃色（黒系）のまま。
  const sepStyle = isSeries ? ` style="color:${yc.main}"` : '';
  const sepSpan = (pub && hasYear) ? `<span class="meta__sep"${sepStyle}>, </span>` : '';
  // 出版年はプレースホルダーの出版社ラベルと同じ濃さ（yc.main）で太字表示。
  const yearSpan = hasYear
    ? `<span class="meta__year" data-year="${escapeHtml(String(year))}" style="color:${yc.main}">${escapeHtml(year + '年')}</span>`
    : '';
  return `<div class="meta__sub">${pubSpan}${sepSpan}${yearSpan}</div>`;
}

function itemHtml(item, idx, ungroup) {
  const isSeries = item.type === 'series';
  const book = isSeries ? item.rep : item.book;
  // 冊数: シリーズ束 = メンバー数、多巻単独 = 巻数（ISBN 数）
  const count = isSeries ? item.books.length : (item.volumes || 1);
  // まとめ解除中（ungroup）は枠・冊数・紙束を一切付けない（多巻単独も含め装飾を外す）。
  const seriesLike = !ungroup && isSeriesLike(item); // 枠・冊数表示の対象
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
        <div class="meta__author">${escapeHtml(authorRawName(book))}</div>
        ${subYearHtml(isSeries ? sName : publisherText(book), isSeries, book.year)}
      </div>
    </article>`;
}

// 棚を先頭から描き直す（タブ切替・並べ替え・まとめ解除・検索など、並びが変わる
// 操作は必ずこれを呼ぶ）。実描画は先頭 PAGE_SIZE 件のみで、残りはスクロールに
// 応じて appendNextPage が追加する。
function renderShelf() {
  renderedCount = 0;
  els.shelf.innerHTML = '';
  appendNextPage();
}

// shelfItems の renderedCount 以降から PAGE_SIZE 件を追加描画する。呼び出し後は
// renderedCount が更新される。既に全件描画済みなら何もしない。
function appendNextPage() {
  if (renderedCount >= shelfItems.length) return;
  const ungroup = activeTab === 'series' && seriesUngrouped;
  const end = Math.min(renderedCount + PAGE_SIZE, shelfItems.length);
  const html = shelfItems
    .slice(renderedCount, end)
    .map((it, i) => itemHtml(it, renderedCount + i, ungroup))
    .join('');
  els.shelf.insertAdjacentHTML('beforeend', html);
  renderedCount = end;
  els.shelf.setAttribute('aria-busy', 'false');
}

// 棚の中身を1個のメッセージ（読み込み中/該当なし/エラー）へ置き換える。shelfItems/
// renderedCount も空にリセットしないと、センチネル（棚の外にある監視要素）は
// 前回描画分の残り件数を「まだ読み込める」と見なしたままになり、IntersectionObserver
// がメッセージ表示直後に前回検索の残りカードを追記してしまう。
function showShelfMessage(html) {
  shelfItems = [];
  renderedCount = 0;
  els.shelf.setAttribute('aria-busy', 'false');
  els.shelf.innerHTML = `<p class="shelf__empty">${html}</p>`;
}

// shouldStop() が true を返すか全件描画済みになるまでページを追加し続ける
// 共通ループ。棚板の再配置（applyShelfLayout）は 1 ページごとに行うと、描画済み
// 件数に比例して棚全体の再走査コストがかさむ（ページ数が多いほど二乗的に重くなる）
// ため、ループが終わったあとに一度だけ行う。
function loadWhile(shouldStop) {
  let loaded = false;
  while (renderedCount < shelfItems.length && !shouldStop()) {
    appendNextPage();
    loaded = true;
  }
  if (loaded) applyShelfLayout(true);
}

// センチネル（棚の末尾に置いた監視用要素）が画面下端の近く（600px 手前）まで
// 来ていれば次ページを追加する。IntersectionObserver は「交差状態が変化した
// 瞬間」にしか発火しないため、タブ切替直後など「センチネルが交差したまま」の
// ケースを取りこぼす。実際の座標で判定するループにし、広い画面で一度に複数
// ページ分の空きができていても埋まるまで繰り返す。
const LOAD_MORE_MARGIN_PX = 600;
function maybeLoadMore() {
  loadWhile(() => els.sentinel.getBoundingClientRect().top > window.innerHeight + LOAD_MORE_MARGIN_PX);
}

// 指定の Y 座標までスクロールできるだけの十分な描画量を確保する（タブ復帰時、
// 保存済みスクロール位置が先頭 PAGE_SIZE 件の範囲を超えている場合に使う）。
function loadUntil(targetY) {
  loadWhile(() => document.documentElement.scrollHeight >= targetY);
}

/* タブの基準アイテム列を返す。シリーズタブでまとめ解除中のときだけ展開版を使う。 */
function baseItemsFor(tab) {
  if (tab === 'series' && seriesUngrouped) return tabItems.seriesUngrouped;
  return tabItems[tab];
}

/* ---------- タブ（表示する書籍の切り替え） ---------- */

/* タブを切り替える。離脱前に現在のスクロール位置を保存し、戻ってきたときに復元する
 * （例: すべて→単著/共著→すべて で元の位置に戻る）。初訪問のタブは先頭から表示。 */
function switchTab(tab) {
  if (tab === activeTab || !tabItems[tab]) return;
  scrollByTab[activeTab] = window.scrollY; // 離脱するタブの位置を保存
  activeTab = tab;

  els.tabs.querySelectorAll('.tab').forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });

  // まとめ解除スイッチはシリーズタブのときだけ見せる。
  if (els.seriesToggle) els.seriesToggle.hidden = tab !== 'series';

  hideCoverDetail();
  shelfItems = sortedItems(baseItemsFor(tab), sortMode); // 現在の並べ替えを引き継ぐ
  renderShelf();
  applyShelfLayout(true);

  // レイアウト確定後に保存位置（無ければ先頭）へ復元する。保存位置が先頭
  // PAGE_SIZE 件の範囲を超えている場合は、そこまで届くよう追加読み込みしておく。
  const y = scrollByTab[tab] || 0;
  loadUntil(y + window.innerHeight);
  requestAnimationFrame(() => window.scrollTo(0, y));
}

/* シリーズの「まとめ解除」を切り替える。シリーズ束を各巻へ分け（多巻単独はそのまま）、
 * 枠・冊数・紙束を外して所蔵館数順に並べ直す。並びが変わるためスクロール位置の保存は
 * 破棄して先頭へ戻す。シリーズタブ表示中にのみ呼ばれる。 */
function toggleSeriesUngroup(on) {
  if (on === seriesUngrouped) return;
  seriesUngrouped = on;
  delete scrollByTab['series'];

  hideCoverDetail();
  shelfItems = sortedItems(baseItemsFor('series'), sortMode);
  renderShelf();
  applyShelfLayout(true);
  window.scrollTo(0, 0);
  maybeLoadMore(); // 先頭に戻った直後、画面を埋めるだけの分は続けて読み込む
}

/* 並べ替えを切り替える。表示中タブの基準順へ新しい sortMode を適用し、棚を再描画する。
 * 並びが変わるとスクロール位置の意味が失われるため、各タブの保存位置を破棄して
 * 先頭へ戻す（タブ切替も以後は先頭から始まる）。 */
function changeSort(mode) {
  if (mode === sortMode) return;
  sortMode = mode;
  Object.keys(scrollByTab).forEach((k) => delete scrollByTab[k]);

  hideCoverDetail();
  shelfItems = sortedItems(baseItemsFor(activeTab), sortMode);
  renderShelf();
  applyShelfLayout(true);
  window.scrollTo(0, 0);
  maybeLoadMore(); // 先頭に戻った直後、画面を埋めるだけの分は続けて読み込む
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

// 縦方向に重なりがあれば同じ折り返し行とみなす（align-items の違いで
// offsetTop が数px ずれる要素同士でも、行が分かれていなければ重なりが残る。
// 折り返しで別行になった場合は重なりが無くなる）。
function verticallyOverlaps(a, b) {
  const ra = a.getBoundingClientRect();
  const rb = b.getBoundingClientRect();
  return ra.top < rb.bottom && rb.top < ra.bottom;
}

// 検索バーの PC/モバイル表示切り替え。
// タブ・並べ替え・検索窓（シリーズトグルを除く主要3要素）がビューポート幅に
// よらず1行に収まるかどうかを実測し、収まらないときだけ .searchbar__inner に
// .searchbar--compact を付与してモバイル型の2段組みへ切り替える。
// シリーズトグルは例外として判定対象に含めない（トグルだけが次行へ折り返すのは許容）。
function updateToolbarLayout() {
  const inner = els.searchbarInner;
  if (!inner || !els.tabs || !els.searchForm) return;
  // PC版の並びで実測するため、既にコンパクト表示なら一旦解除してから測る。
  inner.classList.remove('searchbar--compact');
  const fitsOneLine =
    (!els.sort || verticallyOverlaps(els.tabs, els.sort)) &&
    verticallyOverlaps(els.tabs, els.searchForm);
  inner.classList.toggle('searchbar--compact', !fitsOneLine);
}

// コンパクト表示のみ、並べ替えプルダウンを展開したときに先頭へ選択不可の見出し
// 「並べ替え」を表示する（閉じた状態の表示＝選択中の値には影響しない）。
// 通常表示では付与せず、ラベル表示の現状を維持する。
function syncSortHeadingOption() {
  const select = els.sortSelect;
  if (!select) return;
  const isCompact = !!(els.searchbarInner && els.searchbarInner.classList.contains('searchbar--compact'));
  const group = select.querySelector('optgroup[data-sort-heading]');
  if (isCompact && !group) {
    // option を一旦 select から切り離して移すと、再接続時に選択状態が崩れる
    // （末尾の option が選ばれてしまう）ブラウザ挙動があるため、選択中の値を
    // 退避しておき、移し替え後に明示的に復元する。
    const current = select.value;
    const og = document.createElement('optgroup');
    og.label = '並べ替え';
    og.dataset.sortHeading = 'true';
    while (select.firstChild) og.appendChild(select.firstChild);
    select.appendChild(og);
    select.value = current;
  } else if (!isCompact && group) {
    const current = select.value;
    while (group.firstChild) select.appendChild(group.firstChild);
    group.remove();
    select.value = current;
  }
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

  // タブ切り替え（クリック / Enter・Space はボタン要素が自動で click を発火）
  els.tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (btn) switchTab(btn.dataset.tab);
  });

  // 並べ替え（プルダウン）
  if (els.sortSelect) {
    els.sortSelect.addEventListener('change', (e) => changeSort(e.target.value));
  }

  // シリーズまとめ解除スイッチ（チェック = まとめる / 解除 = 各巻に分ける）
  if (els.groupToggle) {
    els.groupToggle.addEventListener('change', (e) => toggleSeriesUngroup(!e.target.checked));
  }

  // 検索窓: 入力語で API（server/app.py）を叩き、返ってきた books（books.json と
  // 同一スキーマ）で棚を作り直す。空送信は既定データ（data/books.json）へ戻す。
  els.searchForm.addEventListener('submit', (e) => {
    e.preventDefault();
    runSearch(els.searchInput ? els.searchInput.value.trim() : '');
  });

  // ウィンドウ幅変化で列数が変わったら棚板を敷き直す（リサイズ確定後にだけ実行）。
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      applyShelfLayout(false);
      updateToolbarLayout();
      syncSortHeadingOption();
      maybeLoadMore(); // 画面が広がり一度に多くの段が見える場合の追加読み込み
    }, 120);
  });

  // 無限スクロール: 画面下端のセンチネルが近づいたら次の PAGE_SIZE 件を追加する。
  // rootMargin で実際に見える少し手前（600px）から先読みし、スクロールが
  // 途切れないようにする。
  if (els.sentinel) {
    const io = new IntersectionObserver((entries) => {
      if (entries.some(en => en.isIntersecting)) maybeLoadMore();
    }, { rootMargin: `${LOAD_MORE_MARGIN_PX}px 0px` });
    io.observe(els.sentinel);
  }
}

/* ---------- 起動・データ反映 ---------- */

/* books 配列（build の books.json / 動的検索 API いずれも同一スキーマ）を棚へ反映する
 * 共通経路。初期ロードと検索の両方から呼ぶ。表示は「すべて」タブ・先頭から始める。
 * 並べ替えモード（sortMode）は現在の選択を引き継ぐ。 */
function setBooks(books) {
  tabItems = buildShelfItems(books);
  activeTab = 'all';
  seriesUngrouped = false;
  // タブの見た目を「すべて」に戻す。
  els.tabs.querySelectorAll('.tab').forEach((b) => {
    const on = b.dataset.tab === 'all';
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  if (els.seriesToggle) els.seriesToggle.hidden = true;
  Object.keys(scrollByTab).forEach((k) => delete scrollByTab[k]);
  hideCoverDetail();
  shelfItems = sortedItems(baseItemsFor(activeTab), sortMode);
  renderShelf();
  applyShelfLayout(true);
  // 先頭へ戻してから埋める。直前の検索/タブで深くスクロールした位置のまま
  // maybeLoadMore を呼ぶと、無駄に何ページも先読みしてしまうため。
  window.scrollTo(0, 0);
  maybeLoadMore();
}

/* 既定データ（静的 data/books.json）を読み込んで表示する。動的検索サーバが無くても
 * この経路だけで従来どおり本棚が見える（グレースフルデグレード）。 */
async function loadDefault() {
  els.shelf.setAttribute('aria-busy', 'true');
  try {
    const books = await fetch(DATA_URL)
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
    setBooks(books);
  } catch (err) {
    showShelfMessage(`データの読み込みに失敗しました（${escapeHtml(String(err.message || err))}）。<br>build を実行して <code>site/data/books.json</code> を生成してください。`);
  }
}

/* 動的検索。検索語で API を叩き、返った books で棚を作り直す。空語なら既定へ戻す。
 * 返却スキーマは books.json と同一なので setBooks() をそのまま流用できる。 */
async function runSearch(query) {
  if (!query) { await loadDefault(); window.scrollTo(0, 0); return; }
  shelfItems = [];
  renderedCount = 0;
  els.shelf.setAttribute('aria-busy', 'true');
  els.shelf.innerHTML = '<p class="shelf__loading">検索中…</p>';
  try {
    const url = `${SEARCH_URL}?q=${encodeURIComponent(query)}`;
    const books = await fetch(url)
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
    if (!Array.isArray(books) || books.length === 0) {
      showShelfMessage(`「${escapeHtml(query)}」に一致する書誌は見つかりませんでした。`);
      return;
    }
    setBooks(books); // 先頭へのスクロールも setBooks 側で行う
  } catch (err) {
    showShelfMessage(`検索に失敗しました（${escapeHtml(String(err.message || err))}）。<br>検索用サーバ（<code>server/app.py</code>）が起動しているか確認してください。`);
  }
}

async function init() {
  bindEvents();
  updateToolbarLayout();
  syncSortHeadingOption();
  await loadDefault();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
