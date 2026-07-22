/* tests/test_search_normalize.mjs — 検索正規化の共有テストベクタ検証（JS 側）。
 *
 * core/search_normalize_vectors.json を読み、site/js/search.js の normalizeSearch が
 * 各ケースの期待値と一致することを検証する。**同じベクタを Python 側テスト
 * （tests/test_search_normalize.py）からも読む**ことで、Python と JS の正規化一致を
 * 担保する（docs/site-search.md 問題点と対処 #1）。
 *
 * 実行: node --test tests/test_search_normalize.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const require = createRequire(import.meta.url);

const SiteSearch = require(join(ROOT, 'site', 'js', 'search.js'));
const vectors = JSON.parse(
  readFileSync(join(ROOT, 'core', 'search_normalize_vectors.json'), 'utf-8'),
);

test('全ベクタで normalizeSearch が期待値と一致する', () => {
  for (const c of vectors.cases) {
    assert.equal(
      SiteSearch.normalizeSearch(c.input), c.expected,
      `${c.desc}: input=${JSON.stringify(c.input)}`,
    );
  }
});

test('正規化は冪等（正規化済みを再度かけても変わらない）', () => {
  for (const c of vectors.cases) {
    const once = SiteSearch.normalizeSearch(c.input);
    assert.equal(SiteSearch.normalizeSearch(once), once);
  }
});

test('1 文字語の判定（isQueryLongEnough）', () => {
  assert.equal(SiteSearch.isQueryLongEnough('あ'), false);
  assert.equal(SiteSearch.isQueryLongEnough('漱石'), true);
  assert.equal(SiteSearch.isQueryLongEnough('東京 大'), false); // 1 文字語を含む
  assert.equal(SiteSearch.isQueryLongEnough('東京 大学'), true);
  assert.equal(SiteSearch.isQueryLongEnough(''), false);
});
