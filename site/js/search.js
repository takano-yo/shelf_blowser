'use strict';

/* shelf_blowser — 収録データ検索（NDC 類内検索）モジュール
 *
 * docs/site-search.md 実装手順4。サーバ未稼働（GitHub Pages のみ）でも、事前生成した
 * 検索コーパス（data/search/corpus-<類>.json）を使って NDC 類（1 桁）単位の
 * 収録データ全体を検索できるようにする。
 *
 * 流れ（docs/site-search.md データ設計）:
 *  ① 対象類のコーパスを初回検索時に fetch（以後セッション内で再利用）
 *  ② メモリ上で部分一致走査（正規化済み同士の includes()・空白区切り AND）
 *  ③ 上位 maxResults 件の参照から、取得上限 K（fetchLimit）までの分類ファイルを
 *     ランキング順に fetch して書誌本体を解決（K 超過は打ち切り「上位 m 件」）
 *  ④ 呼び出し側（app.js）が既存の setBooks() へ渡して棚を描画
 *
 * 正規化は core/search_normalize.py と同一規則。共有テストベクタ
 * （core/search_normalize_vectors.json）で Python と一致を検証する。
 *
 * window.SiteSearch として公開する（app.js から使う）。Node（テスト）からは
 * module.exports 経由で正規化関数を検証する（共有テストベクタ）。
 */

