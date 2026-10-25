/* CSRF token for every state-changing request this page makes (see _csrf_protect in app.py).
   Loaded at the top of <head> on every HTML page, before the page's own scripts:
     - fetch() / XMLHttpRequest: POST/PUT/PATCH/DELETE to this site get an X-CSRFToken header
     - <form method="post"> to this site: a hidden csrf_token field, also for form.submit()
     - csrfPost(url): submit a POST to url (used by buttons that used to be GET links)
   Requests to other sites never receive the token. */
(function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    var token = meta && meta.getAttribute('content');
    if (!token) return;
    window.CSRF_TOKEN = token;

    var SAFE = /^(GET|HEAD|OPTIONS|TRACE)$/i;
    function sameOrigin(url) {
        try { return new URL(url, window.location.href).origin === window.location.origin; }
        catch (e) { return false; }
    }

    var nativeFetch = window.fetch;
    if (nativeFetch) {
        window.fetch = function (input, init) {
            var isRequest = typeof Request !== 'undefined' && input instanceof Request;
            var method = (init && init.method) || (isRequest ? input.method : 'GET');
            var url = isRequest ? input.url : String(input);
            if (!SAFE.test(method) && sameOrigin(url)) {
                var headers = new Headers((init && init.headers) || (isRequest ? input.headers : undefined));
                if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
                init = Object.assign({}, init, { headers: headers });
            }
            return nativeFetch.call(this, input, init);
        };
    }

    var nativeOpen = XMLHttpRequest.prototype.open;
    var nativeSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this._csrfNeeded = !SAFE.test(method) && sameOrigin(url);
        return nativeOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
        if (this._csrfNeeded) {
            try { this.setRequestHeader('X-CSRFToken', token); } catch (e) { /* not opened */ }
        }
        return nativeSend.apply(this, arguments);
    };

    // getAttribute, not form.method / form.action: an <input name="action"> shadows those.
    function addTokenField(form) {
        if (!form || form.tagName !== 'FORM') return;
        if (SAFE.test(form.getAttribute('method') || 'GET')) return;
        if (!sameOrigin(form.getAttribute('action') || window.location.href)) return;
        var field = form.querySelector('input[name="csrf_token"]');
        if (!field) {
            field = document.createElement('input');
            field.type = 'hidden';
            field.name = 'csrf_token';
            form.appendChild(field);
        }
        field.value = token;
    }
    document.addEventListener('submit', function (e) { addTokenField(e.target); }, true);
    var nativeSubmit = HTMLFormElement.prototype.submit;
    HTMLFormElement.prototype.submit = function () {
        addTokenField(this);
        return nativeSubmit.apply(this, arguments);
    };

    window.csrfPost = function (url) {
        var form = document.createElement('form');
        form.method = 'POST';
        form.action = url;
        form.style.display = 'none';
        document.body.appendChild(form);
        form.submit();
    };
})();
