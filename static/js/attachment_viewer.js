/* Request attachment viewer — opens a request's attachment (students' consent letter) in a
   popup on the same page instead of a new tab. Images fit the popup; PDFs show in the
   browser's own PDF viewer inside it. Close with ×, the Close button, Esc, or a click outside.

   Usage: openAttachmentViewer(attachmentId, filename, mimeType)
   Files come from /api/requests/attachment/<id> (access checked by the server). */
(function () {
    var STYLE_ID = 'att-viewer-style';
    var CSS = [
        '.att-viewer{position:fixed;inset:0;z-index:10050;background:rgba(15,0,0,.62);display:flex;',
        'align-items:center;justify-content:center;padding:16px;}',
        '.att-viewer-box{background:#fff;border-radius:12px;width:min(920px,100%);height:min(88vh,100%);',
        'display:flex;flex-direction:column;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.35);}',
        '.att-viewer-hd{display:flex;align-items:center;gap:10px;padding:12px 16px;background:#800000;color:#fff;}',
        '.att-viewer-hd i.att-ico{font-size:1rem;opacity:.9;}',
        '.att-viewer-name{flex:1;min-width:0;font-weight:700;font-size:.88rem;overflow:hidden;',
        'text-overflow:ellipsis;white-space:nowrap;}',
        '.att-viewer-x{background:none;border:0;color:#fff;font-size:1.25rem;cursor:pointer;opacity:.85;line-height:1;padding:2px 4px;}',
        '.att-viewer-x:hover{opacity:1;}',
        '.att-viewer-body{flex:1;min-height:0;background:#f4f4f4;display:flex;align-items:center;justify-content:center;overflow:auto;}',
        '.att-viewer-body img{max-width:100%;max-height:100%;object-fit:contain;display:block;background:#fff;}',
        '.att-viewer-body iframe{width:100%;height:100%;border:0;background:#fff;}',
        '.att-viewer-msg{color:#777;font-size:.85rem;padding:20px;text-align:center;}',
        '.att-viewer-ft{display:flex;justify-content:flex-end;gap:10px;padding:10px 16px;border-top:1px solid #eee;flex-wrap:wrap;}',
        '.att-viewer-btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:7px;font-size:.78rem;',
        'font-weight:700;cursor:pointer;text-decoration:none;border:1.5px solid #800000;}',
        '.att-viewer-btn.primary{background:#800000;color:#fff;}',
        '.att-viewer-btn.ghost{background:#fff;color:#800000;}',
        '@media (max-width:600px){.att-viewer{padding:8px;}.att-viewer-box{height:92vh;}}'
    ].join('');

    function esc(t) {
        return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function close() {
        var el = document.getElementById('attViewer');
        if (el) el.remove();
        document.removeEventListener('keydown', onKey, true);
        document.body.style.overflow = document.body.dataset.attPrevOverflow || '';
    }
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }

    window.openAttachmentViewer = function (id, filename, mime) {
        id = Number(id);
        if (!id) return;
        if (!document.getElementById(STYLE_ID)) {
            var st = document.createElement('style');
            st.id = STYLE_ID; st.textContent = CSS;
            document.head.appendChild(st);
        }
        close();
        var url = '/api/requests/attachment/' + id;
        var isPdf = mime === 'application/pdf' || /\.pdf$/i.test(filename || '');
        var ov = document.createElement('div');
        ov.id = 'attViewer';
        ov.className = 'att-viewer';
        ov.innerHTML =
            '<div class="att-viewer-box" role="dialog" aria-modal="true" aria-label="Attachment">' +
              '<div class="att-viewer-hd">' +
                '<i class="fas ' + (isPdf ? 'fa-file-pdf' : 'fa-file-image') + ' att-ico"></i>' +
                '<span class="att-viewer-name" title="' + esc(filename) + '">' + esc(filename || 'Attachment') + '</span>' +
                '<button type="button" class="att-viewer-x" data-close aria-label="Close">&times;</button>' +
              '</div>' +
              '<div class="att-viewer-body">' +
                (isPdf
                    ? '<iframe src="' + url + '" title="' + esc(filename) + '"></iframe>'
                    : '<img src="' + url + '" alt="' + esc(filename) + '">') +
              '</div>' +
              '<div class="att-viewer-ft">' +
                '<a class="att-viewer-btn ghost" href="' + url + '?download=1"><i class="fas fa-download"></i> Download</a>' +
                '<button type="button" class="att-viewer-btn primary" data-close><i class="fas fa-xmark"></i> Close</button>' +
              '</div>' +
            '</div>';
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-close]')) close();
        });
        var img = ov.querySelector('img');
        if (img) img.addEventListener('error', function () {
            img.outerHTML = '<div class="att-viewer-msg">The file could not be opened. Try Download instead.</div>';
        });
        document.body.dataset.attPrevOverflow = document.body.style.overflow || '';
        document.body.style.overflow = 'hidden';
        document.body.appendChild(ov);
        document.addEventListener('keydown', onKey, true);
        ov.querySelector('.att-viewer-x').focus();
    };
})();
