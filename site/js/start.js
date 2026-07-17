'use strict';

/* shelf_blowser — スタートページ（入口）
 * 検索窓は素の GET フォームで shelf.html?q=<語> へ遷移する（JS 不要）。
 * このスクリプトは NDC 分類ナビ（類目 1 桁 → 綱目 2 桁 → 細目 3 桁の 3 階層・
 * 各階層 10 ボタンを 横5×縦2 で配置）を NDC マスタ data/ndc/index.json から描画する。
 *  - 1・2 桁のボタン = その分類を選んで 1 つ下の階層を表示する（掘り下げ）。
 *  - 3 桁のボタン    = 選択＝遷移（shelf.html?ndc=<3桁> へのリンク）。
 *  - パンくず（選択中の分類）のクリック = その階層（選択前）へ戻る。
 *  - 「この分類の棚を見る」= 1・2 桁の段階で、それ以上絞り込まずに
 *    shelf.html?ndc=<記号> へ遷移する。
 * 階層移動はボタン群（5×2）を横スライドで切り替える。掘り下げ＝現ボタンが左へ流れ
 * 右から新ボタンが出る／戻る＝現ボタンが右へ流れ左から新ボタンが出る。
 * データ未整備・0 件の分類はボタンを無効表示にする（現データでは全分類に件数あり）。
 */

const NDC_INDEX_URL = 'data/ndc/index.json';
const SHELF_URL = 'shelf.html';

const els = {
  crumbs: document.getElementById('ndc-crumbs'),
  controls: document.getElementById('ndc-controls'),
  view: document.getElementById('ndc-view'),
  viewport: document.getElementById('ndc-viewport'),
};

const prefersReducedMotion =
  window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let byCode = new Map(); // 分類記号 -> { code, label, count, records, hasData, fetchedAt }
let path = '';          // 選択済みの上位記号。'' = 類目選択中 / '9' = 綱目選択中 / '91' = 細目選択中
let activeGrid = null;  // 現在表示中の .ndc-nav__grid 要素
let animating = false;  // スライド中フラグ（多重操作防止）

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function shelfUrl(code) {
  return `${SHELF_URL}?ndc=${encodeURIComponent(code)}`;
}

function labelOf(code) {
  const e = byCode.get(code);
  return (e && e.label) || '';
}

/* 記号＋分類名の表示用文字列（分類名が未収録＝NDC9 の欠番なら記号のみ）。 */
function codeWithLabel(code) {
  const label = labelOf(code);
  return label ? `${code} ${label}` : code;
}

/* 指定 path の下位 0〜9 の 10 個の分類記号。 */
function codesForPath(forPath) {
  const out = [];
  for (let i = 0; i < 10; i++) out.push(forPath + String(i));
  return out;
}

/* 指定 path の分類ボタン 10 個ぶんの HTML。
 * 3 桁は選択＝遷移なのでリンク、1・2 桁は掘り下げボタン。 */
function gridHtml(forPath) {
  return codesForPath(forPath).map((code) => {
    const e = byCode.get(code);
    const label = e && e.label;
    const disabled = !e || !e.hasData || !e.count;
    const countText = e ? `${Number(e.count).toLocaleString('ja-JP')} 件` : 'データなし';
    const labelHtml = label
      ? escapeHtml(label)
      : '<span class="ndc-btn__nolabel">分類名未収録</span>';
    const inner = `
      <span class="ndc-btn__code">${escapeHtml(code)}</span>
      <span class="ndc-btn__label">${labelHtml}</span>
      <span class="ndc-btn__count">${escapeHtml(countText)}</span>`;
    if (code.length === 3 && !disabled) {
      return `<a class="ndc-btn" href="${escapeHtml(shelfUrl(code))}"
                 aria-label="NDC ${escapeHtml(codeWithLabel(code))} の棚を見る">${inner}</a>`;
    }
    return `<button type="button" class="ndc-btn" data-code="${escapeHtml(code)}"
                    ${disabled ? 'disabled' : ''}
                    aria-label="NDC ${escapeHtml(codeWithLabel(code))} ${code.length === 3 ? 'の棚を見る' : 'を選ぶ'}">${inner}</button>`;
  }).join('');
}

/* 5×2 のボタングリッド要素を生成する。 */
function makeGrid(forPath) {
  const g = document.createElement('div');
  g.className = 'ndc-nav__grid';
  g.innerHTML = gridHtml(forPath);
  return g;
}

/* 選択中の分類パンくずを描画する。先頭「すべて」＝類目選択前（path=''）。
 * 末尾（現在地）以外はボタンで、クリックするとその階層＝選択前へ戻る。 */
