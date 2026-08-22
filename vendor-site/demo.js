/* Живая демонстрация для страницы продажи.
 *
 * Намеренно ничего не спрашивает у сервера. Показывать нотариусу нужно
 * в переговорной, где интернет чужой, а сервис может быть занят обновлением;
 * демонстрация обязана работать всегда и одинаково. Поэтому диалог здесь
 * свой, но слово в слово повторяет рабочий виджет, а услуги и списки
 * документов взяты такие, какие нотариус узнает.
 *
 * Смысл сцены один: клиент делает шаг — и это немедленно видно в двух
 * других окнах. Ради этого telegram() и cabinet() вызываются из submit().
 */
(function () {
  var chat = document.getElementById('chat');
  var choices = document.getElementById('choices');
  var row = document.getElementById('row');
  var field = document.getElementById('field');
  var send = document.getElementById('send');
  var tg = document.getElementById('tg');
  var tgEmpty = document.getElementById('tg-empty');
  var cab = document.getElementById('cab');
  var cabEmpty = document.getElementById('cab-empty');
  var cabCount = document.getElementById('cab-count');
  if (!chat) return;

  // --- то, что нотариус узнает как своё ------------------------------------

  var SERVICES = [
    {
      title: 'Доверенность на автомобиль',
      mode: 'documents',
      lead: 'в день обращения',
      price: '1 800 ₽',
      words: ['довер', 'машин', 'авто', 'тс', 'птс'],
      docs: [
        { t: 'Паспорт' },
        { t: 'СТС или ПТС на автомобиль' },
        { t: 'Данные представителя', d: 'ФИО полностью, дата рождения, серия и номер паспорта' },
        { t: 'Прежняя доверенность', d: 'если переоформляете', opt: true }
      ]
    },
    {
      title: 'Согласие на выезд ребёнка за границу',
      mode: 'visit',
      lead: 'готово за 30 минут',
      price: '1 500 ₽',
      words: ['ребен', 'ребён', 'выезд', 'соглас', 'границ', 'дет'],
      docs: [
        { t: 'Паспорт родителя' },
        { t: 'Свидетельство о рождении ребёнка' },
        { t: 'Даты поездки и страны', d: 'можно списком, через запятую' },
        { t: 'Загранпаспорт ребёнка', opt: true }
      ]
    },
    {
      title: 'Договор купли-продажи квартиры',
      mode: 'visit',
      lead: '3–5 рабочих дней',
      price: 'от 8 000 ₽',
      words: ['куп', 'прода', 'квартир', 'недвиж', 'договор', 'сделк'],
      docs: [
        { t: 'Паспорта продавца и покупателя' },
        { t: 'Выписка из ЕГРН' },
        { t: 'Документ-основание', d: 'договор, свидетельство о праве, решение суда' },
        { t: 'Согласие супруга на сделку', opt: true }
      ]
    },
    {
      title: 'Вступление в наследство',
      mode: 'visit',
      lead: 'по записи',
      price: 'консультация бесплатно',
      words: ['наслед', 'завещ', 'смерт', 'умер'],
      docs: [
        { t: 'Паспорт' },
        { t: 'Свидетельство о смерти' },
        { t: 'Документы о родстве', d: 'свидетельство о рождении, о браке' },
        { t: 'Завещание', opt: true }
      ]
    }
  ];

  var MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

  function slots() {
    var out = [];
    var d = new Date();
    d.setDate(d.getDate() + 1);
    var hours = [10, 11, 14, 16];
    for (var day = 0; day < 2; day++) {
      for (var i = 0; i < hours.length; i++) {
        var when = new Date(d);
        when.setDate(d.getDate() + day);
        out.push({
          label: when.getDate() + ' ' + MONTHS[when.getMonth()] + ', ' +
                 hours[i] + ':' + (i % 2 ? '30' : '00'),
          short: when.getDate() + ' ' + MONTHS[when.getMonth()] + ', ' +
                 hours[i] + ':' + (i % 2 ? '30' : '00')
        });
      }
    }
    return out.slice(0, 6);
  }

  var state = {};
  var number = 1041;
  var timers = [];
  var playing = false;

  function later(fn, ms) {
    var id = setTimeout(fn, ms);
    timers.push(id);
    return id;
  }

  function stopAll() {
    for (var i = 0; i < timers.length; i++) clearTimeout(timers[i]);
    timers = [];
    playing = false;
  }

  // --- лента ---------------------------------------------------------------

  function scroll() { chat.scrollTop = chat.scrollHeight; }

  function bubble(cls, build) {
    var el = document.createElement('div');
    el.className = 'b ' + cls;
    build(el);
    chat.appendChild(el);
    scroll();
    return el;
  }

  function say(text) {
    return bubble('bot', function (el) { el.textContent = text; });
  }

  function you(text) {
    return bubble('me', function (el) { el.textContent = text; });
  }

  function typing() {
    return bubble('bot dots', function (el) {
      el.innerHTML = '<span></span><span></span><span></span>';
    });
  }

  /* Пауза перед ответом — не украшение: без неё все реплики появляются
     разом и читатель не понимает, кто кому отвечает. */
  function reply(text, next) {
    var dots = typing();
    later(function () {
      dots.remove();
      if (text) say(text);
      if (next) next();
    }, text ? 620 : 420);
  }

  function clearChoices() { choices.innerHTML = ''; }

  function choice(label, hint, onPick, cls) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip' + (cls ? ' ' + cls : '');
    var t = document.createElement('div');
    t.textContent = label;
    b.appendChild(t);
    if (hint) {
      var h = document.createElement('div');
      h.className = 'm';
      h.textContent = hint;
      b.appendChild(h);
    }
    b.addEventListener('click', function () {
      if (b.disabled) return;
      clearChoices();
      hideInput();
      onPick();
    });
    choices.appendChild(b);
    return b;
  }

  var onText = null;

  function ask(placeholder, suggestion, handler) {
    field.value = '';
    field.placeholder = placeholder;
    row.hidden = false;
    onText = handler;
    if (suggestion) {
      choice('↳ ' + suggestion, 'нажмите, чтобы не набирать', function () {
        handler(suggestion);
      }, 'ghost');
    }
    later(function () { try { field.focus({ preventScroll: true }); } catch (e) {} }, 60);
  }

  function hideInput() { row.hidden = true; onText = null; }

  function submitText() {
    var v = field.value.trim();
    if (!v || !onText) return;
    var fn = onText;
    clearChoices();
    hideInput();
    fn(v);
  }

  send.addEventListener('click', submitText);
  field.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); submitText(); }
  });

  // --- ход разговора -------------------------------------------------------

  function hint(svc) {
    return (svc.mode === 'visit' ? 'нужен личный визит' : 'документы можно прислать онлайн') +
           ' · ' + svc.lead;
  }

  function listAll() {
    clearChoices();
    SERVICES.forEach(function (svc) {
      choice(svc.title, hint(svc), function () { pick(svc); });
    });
  }

  function search(text) {
    var q = text.toLowerCase();
    var found = SERVICES.filter(function (svc) {
      return svc.words.some(function (w) { return q.indexOf(w) >= 0; });
    });
    if (!found.length) {
      reply('Не нашёл подходящей услуги по этим словам. Посмотрите полный список или напишите иначе.', function () {
        listAll();
        ask('Попробуйте другими словами', null, function (again) {
          you(again); search(again);
        });
      });
      return;
    }
    reply(found.length === 1 ? 'Похоже, вам нужно это:' : 'Вот что подходит:', function () {
      clearChoices();
      found.forEach(function (svc) {
        choice(svc.title, hint(svc), function () { pick(svc); });
      });
    });
  }

  function pick(svc) {
    state.service = svc;
    state.slot = null;
    you(svc.title);

    reply(null, function () {
      bubble('bot', function (el) {
        var title = document.createElement('div');
        title.className = 't';
        title.textContent = 'Что понадобится';
        el.appendChild(title);

        var f = document.createElement('div');
        f.className = 'f';
        f.textContent = ['срок: ' + svc.lead, svc.price,
          svc.mode === 'visit' ? 'нужен личный визит' : 'документы онлайн'].join(' · ');
        el.appendChild(f);

        var ul = document.createElement('ul');
        ul.className = 'docs';
        svc.docs.forEach(function (doc) {
          var li = document.createElement('li');
          if (doc.opt) li.className = 'optional';
          var line = document.createElement('div');
          line.textContent = doc.t;
          if (doc.opt) {
            var o = document.createElement('span');
            o.className = 'opt';
            o.textContent = 'если есть';
            line.appendChild(o);
          }
          li.appendChild(line);
          if (doc.d) {
            var d = document.createElement('div');
            d.className = 'd';
            d.textContent = doc.d;
            li.appendChild(d);
          }
          ul.appendChild(li);
        });
        el.appendChild(ul);
      });

      choice('Оформить заявку', null, askName, 'primary');
      choice('Выбрать другую услугу', null, function () {
        reply('Хорошо. Вот все услуги:', listAll);
      }, 'quiet');
    });
  }

  function askName() {
    reply('Как вас зовут? Напишите фамилию и имя.', function () {
      ask('Фамилия и имя', 'Смирнов Алексей', function (v) {
        state.name = v;
        you(v);
        askPhone();
      });
    });
  }

  function askPhone() {
    reply('Оставьте номер телефона для связи.', function () {
      ask('+7 900 000-00-00', '+7 903 555-12-40', function (v) {
        state.phone = v;
        you(v);
        askConsent();
      });
    });
  }

  function askConsent() {
    reply('Для оформления нужно согласие на обработку персональных данных. Они используются только для подготовки нотариального действия.', function () {
      choice('Согласен', null, function () {
        you('Согласен');
        afterConsent();
      }, 'primary');
      choice('Отказаться', null, function () {
        you('Отказаться');
        reply('Хорошо. Без согласия заявку принять нельзя, но вы всегда можете позвонить нотариусу напрямую.', function () {
          choice('Начать заново', null, start, 'quiet');
        });
      }, 'quiet');
    });
  }

  function afterConsent() {
    if (state.service.mode !== 'visit') { submit(); return; }
    reply('Выберите удобное время приёма.', function () {
      clearChoices();
      slots().forEach(function (s) {
        choice(s.label, null, function () {
          state.slot = s.short;
          you(s.label);
          submit();
        }, 'slot');
      });
    });
  }

  function submit() {
    clearChoices();
    hideInput();
    var dots = typing();
    later(function () {
      dots.remove();
      state.number = ++number;
      say('Заявка № ' + state.number + ' принята.');

      // Тот самый момент: заявка уходит в два других окна.
      later(function () { telegram(); }, 260);
      later(function () { cabinet(); }, 700);

      later(function () {
        if (state.slot) {
          reply('Ждём вас в выбранное время. Сотрудник свяжется для подтверждения.', tail);
        } else {
          reply(null, function () {
            bubble('bot', function (el) {
              var p = document.createElement('div');
              p.textContent = 'Документы можно прислать прямо сейчас:';
              el.appendChild(p);
              var a = document.createElement('span');
              a.className = 'lnk';
              a.textContent = 'Загрузить документы';
              el.appendChild(a);
              var n = document.createElement('div');
              n.className = 'f';
              n.style.marginTop = '6px';
              n.textContent = 'Ссылка действует 30 минут, догрузить забытое можно по ней же.';
              el.appendChild(n);
            });
            tail();
          });
        }
      }, 900);
    }, 900);
  }

  function tail() {
    later(function () {
      bubble('note', function (el) {
        el.textContent = 'Заявка у сотрудников — посмотрите два окна справа →';
      });
      choice('Пройти ещё раз', null, start, 'quiet');
    }, 500);
  }

  // --- телефон сотрудника --------------------------------------------------

  function telegram() {
    if (tgEmpty) tgEmpty.remove();
    var el = document.createElement('div');
    el.className = 'tg-msg';

    var lines = [
      'Новая заявка № ' + state.number,
      state.service.title,
      '',
      state.name + ' · ' + state.phone
    ];
    lines.push(state.slot ? 'Приём: ' + state.slot : 'Клиент присылает документы онлайн');

    var body = document.createElement('div');
    body.className = 'tg-text';
    body.textContent = lines.join('\n');
    el.appendChild(body);

    var time = document.createElement('div');
    time.className = 'tg-time';
    var now = new Date();
    time.textContent = now.getHours() + ':' + String(now.getMinutes()).padStart(2, '0');
    el.appendChild(time);

    tg.appendChild(el);
    tg.scrollTop = tg.scrollHeight;
    flash(el);
  }

  // --- кабинет -------------------------------------------------------------

  function cabinet() {
    if (cabEmpty) cabEmpty.remove();
    var el = document.createElement('div');
    el.className = 'cab-row';

    var head = document.createElement('div');
    head.className = 'cab-top';
    head.innerHTML = '<b>№ ' + state.number + '</b><span class="tag">новая</span>';
    el.appendChild(head);

    var t = document.createElement('div');
    t.className = 'cab-title';
    t.textContent = state.service.title;
    el.appendChild(t);

    var m = document.createElement('div');
    m.className = 'cab-meta';
    m.textContent = state.name + ' · ' + state.phone +
      (state.slot ? ' · приём ' + state.slot : ' · документы онлайн');
    el.appendChild(m);

    var take = document.createElement('button');
    take.type = 'button';
    take.className = 'cab-take';
    take.textContent = 'Взять в работу';
    take.addEventListener('click', function () {
      el.classList.add('taken');
      head.innerHTML = '<b>№ ' + state.number + '</b><span class="tag work">в работе</span>';
      take.remove();
      var who = document.createElement('div');
      who.className = 'cab-who';
      who.textContent = 'Ведёт Петрова А. — остальные сотрудники её уже не возьмут';
      el.appendChild(who);
      recount();
    });
    el.appendChild(take);

    cab.appendChild(el);
    flash(el);
    recount();
  }

  function recount() {
    var n = cab.querySelectorAll('.cab-row:not(.taken)').length;
    cabCount.textContent = n;
  }

  function flash(el) {
    el.classList.add('arrive');
    later(function () { el.classList.remove('arrive'); }, 1400);
  }

  // --- запуск --------------------------------------------------------------

  function start() {
    stopAll();
    chat.innerHTML = '';
    clearChoices();
    hideInput();
    state = {};
    reply('Здравствуйте. Подскажу, какие документы нужны, и приму заявку.', function () {
      reply('Напишите своими словами, что нужно — например «доверенность на машину». Или выберите из списка.', function () {
        listAll();
        ask('Что нужно оформить?', null, function (text) {
          you(text);
          search(text);
        });
        var first = choices.querySelector('.chip');
        if (first) {
          first.classList.add('pulse');
          later(function () { first.classList.remove('pulse'); }, 5200);
        }
      });
    });
  }

  function reset() {
    stopAll();
    tg.innerHTML = '';
    var e1 = document.createElement('div');
    e1.className = 'tg-empty';
    e1.innerHTML = 'Здесь появится заявка,<br>как только клиент её отправит';
    tg.appendChild(e1);
    tgEmpty = e1;

    cab.innerHTML = '';
    var e2 = document.createElement('div');
    e2.className = 'cab-empty';
    e2.textContent = 'Новых заявок нет';
    cab.appendChild(e2);
    cabEmpty = e2;
    cabCount.textContent = '0';
    start();
  }

  document.getElementById('replay').addEventListener('click', reset);

  /* Показ целиком — для тех, кто не хочет кликать сам. Нажимает те же кнопки,
     что и человек, поэтому расходиться с ручным проходом ему нечем.
     Каждый шаг ждёт появления своей кнопки, а не наступления секунды: у бота
     паузы на «печатает», расписание от них разъезжается и показ застревает
     на середине — при нотариусе это худшее, что может случиться. */
  var SCENARIO = [
    function () { return findChip('Согласие на выезд'); },
    function () { return findChip('Оформить заявку'); },
    function () { return findChip('Смирнов'); },
    function () { return findChip('+7 903'); },
    function () { return findChip('Согласен'); },
    function () { return choices.querySelector('.chip.slot'); },
    function () { return cab.querySelector('.cab-take'); }
  ];

  function findChip(match) {
    var chips = choices.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) {
      if (chips[i].textContent.indexOf(match) >= 0) return chips[i];
    }
    return null;
  }

  function play(step, waited) {
    if (!playing) return;
    if (step >= SCENARIO.length) { playing = false; return; }
    var target = SCENARIO[step]();
    if (target) {
      later(function () {
        if (!playing) return;
        target.click();
        play(step + 1, 0);
      }, 750);                       // пауза, чтобы глаз успел прочесть
      return;
    }
    if (waited > 9000) { playing = false; return; }  // что-то пошло не так — молча выходим
    later(function () { play(step, waited + 120); }, 120);
  }

  document.getElementById('auto').addEventListener('click', function () {
    if (playing) { stopAll(); return; }
    reset();
    playing = true;
    play(0, 0);
  });

  start();
})();
