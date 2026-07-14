'use strict';

/* shelf_blowser — スタートページ（入口）
 * 検索窓は素の GET フォームで shelf.html?q=<語> へ遷移する（JS 不要）。
 * このスクリプトは NDC 分類ナビ（類目 1 桁 → 綱目 2 桁 → 細目 3 桁の 3 階層・
 * 各階層 10 ボタン）を NDC マスタ data/ndc/index.json から描画する。
 *  - 1・2 桁のボタン = その分類を選んで 1 つ下の階層を表示する（掘り下げ）。
 *  - 3 桁のボタン    = 選択＝遷移（shelf.html?ndc=<3桁> へのリンク）。
 *  - 「上の階層へ戻る」= 1 つ上の階層へ戻る。
 *  - 「この分類の棚を見る」= 1・2 桁の段階で、それ以上絞り込まずに
 *    shelf.html?ndc=<記号> へ遷移する。
 * データ未整備・0 件の分類はボタンを無効表示にする（現データでは全分類に件数あり）。
 */

const NDC_INDEX_URL = 'data/ndc/index.json';
const SHELF_URL = 'shelf.html';

const els = {
  crumb: document.getElementById('ndc-crumb'),
  controls: document.getElementById('ndc-controls'),
  back: document.getElementById('ndc-back'),
  view: document.getElementById('ndc-view'),
  grid: document.getElementById('ndc-grid'),
};

let byCode = new Map(); // 分類記号 -> { code, label, count, records, hasData, fetchedAt }
let path = '';          // 選択済みの上位記号。'' = 類目選択中 / '9' = 綱目選択中 / '91' = 細目選択中

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

/* 現在の階層で表示する 10 個の分類記号（path の下位 0〜9）。 */
function codesForPath() {
  const out = [];
  for (let i = 0; i < 10; i++) out.push(path + String(i));
  return out;
}

/* 現在の path に応じてナビ全体（パンくず・操作ボタン・分類ボタン 10 個）を描き直す。 */
function render() {
  const depth = path.length; // 0 = 類目 / 1 = 綱目 / 2 = 細目 を選択中

  // パンくず（いまどの階層のどの分類を選んでいるか）
  if (depth === 0) {
    els.crumb.textContent = '類目（1 桁・10 区分）から選んでください。分類を選ぶと下の階層へ進みます。';
    els.controls.hidden = true;
  } else {
    const parts = [];
    for (let i = 1; i <= depth; i++) parts.push(codeWithLabel(path.slice(0, i)));
    const next = depth === 1 ? '綱目（2 桁）' : '細目（3 桁）';
    els.crumb.textContent = `選択中: ${parts.join(' › ')} — ${next}を選ぶか、この分類のまま棚を見られます。`;
    els.controls.hidden = false;
    els.view.href = shelfUrl(path);
    els.view.textContent = `この分類（${codeWithLabel(path)}）の棚を見る →`;
  }

  // 分類ボタン（各階層 10 個）。3 桁は選択＝遷移なのでリンク、1・2 桁は掘り下げボタン。
  const html = codesForPath().map((code) => {
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
  els.grid.innerHTML = html;
}

function bindEvents() {
  // 1・2 桁のボタン = 掘り下げ（3 桁はリンクなのでブラウザの遷移に任せる）
  els.grid.addEventListener('click', (e) => {
    const btn = e.target.closest('button.ndc-btn[data-code]');
    if (!btn || btn.disabled) return;
    const code = btn.dataset.code;
    if (code.length >= 3) return;
    path = code;
    render();
  });

  els.back.addEventListener('click', () => {
    if (!path) return;
    path = path.slice(0, -1);
    render();
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
    render();
  } catch (err) {
    els.grid.innerHTML =
      `<p class="ndc-nav__error">分類データ（data/ndc/index.json）を読み込めませんでした（${escapeHtml(String(err.message || err))}）。<br>キーワード検索、または <a href="shelf.html">既定の棚</a> をご利用ください。</p>`;
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
