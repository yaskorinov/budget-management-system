'use strict';

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

const state = {
  token: localStorage.getItem('budget_token') || '',
  user: null,
  groups: [],
  groupId: null,
  members: [],
  categories: [],
  operations: [],
  stats: null,
  kind: 'purchase',
  category: 'food',
  participants: new Set(),
  scope: 'all',
  mode: 'categories',
  period: 'month',
  editing: null,
  pick: null,
};

// Код категории → иконка из спрайта в index.html
const CAT_ICON = {
  food: 'c-food',
  household: 'c-household',
  utilities: 'c-utilities',
  subscriptions: 'c-subscriptions',
  goods: 'c-goods',
  other: 'c-other',
};

const SVG_NS = 'http://www.w3.org/2000/svg';

const $ = (id) => document.getElementById(id);

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

function icon(name, cls = 'ic') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', cls);
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `#${name}`);
  svg.appendChild(use);
  return svg;
}

// Пастельный цвет категории — подложкой под иконку, а не заливкой в упор.
function tint(hex, alpha) {
  const value = hex.replace('#', '');
  const int = Number.parseInt(value.length === 3
    ? value.split('').map((c) => c + c).join('')
    : value, 16);
  return `rgba(${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255}, ${alpha})`;
}

function money(cents, { short = false } = {}) {
  const rubles = cents / 100;
  if (short && Math.abs(rubles) >= 100000) {
    return `${(rubles / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 0 })} тыс ₽`;
  }
  const value = rubles.toLocaleString('ru-RU', {
    minimumFractionDigits: cents % 100 ? 2 : 0,
    maximumFractionDigits: 2,
  });
  return `${value} ₽`;
}

const signed = (cents) => (cents > 0 ? '+' : '') + money(cents);

const initial = (name) => (name || '?').trim().charAt(0).toUpperCase();

function parseAmount(text) {
  const cleaned = String(text || '').replace(/\s/g, '').replace(',', '.');
  const value = Number.parseFloat(cleaned);
  return Number.isFinite(value) && value > 0 ? Math.round(value * 100) : null;
}

function haptic(kind = 'light') {
  try {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(kind);
  } catch (_) { /* старый клиент — не беда */ }
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
  $('auth').style.display = 'flex';
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
//  Загрузка
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
  select.disabled = state.groups.length < 2;
  updateGroupName();

  state.categories = await api('/categories');
  renderCategories();
  await refresh();
}

function updateGroupName() {
  const group = state.groups.find((item) => item.id === state.groupId);
  $('group-name').textContent = group ? group.title : 'Бюджет';
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

  renderFund(summary);
  renderBalances(summary);
  renderParticipants();
  await Promise.all([loadOperations(), loadStats()]);
}

// --------------------------------------------------------------------------- //
//  Главная
// --------------------------------------------------------------------------- //

function renderFund(summary) {
  $('fund').textContent = money(summary.fund_left);
  $('fund-in').textContent = money(summary.total_contributed, { short: true });
  $('fund-out').textContent = money(summary.total_spent, { short: true });

  const share = summary.total_contributed
    ? Math.min(100, Math.round((summary.total_spent / summary.total_contributed) * 100))
    : 0;
  $('fund-share').textContent = `${share}%`;
  $('meter-fill').style.width = `${share}%`;
}

function renderBalances(summary) {
  const box = $('balances');
  box.innerHTML = '';

  summary.members.forEach((item) => {
    const sign = item.balance > 0 ? 'pos' : item.balance < 0 ? 'neg' : '';
    const card = el('div', `balance ${sign}`);

    const top = el('div', 'balance-top');
    top.append(el('div', 'avatar', initial(item.name)), el('div', 'balance-name', item.name));

    card.append(top, el('div', 'balance-sum', signed(item.balance)));
    card.appendChild(el('div', 'balance-note',
      item.balance > 0 ? 'переплатил' : item.balance < 0 ? 'нужно доложить' : 'в расчёте'));
    box.appendChild(card);
  });
}

// --------------------------------------------------------------------------- //
//  Операции
// --------------------------------------------------------------------------- //

async function loadOperations() {
  state.operations = await api(
    `/groups/${state.groupId}/operations?scope=${state.scope}&limit=50`
  );
  renderOperations($('ops-list'), state.operations);
  renderOperations($('recent'), state.operations.slice(0, 4));
}

