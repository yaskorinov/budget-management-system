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
  groupMode: 'fund',   // fund — общая касса, split — делим расходы
  payee: null,         // кому возвращаем долг
  mode: 'categories',
  period: 'month',
  editing: null,
  pick: null,
  yandexReady: false,
};

const SVG_NS = 'http://www.w3.org/2000/svg';

const $ = (id) => document.getElementById(id);

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
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

function icon(name, cls = 'ic') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
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
    return `${(rubles / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 0 })} тыс. ₽`;
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

// Сегмент-контрол: линза встаёт по активной кнопке. Ширины у пилюль разные,
// а панель с ними может быть спрятана (offsetWidth = 0) — тогда расстановку
// откладываем до момента, когда вкладку покажут.
function positionThumb(box) {
  const active = box.querySelector('.active');
  if (!active || !box.offsetWidth) return;
  box.style.setProperty('--seg-x', `${active.offsetLeft}px`);
  box.style.setProperty('--seg-w', `${active.offsetWidth}px`);
  if (box.dataset.ready) return;
  // Пружину включаем следующим кадром, иначе линза приедет из левого края.
  requestAnimationFrame(() => { box.dataset.ready = '1'; });
}

function syncSegments() {
  document.querySelectorAll('.segmented').forEach(positionThumb);
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
  const invite = params.get('invite');
  const failure = params.get('auth_error');
  if (failure) history.replaceState({}, '', location.pathname);

  try {
    let payload = null;
    if (magic) {
      payload = await api('/auth/magic', {
        method: 'POST',
        body: JSON.stringify({ token: magic }),
      });
      history.replaceState({}, '', location.pathname);
    } else if (invite) {
      payload = await joinByInvite(invite);
      if (!payload) return;
    } else if (tg && tg.initData) {
      payload = await api('/auth/telegram', {
        method: 'POST',
        body: JSON.stringify({ init_data: tg.initData }),
      });
    } else if (state.token) {
      payload = await api('/me');
    }

    if (!payload) {
      showAuth(failure || 'Откройте приложение через бота: отправьте /web и перейдите по ссылке.');
      offerYandex();
      return;
    }

    await enter(payload);
  } catch (error) {
    if (error.message !== 'unauthorized') {
      showAuth(`Не удалось войти: ${error.message}`);
      offerYandex();
    }
  }
}

// Общий хвост всех способов входа: запомнить сессию и показать приложение.
async function enter(payload) {
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
}

// Приглашение: вошедший просто вступает, новому сначала показываем, куда зовут.
async function joinByInvite(token) {
  const info = await api(`/auth/invite/${encodeURIComponent(token)}`).catch(() => null);
  if (!info) {
    showAuth('Приглашение недействительно или уже использовано');
    offerYandex();
    return null;
  }

  if (state.token) {
    const payload = await api('/auth/invite', {
      method: 'POST',
      body: JSON.stringify({ token }),
    });
    history.replaceState({}, '', location.pathname);
    return payload;
  }

  showAuth(`Вас зовут в бюджет «${info.group_title}»`);
  $('auth-hint').textContent = info.inviter
    ? `Кто пригласил: ${info.inviter}. Войдите — и увидите общие расходы.`
    : 'Войдите — и увидите общие расходы.';
  $('invite-form').hidden = false;
  offerYandex(token);

  $('invite-go').onclick = async () => {
    const name = $('invite-name').value.trim();
    if (!name) return toast('Напишите, как вас зовут');
    try {
      const payload = await api('/auth/invite', {
        method: 'POST',
        body: JSON.stringify({ token, name }),
      });
      history.replaceState({}, '', location.pathname);
      await enter(payload);
    } catch (error) {
      toast(error.message);
    }
  };
  return null;
}

// Кнопка появляется, только если вход через Яндекс настроен на сервере.
async function offerYandex(invite) {
  try {
    const path = invite
      ? `/auth/yandex/url?invite=${encodeURIComponent(invite)}`
      : '/auth/yandex/url';
    const { url } = await api(path);
    const button = $('yandex-login');
    button.hidden = false;
    button.onclick = () => { location.href = url; };
  } catch (_) { /* не настроен — кнопки просто нет */ }
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
  renderAccount();
  probeYandex();
  await refresh();
}

function updateGroupName() {
  const group = state.groups.find((item) => item.id === state.groupId);
  $('group-name').textContent = group ? group.title : 'Бюджет';
  applyMode(group ? group.mode : 'fund');
}

