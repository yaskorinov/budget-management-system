'use strict';

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

const state = {
  token: localStorage.getItem('budget_token') || '',
  user: null,
  groups: [],
  groupId: null,
  members: [],
  categories: [],
  kind: 'purchase',
  category: 'food',
  participants: new Set(),
  scope: 'all',
  mode: 'categories',
  period: 'month',
  editing: null,
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

function money(cents) {
  const value = (cents / 100).toLocaleString('ru-RU', {
    minimumFractionDigits: cents % 100 ? 2 : 0,
    maximumFractionDigits: 2,
  });
  return `${value} ₽`;
}

function signed(cents) {
  return (cents > 0 ? '+' : '') + money(cents);
}

function parseAmount(text) {
  const cleaned = String(text || '').replace(/\s/g, '').replace(',', '.');
  const value = Number.parseFloat(cleaned);
  return Number.isFinite(value) && value > 0 ? Math.round(value * 100) : null;
}

function toast(message) {
  const node = $('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 2600);
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    localStorage.removeItem('budget_token');
    state.token = '';
    showAuth('Сессия истекла. Запросите новую ссылку командой /web в боте.');
    throw new Error('unauthorized');
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Ошибка ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function showAuth(message) {
  $('auth').style.display = 'grid';
  $('app').hidden = true;
  $('auth-text').textContent = message;
}

// --------------------------------------------------------------------------- //
//  Вход
// --------------------------------------------------------------------------- //

async function authenticate() {
  const params = new URLSearchParams(location.search);
  const magic = params.get('login');

  try {
    let payload = null;
    if (magic) {
      payload = await api('/auth/magic', {
        method: 'POST',
        body: JSON.stringify({ token: magic }),
      });
      history.replaceState({}, '', location.pathname);
    } else if (tg && tg.initData) {
      payload = await api('/auth/telegram', {
        method: 'POST',
        body: JSON.stringify({ init_data: tg.initData }),
      });
    } else if (state.token) {
      payload = await api('/me');
    }

    if (!payload) {
      showAuth('Откройте приложение через бота: отправьте /web и перейдите по ссылке.');
      return;
    }

    state.token = payload.token;
    localStorage.setItem('budget_token', payload.token);
    state.user = payload.user;
    state.groups = payload.groups;
    state.groupId = payload.active_group_id || (payload.groups[0] && payload.groups[0].id);

    if (!state.groupId) {
      showAuth('У вас пока нет общего бюджета. Создайте его в боте: /newgroup Название');
      return;
    }

    $('auth').style.display = 'none';
    $('app').hidden = false;
    await bootstrap();
  } catch (error) {
    if (error.message !== 'unauthorized') {
      showAuth(`Не удалось войти: ${error.message}`);
    }
  }
}

// --------------------------------------------------------------------------- //
//  Загрузка и отрисовка
// --------------------------------------------------------------------------- //

async function bootstrap() {
  const select = $('group-select');
  select.innerHTML = '';
  state.groups.forEach((group) => {
    const option = el('option', null, group.title);
    option.value = group.id;
    select.appendChild(option);
  });
  select.value = String(state.groupId);
  select.hidden = state.groups.length < 2;

  state.categories = await api('/categories');
  renderCategories();
  await refresh();
}

async function refresh() {
  const [summary, members] = await Promise.all([
    api(`/groups/${state.groupId}/summary`),
    api(`/groups/${state.groupId}/members`),
  ]);

  state.members = members;
  if (!state.participants.size) {
    members.forEach((member) => state.participants.add(member.id));
  }

  $('fund').textContent = money(summary.fund_left);
  $('fund-sub').textContent =
    `Внесено ${money(summary.total_contributed)} · потрачено ${money(summary.total_spent)}`;

  const box = $('balances');
  box.innerHTML = '';
  summary.members.forEach((item) => {
    const chip = el('div', `balance ${item.balance > 0 ? 'pos' : item.balance < 0 ? 'neg' : ''}`);
    chip.appendChild(el('span', null, item.name));
    chip.appendChild(el('b', null, signed(item.balance)));
    box.appendChild(chip);
  });

  renderParticipants();
  await Promise.all([loadOperations(), loadStats()]);
}

function renderCategories() {
  const box = $('categories');
  box.innerHTML = '';
  state.categories.forEach((category) => {
    const chip = el('button', `chip${category.code === state.category ? ' active' : ''}`);
    chip.type = 'button';
    const dot = el('span', 'dot');
    dot.style.background = category.color;
    chip.append(dot, document.createTextNode(`${category.emoji} ${category.title}`));
    chip.onclick = () => {
      state.category = category.code;
      renderCategories();
    };
    box.appendChild(chip);
  });
}

function renderParticipants() {
  const box = $('participants');
  box.innerHTML = '';
  state.members.forEach((member) => {
    const active = state.participants.has(member.id);
    const chip = el('button', `chip${active ? ' active' : ''}`, `${active ? '✓ ' : ''}${member.name}`);
    chip.type = 'button';
    chip.onclick = () => {
      if (state.participants.has(member.id)) {
        if (state.participants.size === 1) return toast('Нужен хотя бы один участник');
        state.participants.delete(member.id);
      } else {
        state.participants.add(member.id);
      }
      renderParticipants();
    };
    box.appendChild(chip);
  });
}

function updateKindFields() {
  const purchase = state.kind === 'purchase';
  $('purchase-fields').hidden = !purchase;
  $('category-field').hidden = !purchase;
  $('participants-field').hidden = !purchase;
  $('title-field').hidden = !purchase;
  $('submit').textContent = purchase ? 'Записать покупку' : 'Внести в фонд';
}

// --------------------------------------------------------------------------- //
//  Операции
// --------------------------------------------------------------------------- //

async function loadOperations() {
  const operations = await api(
    `/groups/${state.groupId}/operations?scope=${state.scope}&limit=50`
  );
  const list = $('ops-list');
  list.innerHTML = '';

  if (!operations.length) {
    list.appendChild(el('div', 'empty', 'Пока нет операций'));
    return;
  }

  const colors = Object.fromEntries(state.categories.map((c) => [c.code, c.color]));

  operations.forEach((operation) => {
    const row = el('div', 'op');
    const dot = el('span', 'dot');
    dot.style.background =
      operation.kind === 'contribution' ? 'var(--good)' : colors[operation.category] || '#8a8985';

    const main = el('div', 'op-main');
    main.appendChild(
      el('div', 'op-title', operation.kind === 'contribution'
        ? `Взнос в фонд · ${operation.author}`
        : operation.title || operation.category_title)
    );
    const when = new Date(operation.occurred_at + 'Z').toLocaleString('ru-RU',
      { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    const parts = operation.shares.length
      ? ` · на ${operation.shares.length}: ${operation.shares.map((s) => s.name).join(', ')}`
      : '';
    main.appendChild(
      el('div', 'op-meta',
        `${operation.kind === 'contribution' ? '' : operation.category_title + ' · '}` +
        `${operation.author} · ${when}${parts}`)
    );

    const right = el('div');
    right.appendChild(el('div', 'op-sum', money(operation.amount)));
    if (operation.can_edit) {
      const actions = el('div', 'op-actions');
      const edit = el('button', 'mini-btn edit', '✏️');
      edit.onclick = () => startEdit(operation);
      const remove = el('button', 'mini-btn danger', '🗑');
      remove.onclick = () => removeOperation(operation);
      actions.append(edit, remove);
      right.appendChild(actions);
    }

    row.append(dot, main, right);
    list.appendChild(row);
  });
}

function startEdit(operation) {
  state.editing = operation.id;
  state.kind = operation.kind;
  state.category = operation.category || 'other';
  state.participants = new Set(operation.shares.map((share) => share.user_id));
  $('amount').value = (operation.amount / 100).toString().replace('.', ',');
  $('title').value = operation.title || '';
  $('raw-text').value = '';
  $('submit').textContent = 'Сохранить изменения';
  document.querySelectorAll('#kind-switch .seg').forEach((button) =>
    button.classList.toggle('active', button.dataset.kind === state.kind));
  updateKindFields();
  renderCategories();
  renderParticipants();
  switchTab('add');
  $('add-status').textContent = `Правим операцию #${operation.id}. Очистить — кнопка «Отмена» ниже.`;
  showCancelEdit(true);
}

async function removeOperation(operation) {
  if (!confirm(`Удалить операцию на ${money(operation.amount)}?`)) return;
  await api(`/operations/${operation.id}`, { method: 'DELETE' });
  toast('Операция удалена');
  await refresh();
}

function showCancelEdit(show) {
  let button = $('cancel-edit');
  if (!show) {
    if (button) button.remove();
    return;
  }
  if (button) return;
  button = el('button', 'ghost-btn', 'Отмена');
  button.id = 'cancel-edit';
  button.type = 'button';
  button.style.marginTop = '8px';
  button.onclick = resetForm;
  $('submit').after(button);
}

function resetForm() {
  state.editing = null;
  $('amount').value = '';
  $('title').value = '';
  $('raw-text').value = '';
  $('add-status').textContent = '';
  state.participants = new Set(state.members.map((member) => member.id));
  renderParticipants();
  updateKindFields();
  showCancelEdit(false);
}

async function submitForm() {
  const amount = parseAmount($('amount').value);
  if (!amount) return toast('Укажите сумму');

  const body = {
    kind: state.kind,
    amount,
    title: $('title').value.trim() || null,
    category: state.kind === 'purchase' ? state.category : null,
    participant_ids: state.kind === 'purchase' ? [...state.participants] : null,
  };

  $('submit').disabled = true;
  try {
    if (state.editing) {
      await api(`/operations/${state.editing}`, {
        method: 'PATCH',
        body: JSON.stringify({
          amount: body.amount,
          title: body.title,
          category: body.category,
          participant_ids: body.participant_ids,
        }),
      });
      toast('Изменения сохранены');
    } else {
      await api(`/groups/${state.groupId}/operations`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      toast(state.kind === 'purchase' ? 'Покупка записана' : 'Взнос записан');
    }
    resetForm();
    await refresh();
  } catch (error) {
    $('add-status').textContent = error.message;
  } finally {
    $('submit').disabled = false;
  }
}

async function parseWithLLM() {
  const text = $('raw-text').value.trim();
  if (!text) return toast('Напишите, что купили');

  $('parse-btn').disabled = true;
  $('add-status').textContent = 'Определяем категорию…';
  try {
    const parsed = await api('/categorize', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    if (parsed.amount) $('amount').value = (parsed.amount / 100).toString().replace('.', ',');
    $('title').value = parsed.title;
    state.category = parsed.category;
    renderCategories();
    $('add-status').textContent =
      `${parsed.category_title} — ${parsed.source === 'llm' ? 'определил ИИ' : 'по ключевым словам'}`;
  } catch (error) {
    $('add-status').textContent = error.message;
  } finally {
    $('parse-btn').disabled = false;
  }
}

// --------------------------------------------------------------------------- //
//  Диаграмма
// --------------------------------------------------------------------------- //

async function loadStats() {
  const stats = await api(
    `/groups/${state.groupId}/stats?mode=${state.mode}&period=${state.period}`
  );
  drawDonut(stats);

  const body = $('legend').querySelector('tbody');
  body.innerHTML = '';
  stats.slices.forEach((slice) => {
    const row = el('tr');
    const name = el('td');
    const dot = el('span', 'dot');
    dot.style.background = slice.color;
    name.append(dot, document.createTextNode(slice.label));
    row.append(name, el('td', null, money(slice.value)),
      el('td', null, `${((slice.value / stats.total) * 100).toFixed(1)}%`));
    body.appendChild(row);
  });

  $('chart-total').textContent = stats.total
    ? `${stats.period_title}: ${money(stats.total)}`
    : `${stats.period_title}: расходов нет`;
}

function drawDonut(stats) {
  const canvas = $('donut');
  const context = canvas.getContext('2d');
  const size = canvas.width;
  const center = size / 2;
  const outer = center - 12;
  const inner = outer * 0.62;
  const surface = getComputedStyle(document.body).getPropertyValue('--surface-2').trim();
  const text = getComputedStyle(document.body).getPropertyValue('--text').trim();
  const muted = getComputedStyle(document.body).getPropertyValue('--muted').trim();

  context.clearRect(0, 0, size, size);

  if (!stats.total) {
    context.strokeStyle = muted;
    context.lineWidth = outer - inner;
    context.beginPath();
    context.arc(center, center, (outer + inner) / 2, 0, Math.PI * 2);
    context.globalAlpha = 0.18;
    context.stroke();
    context.globalAlpha = 1;
    return;
  }

  let angle = -Math.PI / 2;
  stats.slices.forEach((slice) => {
    const sweep = (slice.value / stats.total) * Math.PI * 2;
    context.beginPath();
    context.arc(center, center, outer, angle, angle + sweep);
    context.arc(center, center, inner, angle + sweep, angle, true);
    context.closePath();
    context.fillStyle = slice.color;
    context.fill();
    // Зазор цветом подложки: сегменты не сливаются друг с другом.
    context.strokeStyle = surface || '#ffffff';
    context.lineWidth = 4;
    context.stroke();

    const share = slice.value / stats.total;
    if (share >= 0.05) {
      const middle = angle + sweep / 2;
      const radius = (outer + inner) / 2;
      // Палитра пастельная — процент пишем тёмным, белый на ней не читается
      context.fillStyle = '#1a1a19';
      context.font = 'bold 26px -apple-system, "Segoe UI", Roboto, sans-serif';
      context.textAlign = 'center';
      context.textBaseline = 'middle';
      context.fillText(
        `${Math.round(share * 100)}%`,
        center + Math.cos(middle) * radius,
        center + Math.sin(middle) * radius
      );
    }
    angle += sweep;
  });

  context.fillStyle = text;
  context.font = 'bold 40px -apple-system, "Segoe UI", Roboto, sans-serif';
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.fillText(money(stats.total), center, center - 10);
  context.fillStyle = muted;
  context.font = '24px -apple-system, "Segoe UI", Roboto, sans-serif';
  context.fillText('всего', center, center + 30);
}

// --------------------------------------------------------------------------- //
//  Навигация и события
// --------------------------------------------------------------------------- //

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((tab) =>
    tab.classList.toggle('active', tab.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach((panel) =>
    panel.classList.toggle('active', panel.id === `tab-${name}`));
}

function bindSegmented(id, key, onChange) {
  document.querySelectorAll(`#${id} .seg`).forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll(`#${id} .seg`).forEach((other) =>
        other.classList.toggle('active', other === button));
      state[key] = button.dataset[key];
      onChange();
    };
  });
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.onclick = () => switchTab(tab.dataset.tab);
});

bindSegmented('kind-switch', 'kind', updateKindFields);
bindSegmented('scope-switch', 'scope', () => loadOperations().catch(() => {}));
bindSegmented('mode-switch', 'mode', () => loadStats().catch(() => {}));
bindSegmented('period-switch', 'period', () => loadStats().catch(() => {}));

$('submit').onclick = submitForm;
$('parse-btn').onclick = parseWithLLM;
$('reload').onclick = () => refresh().then(() => toast('Обновлено')).catch(() => {});

$('raw-text').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    parseWithLLM();
  }
});

$('group-select').onchange = async (event) => {
  state.groupId = Number(event.target.value);
  state.participants = new Set();
  await api(`/me/active-group/${state.groupId}`, { method: 'POST' });
  await refresh();
};

if (tg) {
  tg.ready();
  tg.expand();
  // В мини-аппе тему задаёт Telegram, а не системная настройка браузера.
  if (tg.colorScheme) document.documentElement.dataset.theme = tg.colorScheme;
  tg.onEvent('themeChanged', () => {
    document.documentElement.dataset.theme = tg.colorScheme;
  });
}

updateKindFields();
authenticate();
