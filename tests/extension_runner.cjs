const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function run(mode) {
  const elements = Object.fromEntries(['scope', 'status', 'start', 'stop', 'token', 'pause', 'skip', 'progress', 'results'].map(id => [id, {
    value: 'test-code', disabled: true, textContent: '', children: [],
    replaceChildren() {this.children = [];}, append(child) {this.children.push(child);}
  }]));
  elements.scope.value = ['wishlist', 'empty'].includes(mode) ? 'wishlist' : 'all';
  const first = {label: 'Deals', kind: 'deals', page: 1, url: 'https://www.kobo.com/list'};
  const next = {...first, page: 2, url: first.url + '?pageNumber=2'};
  const wish = {label: 'Wish', kind: 'wishlist', url: 'https://www.kobo.com/ebook/wish'};
  let url, attempts = 0;
  const saved = [];
  const context = {
    document: {querySelector: selector => elements[selector.slice(1)], createElement: () => ({})},
    chrome: {
      storage: {session: {get: async () => ({}), set: async () => {}}},
      tabs: {
        get: async () => ({status: 'complete'}),
        create: async options => {url = options.url; return {id: 1};},
        update: async (_, options) => {url = options.url;}
      },
      scripting: {executeScript: async () => [{result: {url, challenge: false, html: '<html>Books</html>'}}]}
    },
    AbortSignal, Date,
    setTimeout: callback => setTimeout(() => {
      if (!elements.skip.disabled) {
        if (mode === 'skip') elements.skip.onclick();
        else if (mode === 'stop') elements.stop.onclick();
        else elements.pause.onclick();
      }
      callback();
    }, 0),
    fetch: async (_, options) => {
      if (options.method === 'GET') return {ok: true, json: async () => ({jobs: mode === 'empty' ? [first] : [first, wish]})};
      const body = JSON.parse(options.body);
      attempts++;
      if (attempts === 1 && ['retry', 'skip', 'stop'].includes(mode)) return {ok: false, json: async () => ({error: 'Temporary failure'})};
      saved.push(body.url);
      return {ok: true, json: async () => ({message: 'Saved', signature: body.url, next_job: body.url === first.url ? next : null})};
    }
  };
  vm.runInNewContext(fs.readFileSync('extension/scan.js', 'utf8'), context);
  await elements.start.onclick();
  assert.deepEqual(saved, ['skip', 'wishlist'].includes(mode) ? [wish.url] : ['stop', 'empty'].includes(mode) ? [] : [first.url, next.url, wish.url]);
  assert.equal(elements.scope.disabled, false);
  if (mode === 'empty') {
    assert.equal(url, undefined);
    assert.match(elements.status.textContent, /wishlist is empty/);
  }
  assert.equal(elements.start.disabled, false);
  assert.equal(elements.pause.disabled, true);
  if (mode === 'retry') assert.equal(attempts, 4);
}

(async () => {
  for (const mode of ['normal', 'retry', 'skip', 'stop', 'wishlist', 'empty']) await run(mode);
  console.log('Extension runner: pagination, retry/resume, skip, stop, wishlist-only, and empty wishlist passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
