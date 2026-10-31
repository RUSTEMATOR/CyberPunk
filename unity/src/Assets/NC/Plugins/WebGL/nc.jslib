// The bridge from C# (NcCity, NcFight) to the page of the Unity client (WebGLTemplates/NC/index.html). docs/PLAN_UNITY.md
// NcAxis reads the input the page collects: 0 move x, 1 move y, 2 look dx, 3 look dy (2 and 3 are consumed), 4 run, 5 fire.
mergeInto(LibraryManager.library, {
  NcUi: function (p) { var s = UTF8ToString(p); if (window.ncUi) window.ncUi(s); },
  NcNear: function (p) { var s = UTF8ToString(p); if (window.ncNear) window.ncNear(s); },
  NcStat: function (p) { var s = UTF8ToString(p); if (window.ncStat) window.ncStat(s); },
  NcFightUi: function (p) { var s = UTF8ToString(p); if (window.ncFightUi) window.ncFightUi(s); },
  NcFx: function (p) { var s = UTF8ToString(p); if (window.ncFx) window.ncFx(s); },
  NcAxis: function (i) {
    var a = window.ncIn;
    if (!a) return 0;
    var v = +a[i] || 0;
    if (i === 2 || i === 3) a[i] = 0;
    return v;
  }
});
