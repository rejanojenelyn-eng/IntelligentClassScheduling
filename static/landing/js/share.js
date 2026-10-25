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
  // The link alone works (the server sends it as a download); fetching it first lets
  // the button show progress while the poster is generated.
  const dl = modal.querySelector('[data-share-download]');
  dl.addEventListener('click', async e => {
    if (!window.fetch || !window.URL || !URL.createObjectURL) return;
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
      a.download = dl.getAttribute('download');
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 4000);
      say('QR code poster downloaded.');
    } catch (_) {
      say('Could not prepare the QR code. Please try again.');
    } finally {
      dl.removeAttribute('aria-busy');
      label.textContent = 'Download QR Code';
    }
  });
})();