function renderCrumbs() {
  const depth = path.length; // 0 = 類目 / 1 = 綱目 / 2 = 細目 を選択中
  const items = [];
  const root = depth === 0
    ? '<span class="ndc-crumb ndc-crumb--current" aria-current="true">すべて</span>'
    : '<button type="button" class="ndc-crumb" data-path="">すべて</button>';
  items.push(root);
  for (let i = 1; i <= depth; i++) {
    const p = path.slice(0, i);
    const label = escapeHtml(codeWithLabel(p));
    items.push(i === depth
      ? `<span class="ndc-crumb ndc-crumb--current" aria-current="true">${label}</span>`
      : `<button type="button" class="ndc-crumb" data-path="${escapeHtml(p)}">${label}</button>`);
  }
  els.crumbs.innerHTML = items.join('<span class="ndc-crumb__sep" aria-hidden="true">›</span>');
}

/* パンくず・「この分類の棚を見る」ボタンを現在の path に合わせて更新する。
 * 領域は常設し（選択前でも確保）、選択時にボタンが上下へ動かないようにする。
 * 類目選択前（path=''）は棚を見る対象がないのでボタンを不可視（領域は維持）にする。 */
function updateChrome() {
  renderCrumbs();
  if (!path) {
    els.view.classList.add('is-inactive');
    els.view.removeAttribute('href');
    els.view.textContent = '';
  } else {
    els.view.classList.remove('is-inactive');
    els.view.href = shelfUrl(path);
    els.view.textContent = `この分類（${codeWithLabel(path)}）の棚を見る →`;
  }
}

/* 階層移動。direction: 'forward'（掘り下げ）/ 'back'（戻る）。
 * forward = 現ボタンが左へ流れ右から新ボタン、back = 逆向き。 */
function navigate(newPath, direction) {
  if (animating) return;
  path = newPath;
  updateChrome();

  const outgoing = activeGrid;
  // 初回描画・モーション無効時はスライドせず即差し替え。
  if (!outgoing || prefersReducedMotion) {
    const g = makeGrid(newPath);
    els.viewport.replaceChildren(g);
    activeGrid = g;
    return;
  }

  animating = true;
  const forward = direction !== 'back';
  const incoming = makeGrid(newPath);

  // アニメーション中は 2 枚のグリッドが重なるため、ビューポート高さを固定して崩れを防ぐ。
  els.viewport.style.height = `${outgoing.offsetHeight}px`;
  els.viewport.classList.add('is-animating');

  incoming.style.transform = `translateX(${forward ? '100%' : '-100%'})`;
  incoming.style.opacity = '0';
  els.viewport.appendChild(incoming);
  void incoming.offsetWidth; // 初期位置を確定させてからトランジションを開始（reflow）

  outgoing.style.transform = `translateX(${forward ? '-100%' : '100%'})`;
  outgoing.style.opacity = '0';
  incoming.style.transform = 'translateX(0)';
  incoming.style.opacity = '1';
  activeGrid = incoming;

  const done = () => {
    if (!animating) return;
    animating = false;
    outgoing.remove();
    els.viewport.classList.remove('is-animating');
    els.viewport.style.height = '';
    incoming.style.transform = '';
    incoming.style.opacity = '';
  };
  incoming.addEventListener('transitionend', done, { once: true });
  setTimeout(done, 480); // transitionend が来ない場合のフォールバック
}

function bindEvents() {
  // ビューポートに委譲。1・2 桁のボタン = 掘り下げ（3 桁はリンクなので既定遷移に任せる）。
  els.viewport.addEventListener('click', (e) => {
    const btn = e.target.closest('button.ndc-btn[data-code]');
    if (!btn || btn.disabled) return;
    const code = btn.dataset.code;
    if (code.length >= 3) return;
    navigate(code, 'forward');
  });

  // パンくずのクリック = その階層（選択前）へ戻る。末尾（現在地）は span なので対象外。
  els.crumbs.addEventListener('click', (e) => {
    const btn = e.target.closest('button.ndc-crumb[data-path]');
    if (!btn) return;
    const target = btn.dataset.path;
    if (target === path) return;
    navigate(target, target.length < path.length ? 'back' : 'forward');
  });
}

async function init() {
  bindEvents();
  try {
    const r = await fetch(NDC_INDEX_URL);
    if (!r.ok) throw new Error(r.status);
    const index = await r.json();
    for (const c of index.classes || []) byCode.set(c.code, c);
    if (!byCode.size) throw new Error('classes が空です');
    updateChrome();
    activeGrid = makeGrid(path);
    els.viewport.replaceChildren(activeGrid);
  } catch (err) {
    els.viewport.innerHTML =
      `<p class="ndc-nav__error">分類データ（data/ndc/index.json）を読み込めませんでした（${escapeHtml(String(err.message || err))}）。<br>キーワード検索、または <a href="shelf.html">既定の棚</a> をご利用ください。</p>`;
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