function operationRow(operation) {
  const category = state.categories.find((item) => item.code === operation.category);
  const contribution = operation.kind === 'contribution';

  const row = el('div', `op${contribution ? ' in' : ''}`);

  const badge = el('div', 'op-icon');
  badge.style.background = contribution
    ? tint('#2f9c5c', 0.16)
    : tint(category ? category.color : '#8e8d88', 0.28);
  badge.appendChild(icon(contribution ? 'i-wallet' : CAT_ICON[operation.category] || 'c-other'));

  const main = el('div', 'op-main');
  main.appendChild(el('div', 'op-title', contribution
    ? 'Взнос в фонд'
    : operation.title || operation.category_title));

  const when = new Date(`${operation.occurred_at}Z`).toLocaleString('ru-RU',
    { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const people = !contribution && operation.shares.length < state.members.length
    ? ` · делят: ${operation.shares.map((share) => share.name).join(', ')}`
    : '';
  main.appendChild(el('div', 'op-meta', `${operation.author} · ${when}${people}`));

  const right = el('div', 'op-right');
  right.appendChild(el('div', 'op-sum',
    contribution ? `+${money(operation.amount)}` : money(operation.amount)));

  if (operation.can_edit) {
    const actions = el('div', 'op-actions');

    const edit = el('button', 'mini-btn');
    edit.type = 'button';
    edit.title = 'Изменить';
    edit.appendChild(icon('i-pencil'));
    edit.onclick = () => startEdit(operation);

    const remove = el('button', 'mini-btn danger');
    remove.type = 'button';
    remove.title = 'Удалить';
    remove.appendChild(icon('i-trash'));
    remove.onclick = () => removeOperation(operation);

    actions.append(edit, remove);
    right.appendChild(actions);
  }

  row.append(badge, main, right);
  return row;
}

function renderOperations(box, operations) {
  box.innerHTML = '';
  if (!operations.length) {
    box.appendChild(el('div', 'empty', 'Пока нет операций'));
    return;
  }
  operations.forEach((operation) => box.appendChild(operationRow(operation)));
}

async function removeOperation(operation) {
  if (!confirm(`Удалить операцию на ${money(operation.amount)}?`)) return;
  await api(`/operations/${operation.id}`, { method: 'DELETE' });
  haptic('medium');
  toast('Операция удалена');
  await refresh();
}

// --------------------------------------------------------------------------- //
//  Форма
// --------------------------------------------------------------------------- //

function renderCategories() {
  const box = $('categories');
  box.innerHTML = '';

  state.categories.forEach((category) => {
    const button = el('button', `cat${category.code === state.category ? ' active' : ''}`);
    button.type = 'button';

    const badge = el('div', 'cat-ic');
    badge.style.background = tint(category.color, 0.32);
    badge.appendChild(icon(CAT_ICON[category.code] || 'c-other'));

    button.append(badge, el('span', null, category.title));
    button.onclick = () => {
      state.category = category.code;
      renderCategories();
    };
    box.appendChild(button);
  });
}

function renderParticipants() {
  const box = $('participants');
  box.innerHTML = '';

  state.members.forEach((member) => {
    const active = state.participants.has(member.id);
    const chip = el('button', `chip${active ? ' active' : ''}`);
    chip.type = 'button';
    chip.append(el('span', 'avatar', initial(member.name)),
      document.createTextNode(member.name));
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
  $('submit').textContent = state.editing
    ? 'Сохранить изменения'
    : purchase ? 'Записать покупку' : 'Внести в фонд';
  document.querySelectorAll('#kind-switch .sw').forEach((button) =>
    button.classList.toggle('active', button.dataset.kind === state.kind));
}

function openSheet() {
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
  try {
    if (tg && tg.BackButton) {
      tg.BackButton.show();
      tg.BackButton.onClick(closeSheet);
    }
  } catch (_) { /* нет BackButton — закрываем крестиком */ }
}

function closeSheet() {
  $('sheet').hidden = true;
  document.body.style.overflow = '';
  try {
    if (tg && tg.BackButton) {
      tg.BackButton.offClick(closeSheet);
      tg.BackButton.hide();
    }
  } catch (_) { /* см. выше */ }
  if (state.editing) resetForm();
}

function newOperation() {
  resetForm();
  $('sheet-title').textContent = 'Новая операция';
  openSheet();
  haptic();
}

function startEdit(operation) {
  state.editing = operation.id;
  state.kind = operation.kind;
  state.category = operation.category || 'other';
  state.participants = new Set(operation.shares.map((share) => share.user_id));

  $('amount').value = (operation.amount / 100).toString().replace('.', ',');
  $('title').value = operation.title || '';
  $('raw-text').value = '';
  $('add-status').textContent = '';
  $('sheet-title').textContent = 'Правка операции';
  $('cancel-edit').hidden = false;

  updateKindFields();
  renderCategories();
  renderParticipants();
  openSheet();
}

function resetForm() {
  state.editing = null;
  $('amount').value = '';
  $('title').value = '';
  $('raw-text').value = '';
  $('add-status').textContent = '';
  $('sheet-title').textContent = 'Новая операция';
  $('cancel-edit').hidden = true;
  state.participants = new Set(state.members.map((member) => member.id));
  renderParticipants();
  updateKindFields();
}

async function submitForm(event) {
  event.preventDefault();

  const amount = parseAmount($('amount').value);
  if (!amount) return toast('Укажите сумму');

  const purchase = state.kind === 'purchase';
  const body = {
    kind: state.kind,
    amount,
    title: $('title').value.trim() || null,
    category: purchase ? state.category : null,
    participant_ids: purchase ? [...state.participants] : null,
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
      toast(purchase ? 'Покупка записана' : 'Взнос записан');
    }
    haptic('medium');
    resetForm();
    closeSheet();
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
//  Статистика
// --------------------------------------------------------------------------- //

const RING = { r: 74, width: 26, gap: 3.5 };

async function loadStats() {
  state.stats = await api(
    `/groups/${state.groupId}/stats?mode=${state.mode}&period=${state.period}`
  );
  state.pick = null;
  drawDonut();
  renderLegend();
  showCenter();

  $('chart-total').textContent = state.stats.total
    ? state.stats.period_title
    : `${state.stats.period_title}: расходов нет`;
}

function drawDonut() {
  const svg = $('donut');
  const stats = state.stats;
  const circumference = 2 * Math.PI * RING.r;

  svg.innerHTML = '';
  svg.classList.remove('has-pick');

  const ring = (cls) => {
    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('cx', '100');
    circle.setAttribute('cy', '100');
    circle.setAttribute('r', String(RING.r));
    circle.setAttribute('fill', 'none');
    circle.setAttribute('stroke-width', String(RING.width));
    if (cls) circle.setAttribute('class', cls);
    return circle;
  };

  if (!stats.total) {
    svg.appendChild(ring('track'));
    return;
  }

  // Одна категория на весь период — это кольцо целиком, без зазора и концов.
  const solo = stats.slices.length === 1;

  let offset = 0;
  stats.slices.forEach((slice, index) => {
    const length = (slice.value / stats.total) * circumference;
    // Скруглённые концы добавляют половину толщины с каждой стороны — на узком
    // секторе это превращает полоску в кружок, поэтому там концы прямые.
    const gap = solo ? 0 : RING.gap;
    const rounded = !solo && length - gap > RING.width * 1.2;
    const drawn = Math.max(length - gap - (rounded ? RING.width : 0), 0.6);

    const arc = ring('seg-arc');
    arc.setAttribute('stroke', slice.color);
    arc.setAttribute('stroke-linecap', rounded ? 'round' : 'butt');
    arc.setAttribute('stroke-dasharray', `${drawn} ${circumference - drawn}`);
    arc.setAttribute('transform',
      `rotate(${((offset + gap / 2 + (rounded ? RING.width / 2 : 0)) / circumference) * 360 - 90} 100 100)`);
    arc.dataset.index = String(index);
    arc.addEventListener('click', () => pickSlice(index));
    svg.appendChild(arc);

    offset += length;
  });
}

function pickSlice(index) {
  state.pick = state.pick === index ? null : index;
  const svg = $('donut');
  svg.classList.toggle('has-pick', state.pick !== null);
  svg.querySelectorAll('.seg-arc').forEach((arc) =>
    arc.classList.toggle('pick', Number(arc.dataset.index) === state.pick));
  document.querySelectorAll('#legend .legend-row').forEach((row) =>
    row.classList.toggle('pick', Number(row.dataset.index) === state.pick));
  showCenter();
  haptic();
}

function showCenter() {
  const stats = state.stats;
  if (!stats || !stats.total) {
    $('donut-value').textContent = '0 ₽';
    $('donut-label').textContent = 'нет расходов';
    return;
  }
  if (state.pick === null) {
    $('donut-value').textContent = money(stats.total, { short: true });
    $('donut-label').textContent = 'всего';
    return;
  }
  const slice = stats.slices[state.pick];
  $('donut-value').textContent = money(slice.value, { short: true });
  $('donut-label').textContent = slice.label;
}

function renderLegend() {
  const box = $('legend');
  const stats = state.stats;
  box.innerHTML = '';

  if (!stats.total) {
    box.appendChild(el('div', 'empty', 'За этот период расходов не было'));
    return;
  }

  const top = Math.max(...stats.slices.map((slice) => slice.value), 1);

  stats.slices.forEach((slice, index) => {
    const row = el('div', 'legend-row');
    row.dataset.index = String(index);
    row.onclick = () => pickSlice(index);

    const name = el('div', 'legend-name');
    const dot = el('span', 'dot');
    dot.style.background = slice.color;
    name.append(dot, el('span', null, slice.label));

    const bar = el('div', 'legend-bar');
    const fill = el('i');
    fill.style.width = `${Math.max((slice.value / top) * 100, 3)}%`;
    fill.style.background = slice.color;
    bar.appendChild(fill);

    row.append(name, el('div', 'legend-sum', money(slice.value)), bar,
      el('div', 'legend-share', `${((slice.value / stats.total) * 100).toFixed(1)}% от расходов`));
    box.appendChild(row);
  });
}

// --------------------------------------------------------------------------- //
//  Навигация
// --------------------------------------------------------------------------- //

function switchTab(name) {
  document.querySelectorAll('.tabbtn').forEach((tab) =>
    tab.classList.toggle('active', tab.dataset.tab === name));
  document.querySelectorAll('.panel').forEach((panel) =>
    panel.classList.toggle('active', panel.id === `tab-${name}`));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function bindGroup(id, key, onChange) {
  document.querySelectorAll(`#${id} > *`).forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll(`#${id} > *`).forEach((other) =>
        other.classList.toggle('active', other === button));
      state[key] = button.dataset[key];
      onChange();
    };
  });
}

document.querySelectorAll('.tabbtn').forEach((tab) => {
  tab.onclick = () => switchTab(tab.dataset.tab);
});
document.querySelectorAll('[data-goto]').forEach((button) => {
  button.onclick = () => switchTab(button.dataset.goto);
});
document.querySelectorAll('[data-close]').forEach((button) => {
  button.onclick = closeSheet;
});

bindGroup('kind-switch', 'kind', updateKindFields);
bindGroup('scope-switch', 'scope', () => loadOperations().catch(() => {}));
bindGroup('mode-switch', 'mode', () => loadStats().catch(() => {}));
bindGroup('period-switch', 'period', () => loadStats().catch(() => {}));

$('fab').onclick = newOperation;
$('op-form').onsubmit = submitForm;
$('cancel-edit').onclick = resetForm;
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
  updateGroupName();
  await api(`/me/active-group/${state.groupId}`, { method: 'POST' });
  await refresh();
};

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('sheet').hidden) closeSheet();
});

if (tg) {
  tg.ready();
  tg.expand();

  // В мини-аппе тему задаёт Telegram. В обычном браузере скрипт Telegram тоже
  // загружается и рапортует светлую тему — там мы к нему не прислушиваемся,
  // иначе тёмная тема системы никогда не включится.
  const applyTheme = () => {
    document.documentElement.dataset.theme = tg.colorScheme || 'light';
    try {
      const bg = getComputedStyle(document.body).getPropertyValue('--bg').trim();
      tg.setHeaderColor(bg);
      tg.setBackgroundColor(bg);
    } catch (_) { /* старый клиент цвет шапки не умеет */ }
  };

  if (tg.platform && tg.platform !== 'unknown') {
    applyTheme();
    tg.onEvent('themeChanged', applyTheme);
  }
}

updateKindFields();
authenticate();
