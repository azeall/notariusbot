/* Живая демонстрация для страницы продажи.
 *
 * Намеренно ничего не спрашивает у сервера. Показывать нотариусу приходится
 * в переговорной, где интернет чужой, а сервис может быть занят обновлением;
 * демонстрация обязана работать всегда и одинаково. Диалог повторяет рабочий
 * виджет слово в слово, текст уведомления — формат render_new_request.
 *
 * Главное здесь — общая память: панель нотариуса и чат клиента читают один
 * и тот же список услуг. Нотариус правит цену в панели и тут же видит её
 * в чате. Ради этого SERVICES объявлены на весь файл, а не внутри виджета.
 */
(function () {
  var $ = function (id) { return document.getElementById(id) }

  var chat = $('chat'), choices = $('choices'), row = $('row')
  var field = $('field'), send = $('send')
  var tg = $('tg'), cab = $('cab'), cabTabs = $('cab-tabs')
  var panelNav = $('panel-nav'), panelMain = $('panel-main')
  if (!chat) return

  // ─────────────────────────── общая память ───────────────────────────

  var SERVICES = [
    {
      title: 'Доверенность на автомобиль',
      mode: 'documents', lead: 'в день обращения', price: '1 800 ₽',
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
      mode: 'visit', lead: 'готово за 30 минут', price: '1 500 ₽',
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
      mode: 'visit', lead: '3–5 рабочих дней', price: 'от 8 000 ₽',
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
      mode: 'visit', lead: 'по записи', price: 'консультация бесплатно',
      words: ['наслед', 'завещ', 'смерт', 'умер'],
      docs: [
        { t: 'Паспорт' },
        { t: 'Свидетельство о смерти' },
        { t: 'Документы о родстве', d: 'свидетельство о рождении, о браке' },
        { t: 'Завещание', opt: true }
      ]
    }
  ]

  var STAFF = [
    { name: 'Петрова Анна Сергеевна', role: 'помощник нотариуса', tg: true, on: true },
    { name: 'Волков Игорь Павлович', role: 'помощник нотариуса', tg: true, on: true },
    { name: 'Зайцева Ольга Ивановна', role: 'стажёр', tg: false, on: true },
    { name: 'Крылов Денис Олегович', role: 'помощник нотариуса', tg: true, on: false }
  ]

  var HOURS = [
    { d: 'Понедельник', h: '10:00–19:00', off: false },
    { d: 'Вторник', h: '10:00–19:00', off: false },
    { d: 'Среда', h: '10:00–19:00', off: false },
    { d: 'Четверг', h: '10:00–19:00', off: false },
    { d: 'Пятница', h: '10:00–17:00', off: false },
    { d: 'Суббота', h: '—', off: true },
    { d: 'Воскресенье', h: '—', off: true }
  ]

  var MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

  var requests = []          // очередь кабинета
  var number = 1041
  var timers = []
  var playing = false
  var state = {}

  function later(fn, ms) { var id = setTimeout(fn, ms); timers.push(id); return id }
  function stopAll() { timers.forEach(clearTimeout); timers = []; playing = false }
  function esc(s) { return String(s).replace(/[<>&]/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c] }) }

  function slots() {
    var out = [], d = new Date()
    d.setDate(d.getDate() + 1)
    var hours = ['10:00', '11:30', '14:00', '16:30']
    for (var day = 0; day < 2; day++) {
      for (var i = 0; i < hours.length; i++) {
        var w = new Date(d); w.setDate(d.getDate() + day)
        out.push(w.getDate() + ' ' + MONTHS[w.getMonth()] + ', ' + hours[i])
      }
    }
    return out.slice(0, 6)
  }

  // ─────────────────────────── чат клиента ───────────────────────────

  function bubble(cls, build) {
    var el = document.createElement('div')
    el.className = 'b ' + cls
    build(el)
    chat.appendChild(el)
    chat.scrollTop = chat.scrollHeight
    return el
  }

  function say(t) { return bubble('bot', function (e) { e.textContent = t }) }
  function you(t) { return bubble('me', function (e) { e.textContent = t }) }
  function typing() { return bubble('bot dots', function (e) { e.innerHTML = '<span></span><span></span><span></span>' }) }

  /* Пауза перед ответом — не украшение: без неё реплики появляются разом
     и читатель не понимает, кто кому отвечает. */
  function reply(text, next) {
    var dots = typing()
    later(function () {
      dots.remove()
      if (text) say(text)
      if (next) next()
    }, text ? 620 : 420)
  }

  function clearChoices() { choices.innerHTML = '' }

  function choice(label, hint, onPick, cls) {
    var b = document.createElement('button')
    b.type = 'button'
    b.className = 'chip' + (cls ? ' ' + cls : '')
    var t = document.createElement('div'); t.textContent = label; b.appendChild(t)
    if (hint) { var h = document.createElement('div'); h.className = 'm'; h.textContent = hint; b.appendChild(h) }
    b.addEventListener('click', function () { clearChoices(); hideInput(); onPick() })
    choices.appendChild(b)
    return b
  }

  var onText = null

  function ask(placeholder, suggestion, handler) {
    field.value = ''
    field.placeholder = placeholder
    row.hidden = false
    onText = handler
    if (suggestion) {
      choice('↳ ' + suggestion, 'нажмите, чтобы не набирать', function () { handler(suggestion) }, 'ghost')
    }
    later(function () { try { field.focus({ preventScroll: true }) } catch (e) {} }, 60)
  }

  function hideInput() { row.hidden = true; onText = null }

  function submitText() {
    var v = field.value.trim()
    if (!v || !onText) return
    var fn = onText
    clearChoices(); hideInput(); fn(v)
  }

  send.addEventListener('click', submitText)
  field.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); submitText() } })

  function hint(s) {
    return (s.mode === 'visit' ? 'нужен личный визит' : 'документы можно прислать онлайн') + ' · ' + s.lead
  }

  function listAll() {
    clearChoices()
    SERVICES.forEach(function (s) { choice(s.title, hint(s), function () { pick(s) }) })
  }

  function search(text) {
    var q = text.toLowerCase()
    var found = SERVICES.filter(function (s) {
      return s.words.some(function (w) { return q.indexOf(w) >= 0 }) ||
             s.title.toLowerCase().indexOf(q) >= 0
    })
    if (!found.length) {
      reply('Не нашёл подходящей услуги по этим словам. Посмотрите полный список или напишите иначе.', function () {
        listAll()
        ask('Попробуйте другими словами', null, function (a) { you(a); search(a) })
      })
      return
    }
    reply(found.length === 1 ? 'Похоже, вам нужно это:' : 'Вот что подходит:', function () {
      clearChoices()
      found.forEach(function (s) { choice(s.title, hint(s), function () { pick(s) }) })
    })
  }

  function pick(svc) {
    state.service = svc
    state.slot = null
    you(svc.title)
    reply(null, function () {
      bubble('bot', function (el) {
        var t = document.createElement('div')
        t.className = 't'
        t.textContent = svc.docs.length ? 'Что понадобится' : 'Документы не требуются'
        el.appendChild(t)

        var f = document.createElement('div')
        f.className = 'f'
        f.textContent = ['срок: ' + svc.lead, svc.price,
          svc.mode === 'visit' ? 'нужен личный визит' : 'документы онлайн'].join(' · ')
        el.appendChild(f)

        if (svc.docs.length) {
          var ul = document.createElement('ul')
          ul.className = 'docs'
          svc.docs.forEach(function (doc) {
            var li = document.createElement('li')
            if (doc.opt) li.className = 'optional'
            var line = document.createElement('div')
            line.textContent = doc.t
            if (doc.opt) {
              var o = document.createElement('span'); o.className = 'opt'; o.textContent = 'если есть'
              line.appendChild(o)
            }
            li.appendChild(line)
            if (doc.d) { var d = document.createElement('div'); d.className = 'd'; d.textContent = doc.d; li.appendChild(d) }
            ul.appendChild(li)
          })
          el.appendChild(ul)
        }
      })
      choice('Оформить заявку', null, askName, 'primary')
      choice('Выбрать другую услугу', null, function () { reply('Хорошо. Вот все услуги:', listAll) }, 'quiet')
    })
  }

  function askName() {
    reply('Как вас зовут? Напишите фамилию и имя.', function () {
      ask('Фамилия и имя', 'Смирнов Алексей', function (v) { state.name = v; you(v); askPhone() })
    })
  }

  function askPhone() {
    reply('Оставьте номер телефона для связи.', function () {
      ask('+7 900 000-00-00', '+7 903 555-12-40', function (v) { state.phone = v; you(v); askConsent() })
    })
  }

  function askConsent() {
    reply('Для оформления нужно согласие на обработку персональных данных. Они используются только для подготовки нотариального действия.', function () {
      choice('Согласен', null, function () { you('Согласен'); afterConsent() }, 'primary')
      choice('Отказаться', null, function () {
        you('Отказаться')
        reply('Хорошо. Без согласия заявку принять нельзя, но вы всегда можете позвонить нотариусу напрямую.', function () {
          choice('Начать заново', null, start, 'quiet')
        })
      }, 'quiet')
    })
  }

  function afterConsent() {
    if (state.service.mode !== 'visit') { submit(); return }
    reply('Выберите удобное время приёма.', function () {
      clearChoices()
      slots().forEach(function (s) {
        choice(s, null, function () { state.slot = s; you(s); submit() }, 'slot')
      })
    })
  }

  function submit() {
    clearChoices(); hideInput()
    var dots = typing()
    later(function () {
      dots.remove()
      state.number = ++number
      say('Заявка № ' + state.number + ' принята.')
      later(telegram, 260)
      later(addRequest, 700)
      later(function () {
        if (state.slot) {
          reply('Ждём вас в выбранное время. Сотрудник свяжется для подтверждения.', tail)
        } else {
          reply(null, function () {
            bubble('bot', function (el) {
              var p = document.createElement('div'); p.textContent = 'Документы можно прислать прямо сейчас:'; el.appendChild(p)
              var a = document.createElement('span'); a.className = 'lnk'; a.textContent = 'Загрузить документы'; el.appendChild(a)
              var n = document.createElement('div'); n.className = 'f'; n.style.marginTop = '6px'
              n.textContent = 'Ссылка действует 30 минут, догрузить забытое можно по ней же.'
              el.appendChild(n)
            })
            tail()
          })
        }
      }, 900)
    }, 900)
  }

  function tail() {
    later(function () {
      bubble('note', function (e) { e.textContent = 'Заявка у сотрудников — посмотрите два окна справа →' })
      choice('Пройти ещё раз', null, start, 'quiet')
    }, 500)
  }

  function start() {
    stopAll()
    chat.innerHTML = ''
    clearChoices(); hideInput()
    state = {}
    reply('Здравствуйте. Подскажу, какие документы нужны, и приму заявку.', function () {
      reply('Напишите своими словами, что нужно — например «доверенность на машину». Или выберите из списка.', function () {
        listAll()
        ask('Что нужно оформить?', null, function (t) { you(t); search(t) })
        var first = choices.querySelector('.chip')
        if (first) { first.classList.add('pulse'); later(function () { first.classList.remove('pulse') }, 5200) }
      })
    })
  }

  // ─────────────────────────── Telegram ───────────────────────────

  function telegram() {
    var empty = $('tg-empty')
    if (empty) empty.remove()
    var el = document.createElement('div')
    el.className = 'tg-msg'
    var lines = [
      'Новая заявка № ' + state.number,
      state.service.title, '',
      state.name + ' · ' + state.phone,
      state.slot ? 'Приём: ' + state.slot : 'Клиент присылает документы онлайн'
    ]
    var body = document.createElement('div'); body.className = 'tg-text'; body.textContent = lines.join('\n')
    el.appendChild(body)
    var now = new Date()
    var time = document.createElement('div'); time.className = 'tg-time'
    time.textContent = now.getHours() + ':' + String(now.getMinutes()).padStart(2, '0')
    el.appendChild(time)
    tg.appendChild(el)
    tg.scrollTop = tg.scrollHeight
    flash(el)
  }

  // ─────────────────────────── кабинет ───────────────────────────

  var activeTab = 'new'

  function addRequest() {
    requests.unshift({
      n: state.number,
      service: state.service.title,
      who: state.name + ' · ' + state.phone,
      when: state.slot ? 'приём ' + state.slot : 'документы онлайн',
      st: 'new',
      lead: null
    })
    activeTab = 'new'
    renderCab(true)
  }

  function counts() {
    var c = { new: 0, work: 0, done: 0 }
    requests.forEach(function (r) { c[r.st]++ })
    return c
  }

  function renderCab(highlightFirst) {
    var c = counts()
    Array.prototype.forEach.call(cabTabs.children, function (btn) {
      var s = btn.getAttribute('data-state')
      btn.classList.toggle('on', s === activeTab)
      btn.querySelector('b').textContent = c[s]
    })

    var list = requests.filter(function (r) { return r.st === activeTab })
    cab.innerHTML = ''

    if (!list.length) {
      var e = document.createElement('div')
      e.className = 'cab-empty'
      e.textContent = activeTab === 'new' ? 'Новых заявок нет'
        : activeTab === 'work' ? 'Никто ничего не ведёт'
        : 'Готовых заявок пока нет'
      cab.appendChild(e)
      return
    }

    list.forEach(function (r, i) {
      var el = document.createElement('div')
      el.className = 'cab-row' + (r.st !== 'new' ? ' quiet' : '')

      var tag = r.st === 'new' ? '<span class="tag">новая</span>'
        : r.st === 'work' ? '<span class="tag work">в работе</span>'
        : '<span class="tag done">готова</span>'
      var head = document.createElement('div')
      head.className = 'cab-top'
      head.innerHTML = '<b>№ ' + r.n + '</b>' + tag
      el.appendChild(head)

      var t = document.createElement('div'); t.className = 'cab-title'; t.textContent = r.service; el.appendChild(t)
      var m = document.createElement('div'); m.className = 'cab-meta'; m.textContent = r.who + ' · ' + r.when; el.appendChild(m)

      if (r.st === 'new') {
        var take = document.createElement('button')
        take.type = 'button'; take.className = 'cab-take'; take.textContent = 'Взять в работу'
        take.addEventListener('click', function () {
          r.st = 'work'
          r.lead = 'Петрова А.'
          activeTab = 'work'
          renderCab(true)
        })
        el.appendChild(take)
      } else if (r.st === 'work') {
        var who = document.createElement('div'); who.className = 'cab-who'
        who.textContent = 'Ведёт ' + r.lead + ' — остальные её уже не возьмут'
        el.appendChild(who)
        var fin = document.createElement('button')
        fin.type = 'button'; fin.className = 'cab-take ghost'; fin.textContent = 'Завершить'
        fin.addEventListener('click', function () { r.st = 'done'; activeTab = 'done'; renderCab(true) })
        el.appendChild(fin)
      } else {
        var d = document.createElement('div'); d.className = 'cab-who'
        d.textContent = 'Завершила ' + (r.lead || 'Петрова А.')
        el.appendChild(d)
      }

      cab.appendChild(el)
      if (highlightFirst && i === 0) flash(el)
    })
  }

  cabTabs.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-state]')
    if (!btn) return
    activeTab = btn.getAttribute('data-state')
    renderCab(false)
  })

  function flash(el) {
    el.classList.add('arrive')
    later(function () { el.classList.remove('arrive') }, 1400)
  }

  // ─────────────────────────── панель нотариуса ───────────────────────────

  /* Правка в панели меняет тот же SERVICES, из которого читает чат. Поэтому
     после изменения диалог начинается заново — иначе нотариус смотрит на
     карточку услуги, набранную до его правки, и не верит, что она подействовала. */
  function afterEdit(what) {
    renderPanel()
    start()
    later(function () {
      bubble('note', function (e) { e.textContent = '↑ ' + what })
    }, 40)
    var wdg = $('wdg')
    wdg.classList.add('bumped')
    later(function () { wdg.classList.remove('bumped') }, 1200)
  }

  var panelTab = 'services'

  var TABS = {
    services: function () {
      var h = '<div class="p-head"><h4>Услуги и цены</h4>' +
              '<p>Меняйте прямо в полях. Клиент увидит новое сразу — виджет читает этот же список.</p></div>' +
              '<div class="p-rows">'
      SERVICES.forEach(function (s, i) {
        h += '<div class="p-row">' +
          '<input class="p-in grow" data-svc="' + i + '" data-f="title" value="' + esc(s.title) + '">' +
          '<input class="p-in mid" data-svc="' + i + '" data-f="lead" value="' + esc(s.lead) + '">' +
          '<input class="p-in mid" data-svc="' + i + '" data-f="price" value="' + esc(s.price) + '">' +
          '<button type="button" class="p-toggle" data-mode="' + i + '">' +
            (s.mode === 'visit' ? 'личный визит' : 'документы онлайн') + '</button>' +
          '</div>'
      })
      return h + '</div><p class="p-note">Так же добавляются и убираются услуги целиком.</p>'
    },

    docs: function () {
      var h = '<div class="p-head"><h4>Списки документов</h4>' +
              '<p>Свой для каждой услуги. Уберите лишнее или добавьте своё — клиент получит именно этот список.</p></div>'
      SERVICES.forEach(function (s, i) {
        h += '<div class="p-block"><div class="p-block-t">' + esc(s.title) + '</div><ul class="p-docs">'
        s.docs.forEach(function (d, j) {
          h += '<li><span>' + esc(d.t) + (d.opt ? ' <i>если есть</i>' : '') + '</span>' +
               '<button type="button" class="p-x" data-del="' + i + ':' + j + '" aria-label="Убрать">×</button></li>'
        })
        h += '</ul><div class="p-add"><input class="p-in grow" data-add="' + i + '" placeholder="Добавить документ"> ' +
             '<button type="button" class="p-btn" data-addbtn="' + i + '">Добавить</button></div></div>'
      })
      return h
    },

    schedule: function () {
      var h = '<div class="p-head"><h4>Приём и выходные</h4>' +
              '<p>Клиент выбирает время только из свободного. Двое на один час не запишутся.</p></div>' +
              '<div class="p-rows">'
      HOURS.forEach(function (d, i) {
        h += '<div class="p-row line"><span class="p-day">' + d.d + '</span>' +
          '<input class="p-in mid" data-hour="' + i + '" value="' + esc(d.h) + '"' + (d.off ? ' disabled' : '') + '>' +
          '<button type="button" class="p-toggle' + (d.off ? ' off' : '') + '" data-off="' + i + '">' +
          (d.off ? 'выходной' : 'принимаем') + '</button></div>'
      })
      return h + '</div><p class="p-note">Отдельно отмечаются праздники и дни, когда приёма нет.</p>'
    },

    staff: function () {
      var h = '<div class="p-head"><h4>Сотрудники</h4>' +
              '<p>Заводите, отключайте, подключайте Telegram. Уволился — доступ закрывается одним нажатием.</p></div>' +
              '<div class="p-rows">'
      STAFF.forEach(function (p, i) {
        h += '<div class="p-row line">' +
          '<span class="p-who"><b>' + esc(p.name) + '</b><i>' + esc(p.role) + '</i></span>' +
          '<span class="p-tg' + (p.tg ? ' yes' : '') + '">' + (p.tg ? 'Telegram подключён' : 'без Telegram') + '</span>' +
          '<button type="button" class="p-toggle' + (p.on ? '' : ' off') + '" data-staff="' + i + '">' +
          (p.on ? 'работает' : 'отключён') + '</button></div>'
      })
      return h + '</div>'
    },

    audit: function () {
      var rows = [
        ['сегодня, 11:04', 'Петрова А.', 'Открыт документ', 'паспорт_смирнов.pdf'],
        ['сегодня, 10:58', 'Петрова А.', 'Заявка взята в работу', '№ 1041'],
        ['сегодня, 09:12', 'Волков И.', 'Вход в панель', '—'],
        ['вчера, 18:30', 'Иванова М. С.', 'Заведён сотрудник', 'Зайцева Ольга Ивановна'],
        ['вчера, 16:02', 'Волков И.', 'Открыт документ', 'свидетельство.pdf']
      ]
      var h = '<div class="p-head"><h4>Журнал доступа</h4>' +
              '<p>Кто и когда открывал документы клиентов. По 152-ФЗ оператор обязан уметь ответить на этот вопрос.</p></div>' +
              '<table class="p-table"><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Что</th></tr>'
      rows.forEach(function (r) {
        h += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td><td>' + r[2] + '</td><td class="dim">' + r[3] + '</td></tr>'
      })
      return h + '</table><p class="p-note">Отбирается по сотруднику и датам, выгружается в файл для проверки.</p>'
    },

    look: function () {
      return '<div class="p-head"><h4>Вид виджета</h4>' +
        '<p>Подгоняется под ваш сайт. Нажмите — окно клиента наверху перекрасится.</p></div>' +
        '<div class="p-swatches">' +
        ['#2f5bea', '#b89a5a', '#1d9e75', '#c05c2e', '#534ab7', '#0b0b0d'].map(function (c) {
          return '<button type="button" class="p-sw" data-accent="' + c + '" style="--sw:' + c + '"></button>'
        }).join('') +
        '</div><p class="p-note">Шрифт и светлое/тёмное оформление выбираются там же.</p>'
    }
  }

  function renderPanel() {
    Array.prototype.forEach.call(panelNav.children, function (b) {
      b.classList.toggle('on', b.getAttribute('data-tab') === panelTab)
    })
    panelMain.innerHTML = TABS[panelTab]()
  }

  panelNav.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-tab]')
    if (!b) return
    panelTab = b.getAttribute('data-tab')
    renderPanel()
  })

  panelMain.addEventListener('input', function (e) {
    var t = e.target
    if (t.dataset.svc !== undefined) {
      SERVICES[+t.dataset.svc][t.dataset.f] = t.value
    } else if (t.dataset.hour !== undefined) {
      HOURS[+t.dataset.hour].h = t.value
    }
  })

  /* Правка подхватывается по «уходу» из поля: перерисовывать чат на каждую
     букву — значит не дать дописать слово. Значение читается здесь ещё раз,
     а не берётся из обработчика input: порядок событий зависит от того, как
     поле меняли — с клавиатуры, вставкой или подстановкой из браузера. */
  panelMain.addEventListener('change', function (e) {
    var t = e.target
    if (t.dataset.svc === undefined) return
    SERVICES[+t.dataset.svc][t.dataset.f] = t.value
    afterEdit('Услуги обновлены — посмотрите в списке')
  })

  panelMain.addEventListener('click', function (e) {
    var t = e.target

    var mode = t.closest('[data-mode]')
    if (mode) {
      var s = SERVICES[+mode.getAttribute('data-mode')]
      s.mode = s.mode === 'visit' ? 'documents' : 'visit'
      afterEdit('Порядок приёма изменён')
      return
    }

    var del = t.closest('[data-del]')
    if (del) {
      var p = del.getAttribute('data-del').split(':')
      SERVICES[+p[0]].docs.splice(+p[1], 1)
      afterEdit('Список документов изменён')
      return
    }

    var addBtn = t.closest('[data-addbtn]')
    if (addBtn) {
      var i = +addBtn.getAttribute('data-addbtn')
      var input = panelMain.querySelector('[data-add="' + i + '"]')
      var v = input.value.trim()
      if (!v) { input.focus(); return }
      SERVICES[i].docs.push({ t: v })
      afterEdit('Список документов изменён')
      return
    }

    var off = t.closest('[data-off]')
    if (off) {
      var d = HOURS[+off.getAttribute('data-off')]
      d.off = !d.off
      if (d.off) d.h = '—'
      else if (d.h === '—') d.h = '10:00–19:00'
      renderPanel()
      return
    }

    var st = t.closest('[data-staff]')
    if (st) {
      var p2 = STAFF[+st.getAttribute('data-staff')]
      p2.on = !p2.on
      renderPanel()
      return
    }

    var sw = t.closest('[data-accent]')
    if (sw) {
      document.documentElement.style.setProperty('--wdg-accent', sw.getAttribute('data-accent'))
      panelMain.querySelectorAll('.p-sw').forEach(function (b) { b.classList.remove('on') })
      sw.classList.add('on')
      var wdg = $('wdg')
      wdg.classList.add('bumped')
      later(function () { wdg.classList.remove('bumped') }, 1200)
    }
  })

  // ─────────────────────────── запуск и показ ───────────────────────────

  function reset() {
    stopAll()
    tg.innerHTML = '<div class="tg-empty" id="tg-empty">Здесь появится заявка,<br>как только клиент её отправит</div>'
    requests = []
    activeTab = 'new'
    renderCab(false)
    start()
  }

  $('replay').addEventListener('click', reset)

  /* Показ целиком — для тех, кто не хочет кликать сам. Каждый шаг ждёт
     появления своей кнопки, а не наступления секунды: у бота паузы
     на «печатает», от них расписание разъезжается и показ застревает. */
  var SCENARIO = [
    function () { return findChip('Согласие на выезд') },
    function () { return findChip('Оформить заявку') },
    function () { return findChip('Смирнов') },
    function () { return findChip('+7 903') },
    function () { return findChip('Согласен') },
    function () { return choices.querySelector('.chip.slot') },
    function () { return cab.querySelector('.cab-take') }
  ]

  function findChip(m) {
    var chips = choices.querySelectorAll('.chip')
    for (var i = 0; i < chips.length; i++) if (chips[i].textContent.indexOf(m) >= 0) return chips[i]
    return null
  }

  function play(step, waited) {
    if (!playing) return
    if (step >= SCENARIO.length) { playing = false; return }
    var target = SCENARIO[step]()
    if (target) {
      later(function () {
        if (!playing) return
        target.click()
        play(step + 1, 0)
      }, 750)
      return
    }
    if (waited > 9000) { playing = false; return }
    later(function () { play(step, waited + 120) }, 120)
  }

  $('auto').addEventListener('click', function () {
    if (playing) { stopAll(); return }
    reset()
    playing = true
    play(0, 0)
  })

  renderCab(false)
  renderPanel()
  start()
})()
