/* Подстановка реквизитов в правовые страницы.
 *
 * Пока `legal.js` не заполнен, документ не показывается вовсе — вместо него
 * короткая строка о том, что он готовится. Оферта с прочерком на месте ИНН
 * не документ: она говорит покупателю ровно обратное тому, зачем её открыли.
 *
 * Признак заполненности — ИНН: без него не обойтись ни в договоре,
 * ни в проверке по реестрам, а остальные поля второстепенны.
 */
(function () {
  var legal = window.LEGAL || {};
  var ready = Boolean(legal.inn);

  var doc = document.getElementById("doc");
  var draft = document.getElementById("draft");
  if (doc) doc.hidden = !ready;
  if (draft) draft.hidden = ready;
  if (!ready) return;

  var slots = document.querySelectorAll("[data-legal]");
  for (var i = 0; i < slots.length; i++) {
    var key = slots[i].getAttribute("data-legal");
    slots[i].textContent = legal[key] || "";
  }

  // Необязательные строки: ОГРН, адрес и телефон могут отсутствовать,
  // и пустая строка «Телефон —» выглядит недоделкой, а не сдержанностью.
  var blocks = document.querySelectorAll("[data-legal-block]");
  for (var j = 0; j < blocks.length; j++) {
    if (!legal[blocks[j].getAttribute("data-legal-block")]) {
      blocks[j].parentNode.removeChild(blocks[j]);
    }
  }
})();
