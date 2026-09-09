document$.subscribe(function () {
  document.querySelectorAll("article table:not([data-tablesort])").forEach(function (table) {
    new Tablesort(table);
    table.dataset.tablesort = "true";
  });
});
