/* House style for PDFs built in the browser with jsPDF (+ autoTable): Arial, never
   smaller than 12 pt, and the letterhead logo centered — the same rule the server
   applies to every exported report (export_style.py).

   Load right after jspdf / jspdf-autotable. It wraps window.jspdf.jsPDF so every new
   document:
     - gets "Arial" (the bundled Arimo font — Arial's metric-compatible twin, SIL OFL —
       registered under that name) and draws helvetica/times/courier text in it;
     - never sets a font size below 12 (autoTable's cell fonts included).
   DSSPdf.logo(doc, y) draws the logo centered at y and returns the y below it.
   Fonts and logo are fetched once, in the background, when the page loads. */
(function () {
    'use strict';
    var FONT = 'Arial', MIN_PT = 12;
    var FACES = { normal: 'Arimo-Regular.ttf', bold: 'Arimo-Bold.ttf',
                  italic: 'Arimo-Italic.ttf', bolditalic: 'Arimo-BoldItalic.ttf' };
    var FONT_DIR = '/static/fonts/arimo/';
    var LOGO_URL = '/static/img/pup-logo.png';
    var fontData = {}, logoData = null;

    function toBase64(buf) {
        var bytes = new Uint8Array(buf), s = '';
        for (var i = 0; i < bytes.length; i += 0x8000)
            s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        return btoa(s);
    }

    var ready = Promise.all(Object.keys(FACES).map(function (style) {
        return fetch(FONT_DIR + FACES[style]).then(function (r) { return r.ok ? r.arrayBuffer() : null; })
            .then(function (b) { if (b) fontData[style] = toBase64(b); });
    }).concat([
        fetch(LOGO_URL).then(function (r) { return r.ok ? r.blob() : null; }).then(function (blob) {
            if (!blob) return;
            return new Promise(function (res) {
                var fr = new FileReader();
                fr.onload = function () { logoData = fr.result; res(); };
                fr.onerror = function () { res(); };
                fr.readAsDataURL(blob);
            });
        })
    ])).catch(function () { /* fall back to the built-in font; sizes still apply */ });

    function hasFont() { return !!(fontData.normal && fontData.bold); }

    function mapName(name) {
        return hasFont() && /^(helvetica|times|courier)/i.test(String(name || '')) ? FONT : name;
    }

    function style(doc) {
        if (doc.__dssHouse) return doc;
        doc.__dssHouse = true;
        if (hasFont()) {
            Object.keys(fontData).forEach(function (st) {
                var file = 'Arimo-' + st + '.ttf';
                doc.addFileToVFS(file, fontData[st]);
                doc.addFont(file, FONT, st);
            });
        }
        var setFont = doc.setFont, setFontSize = doc.setFontSize;
        doc.setFont = function (name, fontStyle, weight) {
            return setFont.call(this, mapName(name), fontStyle, weight);
        };
        doc.setFontSize = function (size) {
            return setFontSize.call(this, Math.max(MIN_PT, Number(size) || MIN_PT));
        };
        // autoTable lays its cells out from its own style options (not from the doc's
        // current size), so the house font and minimum size go into those options too.
        if (typeof doc.autoTable === 'function') {
            var autoTable = doc.autoTable;
            doc.autoTable = function (opts) {
                if (opts && typeof opts === 'object' && !Array.isArray(opts)) opts = houseTableOptions(opts);
                return autoTable.apply(this, [opts].concat([].slice.call(arguments, 1)));
            };
        }
        try { doc.setFont(FONT, 'normal'); } catch (e) { /* built-in font stays */ }
        doc.setFontSize(doc.getFontSize());
        return doc;
    }

    function houseStyle(st) {
        st = Object.assign({}, st || {});
        if (st.fontSize != null) st.fontSize = Math.max(MIN_PT, Number(st.fontSize) || MIN_PT);
        if (st.font != null) st.font = mapName(st.font);
        return st;
    }

    function houseTableOptions(opts) {
        var o = Object.assign({}, opts);
        var base = houseStyle(o.styles);
        if (base.fontSize == null) base.fontSize = MIN_PT;      // autoTable's default is 10
        if (hasFont() && base.font == null) base.font = FONT;
        o.styles = base;
        ['headStyles', 'bodyStyles', 'footStyles', 'alternateRowStyles'].forEach(function (k) {
            if (o[k]) o[k] = houseStyle(o[k]);
        });
        if (o.columnStyles) {
            var cs = {};
            Object.keys(o.columnStyles).forEach(function (k) { cs[k] = houseStyle(o.columnStyles[k]); });
            o.columnStyles = cs;
        }
        return o;
    }

    function install() {
        var ns = window.jspdf;
        if (!ns || !ns.jsPDF || ns.jsPDF.__dssHouse) return;
        var Orig = ns.jsPDF;
        var Wrapped = function () {
            var doc = new (Function.prototype.bind.apply(Orig, [null].concat([].slice.call(arguments))))();
            return style(doc);
        };
        Object.keys(Orig).forEach(function (k) { Wrapped[k] = Orig[k]; });
        Wrapped.API = Orig.API;
        Wrapped.prototype = Orig.prototype;
        Wrapped.__dssHouse = true;
        ns.jsPDF = Wrapped;
    }
    install();

    window.DSSPdf = {
        ready: ready,
        MIN_PT: MIN_PT,
        /* Vertical space DSSPdf.logo() takes (0 when there is no logo to draw). */
        logoSpace: function (size) { return logoData ? (size || 14) + 2 : 0; },
        /* Draw the letterhead logo centered across the page at y (mm by default);
           returns the y just below it (y unchanged if the logo isn't available). */
        logo: function (doc, y, size) {
            size = size || 14;
            if (!logoData) return y;
            var W = doc.internal.pageSize.getWidth();
            try { doc.addImage(logoData, 'PNG', W / 2 - size / 2, y, size, size); }
            catch (e) { return y; }
            return y + size + 2;
        }
    };
})();
