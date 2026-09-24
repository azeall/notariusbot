(function () {
  var items = document.querySelectorAll('.launch-list input[type="checkbox"]');
  var progress = document.getElementById('launch-progress');
  var print = document.getElementById('print-tour');
  function update() {
    var done = Array.from(items).filter(function (item) { return item.checked; }).length;
    progress.textContent = 'Подготовлено: ' + done + ' из ' + items.length;
  }
  items.forEach(function (item) { item.checked = false; item.addEventListener('change', update); });
  progress.hidden = false;
  print.hidden = false;
  print.addEventListener('click', function () { window.print(); });
  update();
})();
