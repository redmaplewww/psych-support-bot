import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../../src/psych_support_bot/static/index.html', import.meta.url), 'utf8');

test('account deletion clears all product caches, drafts and secrets without clearing other applications', () => {
  function storage(entries) {
    const values = new Map(Object.entries(entries));
    return { values, get length() { return values.size; }, key: i => [...values.keys()][i], removeItem: key => values.delete(key) };
  }
  const localStorage = storage({ np_guest_secret: 'secret', np_auth_token: 'token', np_theme: 'dark',
    psb_assess_draft_user_phq9: 'private answers', another_app: 'keep' });
  const sessionStorage = storage({ np_voice_used: '1', psb_assess_draft_user_gad7: 'answers', unrelated: 'keep' });
  const start = html.indexOf('    function clearLocalProductData() {');
  const end = html.indexOf('    async function deleteMyAccount()', start);
  vm.runInNewContext(html.slice(start, end) + '\nclearLocalProductData();', { localStorage, sessionStorage });
  assert.deepEqual([...localStorage.values], [['another_app', 'keep']]);
  assert.deepEqual([...sessionStorage.values], [['unrelated', 'keep']]);
});

test('profile memory control exposes pause and confirmed erasure as separate actions', () => {
  assert.match(html, /id="profileMemoryToggle"[^>]+role="switch"/);
  assert.match(html, /\/v1\/me\/profile-memory'[\s\S]+method: 'PUT'/);
  assert.match(html, /requestConfirmToken\('clear_profile_memory'\)/);
  assert.match(html, /method: 'DELETE'/);
  assert.match(html, /关闭只暂停采集与使用，不会自动删除/);
});
