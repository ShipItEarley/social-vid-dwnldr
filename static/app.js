const form = document.querySelector('#download-form');
const input = document.querySelector('#url');
const browser = document.querySelector('#browser');
const submit = document.querySelector('#submit');
const connection = document.querySelector('#connection');
const errorBox = document.querySelector('#form-error');
const jobsNode = document.querySelector('#jobs');
let token = '';
let signature = '';
let sending = false;

function showError(message) {
  errorBox.textContent = message || '';
  errorBox.hidden = !message;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Token': token},
    body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Try again.');
  return data;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function size(bytes) {
  return bytes >= 1024 * 1024 ? (bytes / (1024 * 1024)).toFixed(1) + ' MB' : Math.ceil(bytes / 1024) + ' KB';
}

function render(jobs) {
  const nextSignature = JSON.stringify(jobs);
  if (signature === nextSignature) return;
  signature = nextSignature;
  document.querySelector('#empty').hidden = jobs.length > 0;
  const total = jobs.reduce((sum, job) => sum + job.files.length, 0);
  document.querySelector('#count').textContent = `${total} video${total === 1 ? '' : 's'}`;
  const rows = jobs.map(job => {
    const row = node('article', 'job');
    row.append(node('div', 'job-icon', job.status === 'complete' ? '✓' : job.status === 'error' ? '!' : '↓'));
    const detail = node('div', 'job-detail');
    const shortcode = new URL(job.url).pathname.split('/').filter(Boolean).pop();
    const site = new URL(job.url).hostname.endsWith('tiktok.com') ? 'TikTok' : 'Instagram';
    detail.append(node('p', 'job-title', job.files.length === 1 ? job.files[0].name : `${site} · ${shortcode}`));
    const source = node('a', 'job-link', job.url.replace('https://www.', ''));
    source.href = job.url;
    source.target = '_blank';
    source.rel = 'noreferrer';
    detail.append(source);
    const active = ['queued', 'reading', 'downloading'].includes(job.status);
    const statuses = {queued: 'Queued', reading: `Reading ${site} link…`, downloading: `Downloading · ${Math.round(job.progress)}%`, complete: 'Saved to your Mac'};
    const status = node('p', 'job-status' + (job.status === 'error' ? ' error' : ''), job.error || statuses[job.status]);
    status.setAttribute('role', 'status');
    detail.append(status);
    if (active) {
      const progress = node('progress', 'progress');
      progress.max = 100;
      if (job.status === 'downloading' && job.progress > 0) progress.value = job.progress;
      progress.setAttribute('aria-label', 'Download progress');
      detail.append(progress);
    }
    if (job.files.length) {
      const list = node('div', 'file-list');
      job.files.forEach(file => {
        const link = node('a', '', job.files.length === 1 ? 'Save a copy' : file.name);
        link.href = file.url;
        link.download = file.name;
        link.append(node('span', 'file-size', size(file.size)));
        list.append(link);
      });
      detail.append(list);
    }
    row.append(detail);
    const actions = node('div', 'job-actions');
    if (job.files.length) {
      const reveal = node('button', 'text-button', 'Show in Finder ↗');
      reveal.type = 'button';
      reveal.addEventListener('click', () => post('/api/open-folder', {id: job.id}).catch(error => showError(error.message)));
      actions.append(reveal);
    }
    if (job.status === 'error') {
      const retry = node('button', 'text-button', 'Try again ↑');
      retry.type = 'button';
      retry.addEventListener('click', () => {
        input.value = job.url;
        browser.value = job.browser || '';
        browser.dispatchEvent(new Event('change'));
        input.focus();
        window.scrollTo({top: 0, behavior: 'smooth'});
        showError('');
      });
      actions.append(retry);
    }
    row.append(actions);
    return row;
  });
  jobsNode.replaceChildren(...rows);
}

async function refresh() {
  try {
    const response = await fetch('/api/state');
    if (!response.ok) throw new Error('App unavailable');
    const state = await response.json();
    token = state.token;
    connection.textContent = 'Running locally';
    connection.classList.add('connected');
    submit.disabled = sending;
    document.querySelector('#open-folder').disabled = false;
    const displayPath = state.destination.replace(/^\/Users\/[^/]+\//, '~/');
    document.querySelector('#destination').textContent = displayPath;
    render(state.jobs);
  } catch (error) {
    connection.textContent = 'App disconnected';
    connection.classList.remove('connected');
    submit.disabled = true;
    document.querySelector('#open-folder').disabled = true;
  } finally {
    setTimeout(refresh, 1200);
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (sending) return;
  sending = true;
  submit.disabled = true;
  showError('');
  try {
    await post('/api/downloads', {url: input.value, browser: browser.value});
    input.value = '';
    const response = await fetch('/api/state');
    render((await response.json()).jobs);
  } catch (error) {
    showError(error.message);
  } finally {
    sending = false;
    submit.disabled = !connection.classList.contains('connected');
  }
});
browser.addEventListener('change', () => {
  document.querySelector('#session-note').hidden = !browser.value;
});
document.querySelector('#open-folder').addEventListener('click', () => {
  post('/api/open-folder', {}).catch(error => showError(error.message));
});
refresh();
