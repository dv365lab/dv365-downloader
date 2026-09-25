(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const activeStatuses = new Set(['queued', 'extracting', 'downloading', 'processing']);
  const qualityNames = { best: '最佳画质', '1080': '1080p', '720': '720p', audio: '仅音频' };
  const statusNames = {
    queued: '等待下载', extracting: '解析链接', downloading: '下载中',
    processing: '处理文件', completed: '已完成', failed: '下载失败', cancelled: '已取消',
  };
  const state = { config: null, tasks: [], filter: 'all', submitting: false, saving: false, connected: false };
  const cardCache = new Map();
  const pendingActions = new Set();
  const form = $('#download-form');
  const urlInput = $('#video-url');
  const startButton = $('#start-download');
  const taskList = $('#task-list');
  const settingsDialog = $('#settings-dialog');

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function setMessage(target, text, success = false) {
    target.textContent = text;
    target.hidden = !text;
    target.classList.toggle('is-success', success);
  }

  function toast(message, isError = false) {
    const region = $('#toast-region');
    const item = element('div', `toast${isError ? ' is-error' : ''}`, message);
    region.replaceChildren(item);
    window.setTimeout(() => item.remove(), isError ? 6500 : 4200);
  }

  async function api(path, method = 'GET', body) {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      if (!state.config?.csrf_token) throw new Error('应用尚未连接，请稍后重试。');
      headers['X-App-Token'] = state.config.csrf_token;
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let response;
    try {
      response = await fetch(path, {
        method, headers, credentials: 'same-origin', cache: 'no-store',
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch {
      throw new Error('无法连接本机服务，请确认应用仍在运行。');
    }
    let data;
    try { data = await response.json(); }
    catch { throw new Error(`服务返回了无法读取的响应（${response.status}）。`); }
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : `请求失败（${response.status}），请重试。`);
    return data;
  }

  function setConnection(connected) {
    state.connected = connected;
    const connection = $('#connection-status');
    connection.classList.toggle('is-offline', !connected);
    connection.lastElementChild.textContent = connected ? '实时同步' : '连接中断 · 自动重试';
    updateStartButton();
  }

  function updateStartButton() {
    startButton.disabled = !state.config || !state.connected || state.submitting;
    startButton.querySelector('span').textContent = state.submitting ? '正在添加…' : '开始下载';
    $('#save-settings').disabled = !state.config || !state.connected || state.saving;
    $('#save-settings').textContent = state.saving ? '保存中…' : '保存设置';
  }

  function applyConfig(config) {
    state.config = config;
    const path = $('#storage-path');
    path.textContent = config.download_dir || '未设置';
    path.title = `${config.download_dir || '未设置'} — 点击修改`;
    $('#engine-version').textContent = `yt-dlp ${config.yt_dlp_version || ''}`.trim();
    const ffmpeg = $('#ffmpeg-status');
    ffmpeg.textContent = config.ffmpeg ? 'FFmpeg 已就绪' : 'FFmpeg 未就绪，部分格式受限';
    ffmpeg.classList.toggle('is-warning', !config.ffmpeg);
    updateStartButton();
  }

  function formatBytes(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const unit = Math.min(Math.floor(Math.log(number) / Math.log(1024)), units.length - 1);
    return `${(number / (1024 ** unit)).toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
  }

  function formatEta(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '';
    const seconds = Math.max(0, Math.round(Number(value)));
    if (seconds < 60) return `约 ${seconds} 秒`;
    const minutes = Math.ceil(seconds / 60);
    if (minutes < 60) return `约 ${minutes} 分钟`;
    return `约 ${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
  }

  function formatTime(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.valueOf())) return '';
    return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }

  function parseVideoUrl(value) {
    try {
      const url = new URL(value);
      if (url.protocol === 'https:' || url.protocol === 'http:') return url;
    } catch { /* Invalid links are shown as plain text. */ }
    return null;
  }

  function appendMeta(parent, node) {
    if (parent.childElementCount) parent.append(element('span', 'meta-divider'));
    parent.append(node);
  }

  function actionButton(task, action, label, iconName, extraClass = '') {
    const button = element('button', `task-action ${extraClass}`.trim());
    button.type = 'button';
    button.dataset.action = action;
    button.dataset.taskId = task.id;
    button.disabled = pendingActions.has(task.id);
    button.setAttribute('aria-label', `${label}：${task.title || '此任务'}`);
    button.append(icon(iconName), document.createTextNode(label));
    return button;
  }

  function buildCard(task) {
    const knownStatus = Object.hasOwn(statusNames, task.status) ? task.status : 'queued';
    const card = element('article', `task-card status-${knownStatus}`);
    card.dataset.taskId = task.id;
    const media = element('div', 'task-media');
    media.setAttribute('aria-hidden', 'true');
    media.append(icon(task.quality === 'audio' ? 'music' : 'play'));
    card.append(media);
    const body = element('div', 'task-body');
    const heading = element('div', 'task-heading');
    const sourceUrl = parseVideoUrl(task.url);
    const title = task.title || sourceUrl?.hostname || '等待解析视频信息…';
    const titleNode = element('h3', 'task-title', title);
    titleNode.title = title;
    const badge = element('span', 'status-badge');
    badge.append(element('span', 'small-dot'), document.createTextNode(statusNames[knownStatus]));
    heading.append(titleNode, badge);
    body.append(heading);

    const meta = element('div', 'task-meta');
    if (sourceUrl) {
      const source = element('a', '', sourceUrl.hostname.replace(/^www\./, ''));
      source.href = sourceUrl.href;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      source.title = task.url;
      source.setAttribute('aria-label', `查看原视频：${title}`);
      appendMeta(meta, source);
    }
    appendMeta(meta, element('span', 'quality-tag', qualityNames[task.quality] || '视频'));
    const timestamp = formatTime(task.created_at);
    if (timestamp) appendMeta(meta, element('span', '', timestamp));
    body.append(meta);

    if (activeStatuses.has(task.status)) {
      const row = element('div', 'task-progress-row');
      const track = element('div', 'progress-track');
      track.setAttribute('role', 'progressbar');
      track.setAttribute('aria-label', `${title} 下载进度`);
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', '100');
      const fill = element('div', 'progress-fill');
      const numeric = task.progress !== null && task.progress !== undefined && Number.isFinite(Number(task.progress));
      const progress = numeric ? Math.max(0, Math.min(100, Number(task.progress))) : null;
      const isIndeterminate = progress === null && task.status !== 'queued';
      track.classList.toggle('is-indeterminate', isIndeterminate);
      if (progress !== null) {
        track.setAttribute('aria-valuenow', String(Math.round(progress)));
        fill.style.width = `${progress}%`;
      } else {
        track.setAttribute('aria-valuetext', statusNames[knownStatus]);
      }
      track.append(fill);
      row.append(track, element('span', 'task-progress-text', progress !== null ? `${Math.round(progress)}%` : task.status === 'queued' ? '排队中' : '处理中'));
      body.append(row);
    }

    if (task.status === 'failed' && task.error) body.append(element('p', 'task-error', task.error));

    const subline = element('div', 'task-subline');
    const detail = element('div', 'task-detail');
    if (task.status === 'downloading') {
      if (task.total_bytes > 0) detail.append(element('span', '', `${formatBytes(task.downloaded_bytes)} / ${formatBytes(task.total_bytes)}`));
      else if (task.downloaded_bytes > 0) detail.append(element('span', '', formatBytes(task.downloaded_bytes)));
      if (task.speed > 0) detail.append(element('span', '', `${formatBytes(task.speed)}/s`));
      const eta = formatEta(task.eta);
      if (eta) detail.append(element('span', '', `剩余 ${eta}`));
    } else if (task.status === 'completed') {
      if (task.total_bytes > 0 || task.downloaded_bytes > 0) detail.append(element('span', '', formatBytes(task.total_bytes || task.downloaded_bytes)));
      const savedText = element('span', '', '已保存到本地');
      savedText.title = task.output_dir || task.filename || '';
      detail.append(savedText);
    } else {
      const details = {
        queued: '任务已加入队列，稍后开始下载',
        extracting: '正在获取视频信息与可用格式…',
        processing: '正在合并或转换音视频文件…',
        failed: '可检查链接后重试',
        cancelled: '任务已停止，可随时重新下载',
      };
      detail.append(element('span', '', details[task.status] || ''));
    }
    const actions = element('div', 'task-actions');
    if (activeStatuses.has(task.status)) {
      actions.append(actionButton(task, 'cancel', '取消', 'x', 'task-action-danger'));
    }
    if (task.status === 'failed' || task.status === 'cancelled') {
      actions.append(actionButton(task, 'retry', '重新下载', 'retry', 'task-action-retry'));
    }
    if (task.status === 'completed') {
      actions.append(actionButton(task, 'open', '打开文件夹', 'folder'));
      if (task.filename) {
        const fileLink = element('a', 'task-action');
        fileLink.href = `/api/tasks/${encodeURIComponent(task.id)}/file`;
        fileLink.download = task.filename.split(/[\\/]/).pop();
        fileLink.title = task.filename;
        fileLink.append(icon('arrow-down'), document.createTextNode('获取文件'));
        actions.append(fileLink);
      }
    }
    subline.append(detail, actions);
    body.append(subline);
    card.append(body);
    return card;
  }

  function counts() {
    return {
      all: state.tasks.length,
      active: state.tasks.filter((task) => activeStatuses.has(task.status)).length,
      completed: state.tasks.filter((task) => task.status === 'completed').length,
      failed: state.tasks.filter((task) => task.status === 'failed' || task.status === 'cancelled').length,
    };
  }

  function matchesFilter(task) {
    if (state.filter === 'all') return true;
    if (state.filter === 'active') return activeStatuses.has(task.status);
    if (state.filter === 'failed') return task.status === 'failed' || task.status === 'cancelled';
    return task.status === state.filter;
  }

  function renderTasks() {
    const count = counts();
    for (const [name, value] of Object.entries(count)) {
      for (const prefix of ['count-', 'filter-', 'nav-']) {
        const node = document.getElementById(prefix === 'count-' ? `${prefix}${name}` : `${prefix}${name}-count`);
        if (node) node.textContent = String(value);
      }
    }
    $('#queue-total').textContent = `${count.all} 个任务`;
    document.querySelectorAll('[data-filter]').forEach((button) => {
      const selected = button.dataset.filter === state.filter;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    document.querySelectorAll('[data-nav-filter]').forEach((button) => {
      const selected = button.dataset.navFilter === state.filter || (state.filter === 'active' && button.dataset.navFilter === 'all');
      button.classList.toggle('is-active', selected);
      if (selected) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });

    const visible = state.tasks.filter(matchesFilter).sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
    const visibleIds = new Set(visible.map((task) => task.id));
    const focus = document.activeElement?.closest('[data-action]');
    const focusKey = focus && taskList.contains(focus) ? { id: focus.dataset.taskId, action: focus.dataset.action } : null;
    for (const [id, entry] of cardCache) {
      if (!visibleIds.has(id)) { entry.node.remove(); cardCache.delete(id); }
    }
    visible.forEach((task, index) => {
      const signature = `${JSON.stringify(task)}:${pendingActions.has(task.id)}`;
      let entry = cardCache.get(task.id);
      if (!entry || entry.signature !== signature) {
        const node = buildCard(task);
        if (entry) entry.node.replaceWith(node);
        entry = { node, signature };
        cardCache.set(task.id, entry);
      }
      if (taskList.children[index] !== entry.node) taskList.insertBefore(entry.node, taskList.children[index] || null);
    });
    if (focusKey && document.activeElement === document.body) {
      const card = cardCache.get(focusKey.id)?.node;
      const button = card && [...card.querySelectorAll('[data-action]')].find((node) => node.dataset.action === focusKey.action);
      if (button && !button.disabled) button.focus({ preventScroll: true });
    }

    const empty = $('#empty-state');
    empty.hidden = visible.length > 0;
    if (!empty.hidden) {
      const copy = {
        all: ['你的收藏，从一个链接开始', '在上方粘贴视频网址，下载进度会显示在这里。'],
        active: ['暂时没有正在进行的下载', '添加一个视频，开始你的下一次收藏。'],
        completed: ['好视频正在路上', '下载完成的视频会显示在这里。'],
        failed: ['一切顺利，暂无未成功的任务', '下载失败或取消的任务会显示在这里，支持重新下载。'],
      };
      $('#empty-title').textContent = copy[state.filter][0];
      $('#empty-description').textContent = copy[state.filter][1];
      $('#focus-url').firstChild.textContent = count.all === 0 ? '添加第一个视频' : '添加一个视频';
    }
  }

  function mergeTask(task) {
    if (!task || !task.id) return;
    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index < 0) state.tasks.unshift(task);
    else state.tasks[index] = task;
    renderTasks();
  }

  // A single timer schedules the next poll only after the current request settles.
  let revision = 0;
  let pollTimer;
  async function poll() {
    const requestRevision = revision;
    try {
      if (!state.config || !state.connected) applyConfig(await api('/api/config'));
      const response = await api('/api/tasks');
      if (!Array.isArray(response.tasks)) throw new Error('无法读取下载列表。');
      if (requestRevision === revision) {
        state.tasks = response.tasks;
        renderTasks();
      }
      setConnection(true);
    } catch {
      setConnection(false);
    } finally {
      pollTimer = window.setTimeout(poll, state.connected ? 1200 : 3000);
    }
  }

  function chooseFilter(filter) {
    if (!['all', 'active', 'completed', 'failed'].includes(filter)) return;
    state.filter = filter;
    renderTasks();
  }

  document.querySelectorAll('[data-filter], [data-nav-filter]').forEach((button) => {
    button.addEventListener('click', () => chooseFilter(button.dataset.filter || button.dataset.navFilter));
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.submitting || !state.config || !state.connected) return;
    const url = urlInput.value.trim();
    if (!parseVideoUrl(url)) {
      setMessage($('#form-message'), '请输入以 http:// 或 https:// 开头的视频网址。');
      urlInput.focus();
      return;
    }
    const quality = new FormData(form).get('quality') || 'best';
    state.submitting = true;
    revision += 1;
    updateStartButton();
    setMessage($('#form-message'), '');
    try {
      const task = await api('/api/tasks', 'POST', { url, quality });
      revision += 1;
      mergeTask(task);
      if (urlInput.value.trim() === url) urlInput.value = '';
      chooseFilter('all');
      toast('已加入下载队列，完成后会保存到本地。');
    } catch (error) {
      setMessage($('#form-message'), error.message);
    } finally {
      state.submitting = false;
      updateStartButton();
    }
  });

  urlInput.addEventListener('input', () => setMessage($('#form-message'), ''));
  $('#focus-url').addEventListener('click', () => {
    urlInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
    urlInput.focus({ preventScroll: true });
  });

  $('#paste-url').addEventListener('click', async () => {
    try {
      if (!navigator.clipboard?.readText) throw new Error('clipboard unavailable');
      const text = (await navigator.clipboard.readText()).trim();
      if (!text) {
        toast('剪贴板是空的，请先复制视频链接。');
        return;
      }
      urlInput.value = text;
      setMessage($('#form-message'), '');
      urlInput.focus();
    } catch {
      urlInput.focus();
      toast('请在输入框中按 Ctrl+V（Mac 使用 ⌘V）粘贴链接。');
    }
  });

  taskList.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button || button.disabled) return;
    const id = button.dataset.taskId;
    const action = button.dataset.action;
    if (!['cancel', 'retry', 'open'].includes(action) || pendingActions.has(id)) return;
    pendingActions.add(id);
    revision += 1;
    renderTasks();
    try {
      const response = await api(`/api/tasks/${encodeURIComponent(id)}/${action}`, 'POST', {});
      revision += 1;
      if (action === 'open') toast('已在电脑上打开保存目录。');
      else {
        mergeTask(response);
        toast(action === 'cancel' ? '已请求取消下载。' : '已重新加入下载队列。');
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      pendingActions.delete(id);
      renderTasks();
    }
  });

  $('#open-folder').addEventListener('click', async () => {
    const button = $('#open-folder');
    if (button.disabled) return;
    button.disabled = true;
    try {
      await api('/api/open-folder', 'POST', {});
      toast('已在电脑上打开下载目录。');
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  function openSettings() {
    $('#download-directory').value = state.config?.download_dir || '';
    setMessage($('#settings-message'), '');
    if (!settingsDialog.open) settingsDialog.showModal();
    $('#download-directory').focus();
  }
  function closeSettings() { settingsDialog.close(); }
  $('#open-settings').addEventListener('click', openSettings);
  $('#storage-path').addEventListener('click', openSettings);
  $('#close-settings').addEventListener('click', closeSettings);
  $('#cancel-settings').addEventListener('click', closeSettings);
  settingsDialog.addEventListener('click', (event) => {
    if (event.target !== settingsDialog) return;
    const rect = settingsDialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeSettings();
  });
  $('#settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.saving || !state.config || !state.connected) return;
    const directory = $('#download-directory').value.trim();
    if (!directory) {
      setMessage($('#settings-message'), '请输入本机保存目录。');
      return;
    }
    state.saving = true;
    updateStartButton();
    setMessage($('#settings-message'), '');
    try {
      const config = await api('/api/config', 'PATCH', { download_dir: directory });
      applyConfig(config);
      closeSettings();
      toast('保存目录已更新，新任务将使用新位置。');
    } catch (error) {
      setMessage($('#settings-message'), error.message);
    } finally {
      state.saving = false;
      updateStartButton();
    }
  });

  window.addEventListener('pagehide', () => window.clearTimeout(pollTimer));
  window.addEventListener('pageshow', (event) => { if (event.persisted) poll(); });
  renderTasks();
  poll();
})();