// Режим меняет не только подписи: в кассе есть остаток и взносы, в дележе —
// личный баланс и возвраты долга конкретному человеку.
function applyMode(mode) {
  state.groupMode = mode === 'split' ? 'split' : 'fund';
  const split = state.groupMode === 'split';

  const second = $('kind-second');
  second.dataset.kind = split ? 'transfer' : 'contribution';
  second.querySelector('span').textContent = split ? 'Возврат' : 'Взнос';

  $('fund-badge').hidden = split;
  $('fund-meter').hidden = split;
  $('fund-in-label').textContent = split ? 'Вы оплатили' : 'Внесено';
  $('fund-out-label').textContent = split ? 'Ваша доля' : 'Потрачено';
  $('debts-block').hidden = !split;

  if (state.kind === (split ? 'contribution' : 'transfer')) {
    state.kind = split ? 'transfer' : 'contribution';
  }
  updateKindFields();
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
  renderPayees();

  renderFund(summary);
  renderBalances(summary);
  renderDebts(summary);
  // Совет считает модель — не держим из-за него весь экран.
  loadInsight().catch(() => {});
  renderParticipants();
  await Promise.all([loadOperations(), loadStats()]);
}

// --------------------------------------------------------------------------- //
//  Главная
// --------------------------------------------------------------------------- //

function renderFund(summary) {
  applyMode(summary.mode);

  if (summary.mode === 'split') {
    const me = summary.members.find((item) => item.user_id === (state.user || {}).id);
    const balance = me ? me.balance : 0;
    $('fund-label').textContent =
      balance > 0 ? 'Вам должны' : balance < 0 ? 'Вы должны' : 'Все рассчитались';
    $('fund').textContent = money(Math.abs(balance));
    $('fund-in').textContent = money(me ? me.contributed : 0, { short: true });
    $('fund-out').textContent = money(me ? me.spent : 0, { short: true });
    return;
  }

  $('fund-label').textContent = 'Остаток в фонде';
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

    const mine = item.user_id === (state.user || {}).id;
    let note;
    if (item.balance === 0) {
      note = 'в расчёте';
    } else if (state.groupMode === 'split') {
      note = item.balance > 0
        ? (mine ? 'вам должны' : 'ему должны')
        : (mine ? 'вы должны' : 'должен');
    } else {
      note = item.balance > 0 ? 'переплата' : 'задолженность';
    }

    card.append(top);
    card.appendChild(el('div', 'balance-note', note));
    card.appendChild(el('div', `pill-sum ${sign || 'flat'}`, signed(item.balance)));
    box.appendChild(card);
  });
}

// Тот же совет, что бот присылает вечером: за день он считается один раз,
// поэтому повторные заходы отвечают сразу.
async function loadInsight({ refresh = false } = {}) {
  const box = $('insight');
  box.hidden = false;
  box.classList.add('loading');
  $('insight-text').textContent = refresh
    ? 'Пересобираю совет…'
    : 'Смотрю, на что уходят деньги…';

  try {
    const data = await api(
      `/groups/${state.groupId}/insight${refresh ? '?refresh=true' : ''}`
    );
    if (!data.text) {
      box.hidden = true;   // модель выключена или тратить пока не на что
      return;
    }
    $('insight-text').textContent = data.text;
  } catch (_) {
    box.hidden = true;
  } finally {
    box.classList.remove('loading');
  }
}

$('insight-refresh').onclick = () => {
  haptic();
  loadInsight({ refresh: true }).catch(() => {});
};

function renderDebts(summary) {
  const box = $('debts');
  box.innerHTML = '';
  if (summary.mode !== 'split') return;

  if (!summary.debts.length) {
    box.appendChild(el('div', 'empty', 'Все рассчитались'));
    return;
  }

  summary.debts.forEach((debt) => {
    const row = el('div', 'debt');

    const who = el('div', 'debt-who');
    who.append(
      el('span', 'avatar', initial(debt.from_name)),
      el('span', 'debt-name', debt.from_name),
      icon('i-arrow', 'ic ic-14'),
      el('span', 'avatar', initial(debt.to_name)),
      el('span', 'debt-name', debt.to_name),
    );

    row.append(who, el('div', 'debt-sum', money(debt.amount)));

    if (debt.from_user_id === (state.user || {}).id) {
      const pay = el('button', 'debt-pay', 'Вернуть');
      pay.type = 'button';
      pay.onclick = () => startRepay(debt);
      row.appendChild(pay);
    }
    box.appendChild(row);
  });
}

