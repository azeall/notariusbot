/* Подстановка реквизитов в правовые страницы.
 *
 * Документы показываются всегда. Сначала было иначе: пока в legal.js нет ИНН,
 * страница говорила «документ готовится». Замысел был честный — оферта
 * с прочерком вместо реквизитов выглядит недоделкой, — но цена оказалась
 * выше пользы: без опубликованной оферты покупателю нечего принять,
 * а проверяющий видит сайт, который продаёт услуги вообще без договора.
 *
 * Поэтому теперь: текст на месте, а поля, которых ещё нет, не выводятся —
 * ни прочерком, ни пустой строкой. Вместо них раздел о реквизитах говорит,
 * что полные сведения указываются в счёте. Появится ИНН в legal.js —
 * подставится сам, и оговорка исчезнет.
 */
(function () {
  var legal = window.LEGAL || {};

  var slots = document.querySelectorAll("[data-legal]");
  for (var i = 0; i < slots.length; i++) {
    var key = slots[i].getAttribute("data-legal");
    slots[i].textContent = legal[key] || "";
  }

  // Строки, которых может не быть: статус, ФИО, ИНН, ОГРН, адрес, телефон.
  // Пустая строка «Телефон —» выглядит недоделкой, а не сдержанностью.
  var blocks = document.querySelectorAll("[data-legal-block]");
  for (var j = 0; j < blocks.length; j++) {
    if (!legal[blocks[j].getAttribute("data-legal-block")]) {
      blocks[j].parentNode.removeChild(blocks[j]);
    }
  }

  // Кто именно предлагает: с реквизитами или пока без них. Оба варианта
  // лежат в разметке, показывается ровно один — так текст остаётся связным
  // в обоих случаях, а не собирается из обрывков.
  var named = document.querySelector("[data-legal-named]");
  var unnamed = document.querySelector("[data-legal-unnamed]");
  if (named) named.hidden = !legal.inn;
  if (unnamed) unnamed.hidden = Boolean(legal.inn);
})();
