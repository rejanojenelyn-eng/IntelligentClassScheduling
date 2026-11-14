// Share popup: open/close, copy link, share links and the QR poster download.
// The URL comes from the server (PUBLIC_SITE_URL, else the address the page was opened on).
(() => {
  const modal = document.getElementById('shareModal');
  if (!modal) return;
  const url = modal.dataset.shareUrl;
  const title = 'PUP Lopez Academic Scheduling System';
  const status = modal.querySelector('[data-share-status]');
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  let statusTimer;

  const say = (msg) => {
    status.textContent = msg;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { status.textContent = ''; }, 3500);
  };

  const open = () => {
    if (typeof modal.showModal === 'function') modal.showModal();
    else modal.setAttribute('open', '');
  };
  const close = () => {
    if (typeof modal.close === 'function') modal.close();
    else modal.removeAttribute('open');
  };

  document.querySelectorAll('[data-share-open]').forEach(b => b.addEventListener('click', open));
  modal.querySelector('[data-share-close]').addEventListener('click', close);
  // Click on the dimmed backdrop (outside the popup box) closes it.
  modal.addEventListener('click', e => {
    if (e.target !== modal) return;
    const r = modal.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
  });

  // ----- Copy link -----
  const input = modal.querySelector('[data-share-input]');
  const copyBtn = modal.querySelector('[data-share-copy]');
  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(url);
      return true;
    } catch (_) {
      input.select();
      try { return document.execCommand('copy'); } catch (__) { return false; }
    }
  };
  copyBtn.addEventListener('click', async () => {
    const label = copyBtn.querySelector('span');
    if (await copyText()) {
      label.textContent = 'Copied!';
      say('Link copied to clipboard.');
      setTimeout(() => { label.textContent = 'Copy'; }, 2000);
    } else {
      input.select();
      say('Press Ctrl+C to copy the selected link.');
    }
  });

  // ----- Share via -----
  const enc = encodeURIComponent;
  const targets = {
    facebook: () => `https://www.facebook.com/sharer/sharer.php?u=${enc(url)}`,
    x: () => `https://twitter.com/intent/tweet?url=${enc(url)}&text=${enc(title)}`,
    whatsapp: () => `https://wa.me/?text=${enc(title + ' ' + url)}`,
    email: () => `mailto:?subject=${enc(title)}&body=${enc(title + '\n' + url)}`,
    // Messenger has no public web share link without a Facebook app ID: phones open
    // the Messenger app; on a computer the link is copied and messenger.com opened.
    messenger: () => isMobile ? `fb-messenger://share/?link=${enc(url)}` : 'https://www.messenger.com/',
  };
  modal.querySelectorAll('[data-share-to]').forEach(a => {
    const make = targets[a.dataset.shareTo];
    a.href = make();
    if (a.dataset.shareTo === 'messenger' && !isMobile) {
      a.addEventListener('click', () => {
        copyText().then(ok => { if (ok) say('Link copied — paste it into a Messenger chat.'); });
      });
    }
  });

  // ----- Download QR Code -----
  // Computers and Android Chrome: a normal file download. iPhone and in-app browsers
  // (Messenger, Facebook, Instagram…) can't download a file from a page, so there the
  // phone's share sheet opens with the poster (→ "Save Image"), or — where that isn't
  // supported — the poster opens as an image to long-press and save.
  const dl = modal.querySelector('[data-share-download]');
  const fileName = dl.getAttribute('download') || 'PUPLC_Scheduling_System_QR.png';
  const ua = navigator.userAgent;
  const isIOS = /iPhone|iPad|iPod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const inApp = /FBAN|FBAV|FB_IAB|Messenger|Instagram|Line\/|Twitter|TikTok|Snapchat|; wv\)/i.test(ua);
  const viaShareSheet = isIOS || inApp;
  const inlineUrl = dl.href + (dl.href.includes('?') ? '&' : '?') + 'inline=1';

  // Fetched ahead (when the popup opens) so the tap can open the share sheet at once:
  // phones only allow it straight from the tap, not after waiting for a download.
  let posterFile = null, posterLoading = null;
  const loadPoster = () => {
    if (posterFile) return Promise.resolve(posterFile);
    if (!posterLoading) {
      posterLoading = fetch(dl.href)
        .then(r => { if (!r.ok) throw new Error(r.status); return r.blob(); })
        .then(b => (posterFile = new File([b], fileName, { type: 'image/png' })))
        .catch(err => { posterLoading = null; throw err; });
    }
    return posterLoading;
  };
  if (viaShareSheet && window.fetch && window.File) {
    document.querySelectorAll('[data-share-open]').forEach(b =>
      b.addEventListener('click', () => { loadPoster().catch(() => {}); }));
  }

  const canShareFile = f => {
    try { return !!(f && navigator.canShare && navigator.share && navigator.canShare({ files: [f] })); }
    catch (_) { return false; }
  };
  const openImage = () => {
    say('Long-press the QR code image, then choose "Save Image" or "Download image".');
    setTimeout(() => { window.location.href = inlineUrl; }, 600);
  };

  dl.addEventListener('click', async e => {
    if (viaShareSheet) {
      e.preventDefault();
      if (canShareFile(posterFile)) {
        try {
          await navigator.share({ files: [posterFile], title });
          say('Choose "Save Image" to keep the QR code poster.');
        } catch (err) {
          if (err && err.name === 'AbortError') return;      // the user closed the sheet
          openImage();
        }
        return;
      }
      openImage();
      return;
    }
    if (!window.fetch || !window.URL || !URL.createObjectURL) return;   // the link downloads it
    e.preventDefault();
    const label = dl.querySelector('span');
    dl.setAttribute('aria-busy', 'true');
    label.textContent = 'Preparing…';
    try {
      const res = await fetch(dl.href);
      if (!res.ok) throw new Error(res.status);
      const blobUrl = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 4000);
      say('QR code poster downloaded.');
    } catch (_) {
      // Let the browser download it from the server directly.
      window.location.href = dl.href;
    } finally {
      dl.removeAttribute('aria-busy');
      label.textContent = 'Download QR Code';
    }
  });
})();