// Кнопка «Вернуть» открывает форму уже заполненной: сумма и получатель
// известны из плана взаимозачёта.
function startRepay(debt) {
  resetForm();
  state.kind = 'transfer';
  state.payee = debt.to_user_id;
  $('amount').value = (debt.amount / 100).toString().replace('.', ',');
  $('sheet-title').textContent = 'Возврат долга';
  updateKindFields();
  renderPayees();
  openSheet();
  haptic();
}

function renderPayees() {
  const box = $('payees');
  box.innerHTML = '';

  const others = state.members.filter((member) => member.id !== (state.user || {}).id);
  if (state.payee && !others.some((member) => member.id === state.payee)) {
    state.payee = null;
  }
  if (!others.length) {
    box.appendChild(el('div', 'empty', 'В бюджете пока только вы'));
    return;
  }

  others.forEach((member) => {
    const chip = el('button', `chip${state.payee === member.id ? ' active' : ''}`);
    chip.type = 'button';
    chip.append(el('span', 'avatar', initial(member.name)),
      document.createTextNode(member.name));
    chip.onclick = () => {
      state.payee = member.id;
      renderPayees();
    };
    box.appendChild(chip);
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
  renderOperations($('recent'), state.operations.slice(0, 4), { compact: true });
}

// На главной операции — предпросмотр: сумма встаёт в строку с названием,
// а кнопки правки живут во вкладке «Операции». Иначе четыре карточки
// занимают весь экран.
function operationRow(operation, { compact = false } = {}) {
  const category = state.categories.find((item) => item.code === operation.category);
  const contribution = operation.kind === 'contribution';
  const transfer = operation.kind === 'transfer';

  const row = el('div', `op${contribution ? ' in' : ''}${compact ? ' compact' : ''}`);

  const badge = el('div', 'op-icon');
  const color = transfer ? '#0a84ff'
    : contribution ? '#30d158'
      : category ? category.color : '#8e8d88';
  badge.style.background = tint(color, 0.18);
  badge.style.color = color;
  badge.appendChild(icon(
    transfer ? 'i-arrow'
      : contribution ? 'i-wallet'
        : CAT_ICON[operation.category] || 'c-other',
  ));

  const main = el('div', 'op-main');
  main.appendChild(el('div', 'op-title', transfer
    ? `Возврат: ${operation.author} → ${operation.to_user || '—'}`
    : contribution ? 'Взнос в фонд'
      : operation.title || operation.category_title));

  const when = new Date(`${operation.occurred_at}Z`).toLocaleString('ru-RU',
    { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const people = !contribution && !transfer
    && operation.shares.length < state.members.length
    ? ` · делят: ${operation.shares.map((share) => share.name).join(', ')}`
    : '';
  main.appendChild(el('div', 'op-meta', `${operation.author} · ${when}${people}`));

  const sum = el('div', 'op-sum',
    contribution ? `+${money(operation.amount)}` : money(operation.amount));

  if (compact) {
    row.append(badge, main, sum);
    return row;
  }

  const foot = el('div', 'op-foot');
  foot.appendChild(sum);

  if (operation.can_edit) {
    const actions = el('div', 'op-actions');

    const edit = el('button', 'mini-btn edit');
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
    foot.appendChild(actions);
  }

  main.appendChild(foot);
  row.append(badge, main);
  return row;
}

function renderOperations(box, operations, options = {}) {
  box.innerHTML = '';
  if (!operations.length) {
    box.appendChild(el('div', 'empty', 'Пока нет операций'));
    return;
  }
  operations.forEach((operation) =>
    box.appendChild(operationRow(operation, options)));
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
    badge.style.background = tint(category.color, 0.18);
    badge.style.color = category.color;
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
  const transfer = state.kind === 'transfer';
  $('purchase-fields').classList.toggle('open', purchase);
  $('purchase-details').classList.toggle('open', purchase);
  $('payee-field').classList.toggle('open', transfer);
  $('submit').textContent = state.editing
    ? 'Сохранить изменения'
    : purchase ? 'Записать покупку'
      : transfer ? 'Записать возврат' : 'Внести в фонд';
  document.querySelectorAll('#kind-switch .seg-btn').forEach((button) =>
    button.classList.toggle('active', button.dataset.kind === state.kind));
  positionThumb($('kind-switch'));
}

function openSheet() {
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
  positionThumb($('kind-switch'));
  try {
    if (tg && tg.BackButton) {
      tg.BackButton.show();
      tg.BackButton.onClick(closeSheet);
    }
  } catch (_) { /* нет BackButton — закрываем крестиком */ }
}

function closeSheet() {
  const sheet = $('sheet');
  if (sheet.hidden || sheet.classList.contains('closing')) return;

  try {
    if (tg && tg.BackButton) {
      tg.BackButton.offClick(closeSheet);
      tg.BackButton.hide();
    }
  } catch (_) { /* см. выше */ }

  // Прячем не сразу: сначала шторка должна уехать вниз. Ждём по таймеру,
  // а не по animationend — при «уменьшить движение» анимации нет вовсе.
  sheet.classList.add('closing');
  setTimeout(() => {
    sheet.classList.remove('closing');
    sheet.hidden = true;
    document.body.style.overflow = '';
    resetForm();
  }, 220);
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
  state.participants = operation.kind === 'transfer'
    ? new Set(state.members.map((member) => member.id))
    : new Set(operation.shares.map((share) => share.user_id));
  state.payee = operation.to_user_id || null;

  $('amount').value = (operation.amount / 100).toString().replace('.', ',');
  $('title').value = operation.title || '';
  $('raw-text').value = '';
  $('add-status').textContent = '';
  $('sheet-title').textContent = 'Изменить операцию';
  $('cancel-edit').hidden = false;

  updateKindFields();
  renderCategories();
  renderParticipants();
  renderPayees();
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
  state.payee = null;
  state.participants = new Set(state.members.map((member) => member.id));
  renderParticipants();
  renderPayees();
  updateKindFields();
}

async function submitForm(event) {
  event.preventDefault();

  const amount = parseAmount($('amount').value);
  if (!amount) return toast('Укажите сумму');

  const purchase = state.kind === 'purchase';
  const transfer = state.kind === 'transfer';
  if (transfer && !state.payee) return toast('Выберите, кому вернули долг');

  const body = {
    kind: state.kind,
    amount,
    title: $('title').value.trim() || null,
    category: purchase ? state.category : null,
    participant_ids: purchase ? [...state.participants]
      : transfer ? [state.payee] : null,
    to_user_id: transfer ? state.payee : null,
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
      toast(purchase ? 'Покупка записана'
        : transfer ? 'Возврат записан' : 'Взнос записан');
    }
    haptic('medium');
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

  const center = document.querySelector('.donut-center');
  center.classList.remove('fade');
  void center.offsetWidth;
  center.classList.add('fade');

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
    arc.style.strokeDasharray = `0 ${circumference}`;
    arc.style.transitionDelay = `${index * 45}ms`;
    arc.setAttribute('transform',
      `rotate(${((offset + gap / 2 + (rounded ? RING.width / 2 : 0)) / circumference) * 360 - 90} 100 100)`);
    arc.dataset.index = String(index);
    arc.addEventListener('click', () => pickSlice(index));
    svg.appendChild(arc);

    // Сегмент вырастает от нуля: длину ставим после того, как браузер учёл
    // стартовое состояние, иначе переходу не от чего отталкиваться.
    void arc.getBoundingClientRect();
    arc.style.strokeDasharray = `${drawn} ${circumference - drawn}`;

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
    row.style.animationDelay = `${index * 45}ms`;
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
      el('div', 'legend-share',
        `${((slice.value / stats.total) * 100).toFixed(1).replace('.', ',')}% расходов`));
    box.appendChild(row);
  });
}

// --------------------------------------------------------------------------- //
//  Навигация
// --------------------------------------------------------------------------- //

function switchTab(name) {
  const bar = $('tabbar');
  const button = document.querySelector(`.tabbtn[data-tab="${name}"]`);
  const next = Number(button.dataset.index);
  const previous = Number(bar.dataset.active);

  document.querySelectorAll('.tabbtn').forEach((tab) =>
    tab.classList.toggle('active', tab.dataset.tab === name));
  document.querySelectorAll('.panel').forEach((panel) =>
    panel.classList.toggle('active', panel.id === `tab-${name}`));

  if (next !== previous) {
    // Капсула едет в сторону новой вкладки и растягивается по ходу движения.
    bar.dataset.dir = next > previous ? 'right' : 'left';
    bar.dataset.active = String(next);
    bar.classList.remove('bump');
    void bar.offsetWidth;  // без сброса потока анимация не проиграется повторно
    bar.classList.add('bump');
  }

  syncSegments();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function bindGroup(id, key, onChange) {
  document.querySelectorAll(`#${id} > *`).forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll(`#${id} > *`).forEach((other) =>
        other.classList.toggle('active', other === button));
      state[key] = button.dataset[key];
      positionThumb(button.parentElement);
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

// --------------------------------------------------------------------------- //
//  Аккаунт: приглашения и привязка
// --------------------------------------------------------------------------- //

function renderAccount() {
  const user = state.user || {};
  $('link-block').hidden = !user.is_guest;
  $('link-ya').hidden = !state.yandexReady || user.has_yandex;
  $('link-tg').hidden = user.has_telegram;
}

// Кнопку «Яндекс» показываем только когда вход через него настроен.
async function probeYandex() {
  try {
    await api('/auth/yandex/url');
    state.yandexReady = true;
  } catch (_) {
    state.yandexReady = false;
  }
  renderAccount();
}

async function reloadMe() {
  try {
    const payload = await api('/me');
    state.user = payload.user;
    state.groups = payload.groups;
    renderAccount();
  } catch (_) { /* сессия протухла — разберётся api() */ }
}

$('link-tg').onclick = async () => {
  try {
    const data = await api('/link/telegram', { method: 'POST' });
    if (!data.url) return toast(`Отправьте боту: /start link_${data.code}`);
    toast('Подтвердите привязку в Telegram');
    // Ждём возвращения из Telegram, чтобы обновить отметки на месте.
    window.addEventListener('focus', reloadMe, { once: true });
    if (tg && tg.openTelegramLink) tg.openTelegramLink(data.url);
    else window.open(data.url, '_blank');
  } catch (error) {
    toast(error.message);
  }
};

$('link-ya').onclick = async () => {
  try {
    const { url } = await api('/link/yandex', { method: 'POST' });
    location.href = url;
  } catch (error) {
    toast(error.message);
  }
};

$('invite-btn').onclick = async () => {
  try {
    const data = await api(`/groups/${state.groupId}/invite`, { method: 'POST' });
    $('invite-url').value = data.url;
    $('invite-out').hidden = false;
    haptic();
  } catch (error) {
    toast(error.message);
  }
};

$('invite-copy').onclick = async () => {
  const url = $('invite-url').value;
  if (!url) return;

  // Внутри Telegram делиться удобнее пересылкой, чем буфером обмена.
  if (tg && tg.openTelegramLink) {
    tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(url)}`);
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    toast('Ссылка скопирована — действует 7 дней');
  } catch (_) {
    $('invite-url').select();
    toast('Скопируйте ссылку из поля');
  }
};

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('sheet').hidden) closeSheet();
});

// Telegram знает про чёлку и полоску жеста больше, чем env(safe-area-*):
// в мини-аппе он отдаёт свои отступы, из них и считаем поля экрана.
function applyInsets() {
  if (!tg) return;
  const root = document.documentElement.style;
  const safe = tg.safeAreaInset || {};
  const content = tg.contentSafeAreaInset || {};
  const top = (safe.top || 0) + (content.top || 0);
  const bottom = (safe.bottom || 0) + (content.bottom || 0);
  if (top) root.setProperty('--safe-top', `${top}px`);
  if (bottom) root.setProperty('--safe-bottom', `${bottom}px`);

  // Вебвью Telegram бывает выше видимой части экрана — тогда закреплённый
  // док оказывается ниже сгиба, и до него приходится долистывать. Поднимаем
  // его на эту разницу; в обычном браузере она нулевая.
  if (!tg.isExpanded) {
    try { tg.expand(); } catch (_) { /* старый клиент */ }
  }
  const stable = tg.viewportStableHeight || 0;
  const gap = stable ? Math.max(0, Math.round(window.innerHeight - stable)) : 0;
  root.setProperty('--viewport-gap', `${gap}px`);
}

if (tg) {
  tg.ready();
  tg.expand();
  applyInsets();
  ['safeAreaChanged', 'contentSafeAreaChanged', 'viewportChanged'].forEach((event) => {
    try { tg.onEvent(event, applyInsets); } catch (_) { /* старый клиент */ }
  });
  // Интерфейс всегда тёмный, поэтому и шапку клиента красим под него.
  try {
    tg.setHeaderColor('#0c0c0f');
    tg.setBackgroundColor('#0c0c0f');
  } catch (_) { /* старый клиент цвет шапки не умеет */ }
}

window.addEventListener('resize', syncSegments);

updateKindFields();
syncSegments();
authenticate();
