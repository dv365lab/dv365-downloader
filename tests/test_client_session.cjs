const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const start = source.indexOf('  async function api(');
const end = source.indexOf('\n  function setConnection(', start);
assert.ok(start >= 0 && end > start, 'API request function must be present');
const apiSource = source.slice(start, end);

function client(fetch) {
  const state = { config: { csrf_token: 'old-token' } };
  const context = { state, fetch, applyConfig: (config) => { state.config = config; } };
  vm.runInNewContext(`${apiSource}\n globalThis.request = api;`, context);
  return { request: context.request, state };
}

function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

test('a stale session refreshes its token and retries the original action once', async () => {
  const tokens = [];
  let refreshes = 0;
  const { request, state } = client(async (url, options) => {
    if (url === '/api/config') {
      refreshes += 1;
      return response(200, { csrf_token: 'new-token' });
    }
    assert.equal(url, '/api/tasks');
    tokens.push(options.headers['X-App-Token']);
    return options.headers['X-App-Token'] === 'old-token'
      ? response(403, { error: '页面会话已失效，请刷新页面后重试。' })
      : response(201, { id: 'created' });
  });

  const task = await request('/api/tasks', 'POST', { url: 'http://example.com/', kind: 'pdf' });
  assert.equal(task.id, 'created');
  assert.equal(state.config.csrf_token, 'new-token');
  assert.deepEqual(tokens, ['old-token', 'new-token']);
  assert.equal(refreshes, 1);
});

test('other forbidden responses are not retried', async () => {
  let requests = 0;
  const { request } = client(async (url) => {
    assert.equal(url, '/api/tasks');
    requests += 1;
    return response(403, { error: '已拒绝来自其他网页的请求。' });
  });
  await assert.rejects(request('/api/tasks', 'POST', {}), /已拒绝来自其他网页的请求/);
  assert.equal(requests, 1);
});