(function (root) {
  const SiteSearch = (function () {
  const MANIFEST_URL = 'data/search/manifest.json';
  const corpusUrl = (cls) => `data/search/corpus-${encodeURIComponent(cls)}.json`;
  const bodyUrl = (code) => `data/ndc/${encodeURIComponent(code)}.json`;
  const PING_URL = 'api/ping';

  // 既定値（manifest が読めないときのフォールバック。docs/site-search-poc.md で確定）。
  const DEFAULT_MAX_RESULTS = 1000;
  const DEFAULT_FETCH_LIMIT = 30;

  // --- 正規化（core/search_normalize.py と同一の 4 段。表示には使わない） ---
  const SMALL_KANA = {
    'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
    'っ': 'つ', 'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ',
    'ゎ': 'わ', 'ゕ': 'か', 'ゖ': 'け',
  };
  const SMALL_KANA_RE = /[ぁぃぅぇぉっゃゅょゎゕゖ]/g;

  /* カタカナ（U+30A1..U+30F6）→ ひらがな（-0x60）。長音符 ー(U+30FC) は対象外。 */
  function katakanaToHiragana(s) {
    let out = '';
    for (const ch of s) {
      const o = ch.codePointAt(0);
      out += (o >= 0x30A1 && o <= 0x30F6) ? String.fromCodePoint(o - 0x60) : ch;
    }
    return out;
  }

  /* 検索照合用の正規化。core.search_normalize.normalize_search と同一結果。 */
  function normalizeSearch(s) {
    if (!s) return '';
    s = s.normalize('NFKC');            // 1. 全角/半角統一（＋互換分解）
    s = s.toLowerCase();                // 2. 英字小文字化
    s = katakanaToHiragana(s);          // 3. カタカナ→ひらがな（ヴ→ゔ含む）
    s = s.replace(SMALL_KANA_RE, (ch) => SMALL_KANA[ch]); // 4. 小書き仮名→並字
    return s;
  }

  // --- セッションキャッシュ（同じ類の再検索・同じファイルの再解決を即時にする） ---
  const corpusCache = new Map();  // 類 -> {s:[],f:[],i:[]}
  const bodyCache = new Map();    // 分類記号 -> records[]
  let manifestPromise = null;

  function loadManifest() {
    if (!manifestPromise) {
      manifestPromise = fetchJson(MANIFEST_URL).catch((e) => {
        manifestPromise = null; throw e;
      });
    }
    return manifestPromise;
  }

  async function fetchJson(url, signal) {
    const r = await fetch(url, signal ? { signal } : undefined);
    if (!r.ok) throw new Error(`${url}: ${r.status}`);
    return r.json();
  }

  async function fetchCorpus(cls, signal) {
    if (corpusCache.has(cls)) return corpusCache.get(cls);
    const c = await fetchJson(corpusUrl(cls), signal);
    corpusCache.set(cls, c);
    return c;
  }

  async function fetchBody(code, signal) {
    if (bodyCache.has(code)) return bodyCache.get(code);
    const recs = await fetchJson(bodyUrl(code), signal);
    bodyCache.set(code, recs);
    return recs;
  }

  /* クエリを空白区切りの語配列にする（正規化前）。 */
  function terms(query) {
    return (query || '').split(/\s+/).filter(Boolean);
  }

  /* 各検索語が 2 文字以上か（1 文字語を含むなら false）。docs/site-search.md 決定4。 */
  function isQueryLongEnough(query) {
    const ts = terms(query);
    return ts.length > 0 && ts.every((t) => t.length >= 2);
  }

  /* サーバ稼働判定。/api/ping が {"ok":true} を返せば true。失敗・タイムアウトで false。 */
  async function ping(timeoutMs = 2500) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(PING_URL, { signal: ctrl.signal });
      if (!r.ok) return false;
      const j = await r.json();
      return j && j.ok === true;
    } catch (e) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  /* 収録データ検索の本体。cls=類（1 桁 '0'〜'9'）、query=検索語。
   * 戻り値: { books, totalHits, shown, truncated, files, usedFiles }
   *  - books    … ランキング順（所蔵館数降順）の書誌本体（ndc と同一スキーマ）
   *  - totalHits … 走査でのヒット総数（≦ コーパス件数）
   *  - shown    … 実際に解決・表示できた件数（= books.length。K 打ち切り時 < totalHits）
   *  - truncated … K（fetchLimit）打ち切りが発生したか
   * signal で前回検索を中断できる（AbortController）。 */
  async function search(cls, query, opts) {
    opts = opts || {};
    const signal = opts.signal;
    let maxResults = DEFAULT_MAX_RESULTS;
    let fetchLimit = DEFAULT_FETCH_LIMIT;
    try {
      const m = await loadManifest();
      if (m && Number.isFinite(m.maxResults)) maxResults = m.maxResults;
      if (m && Number.isFinite(m.fetchLimit)) fetchLimit = m.fetchLimit;
    } catch (e) { /* manifest 無しでも既定値で動く */ }

    const qterms = terms(query).map(normalizeSearch);
    const corpus = await fetchCorpus(cls, signal);
    const S = corpus.s;

    // ② 走査: 正規化済み同士の includes() AND。コーパスはランキング順なので、
    //    先頭から maxResults 件そろった時点で打ち切ってよい。
    const hits = [];
    for (let idx = 0; idx < S.length; idx++) {
      const s = S[idx];
      let ok = true;
      for (let t = 0; t < qterms.length; t++) {
        if (s.indexOf(qterms[t]) === -1) { ok = false; break; }
      }
      if (ok) {
        hits.push(idx);
        if (hits.length >= maxResults) break;
      }
    }

    // ③ 書誌解決: ランキング順にヒットの参照ファイルを集め、取得上限 K に達したら
    //    それ以降のヒットは打ち切る（＝上位 m 件の連続した prefix を表示）。
    const fileSet = new Set();
    let m = hits.length;
    for (let k = 0; k < hits.length; k++) {
      const f = corpus.f[hits[k]];
      if (!fileSet.has(f)) {
        if (fileSet.size >= fetchLimit) { m = k; break; }
        fileSet.add(f);
      }
    }
    const truncated = m < hits.length;

    // 必要な分類ファイルを並行取得（セッションキャッシュ済みは再利用）。
    const bodies = new Map();
    await Promise.all([...fileSet].map(async (f) => {
      bodies.set(f, await fetchBody(f, signal));
    }));

    // ランキング順に書誌本体を並べる。
    const books = [];
    for (let k = 0; k < m; k++) {
      const idx = hits[k];
      const recs = bodies.get(corpus.f[idx]);
      const rec = recs && recs[corpus.i[idx]];
      if (rec) books.push(rec);
    }
    return {
      books,
      totalHits: hits.length,
      shown: books.length,
      truncated,
      files: fileSet.size,
      usedFiles: fileSet.size,
      maxResults,
    };
  }

    return {
      normalizeSearch,
      isQueryLongEnough,
      terms,
      search,
      ping,
      loadManifest,
    };
  })();

  if (typeof module !== 'undefined' && module.exports) module.exports = SiteSearch;
  if (root) root.SiteSearch = SiteSearch;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
