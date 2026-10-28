/* photo.camera.js — "Take a Photo" for profile pictures (Academic Information panel).
   Same camera window as the account-setup Photo step: a live preview, then Capture.
   Where a live preview isn't available (older browser, or a page not served over HTTPS)
   it opens the device's own camera picker instead.

     const file = await takeCameraPhoto();   // a File (JPEG), or null when cancelled
   Loaded by the three base layouts, after photo.cropper.js. */
(function () {
    'use strict';

    function injectStyles() {
        if (document.getElementById('pcStyles')) return;
        const style = document.createElement('style');
        style.id = 'pcStyles';
        style.textContent = `
        .pc-overlay { position: fixed; inset: 0; z-index: 10090; background: rgba(30, 10, 10, 0.6);
            display: flex; align-items: center; justify-content: center; padding: 16px; font-family: 'Inter', sans-serif; }
        .pc-dialog { background: #fff; border-radius: 14px; width: 100%; max-width: 480px; padding: 20px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3); }
        .pc-title { color: #800000; font-weight: 800; font-size: 1.1rem; margin-bottom: 4px; }
        .pc-sub { color: #666; font-size: 0.82rem; margin-bottom: 14px; }
        .pc-view { position: relative; background: #111; border-radius: 10px; overflow: hidden; aspect-ratio: 4 / 3; }
        .pc-view video { width: 100%; height: 100%; object-fit: cover; transform: scaleX(-1); display: block; }
        .pc-msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
            text-align: center; padding: 20px; color: #fff; font-size: 0.86rem; line-height: 1.5; }
        .pc-msg[hidden] { display: none; }
        .pc-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
        .pc-actions button { padding: 10px 18px; border-radius: 8px; font-weight: 700; font-size: 0.84rem; cursor: pointer;
            font-family: inherit; }
        .pc-cancel { background: #fff; border: 1.5px solid #d0d0d0; color: #444; }
        .pc-shoot { background: #800000; border: 1.5px solid #800000; color: #fff; display: inline-flex; align-items: center; gap: 8px; }
        .pc-shoot:disabled { background: #d9a9a9; border-color: #d9a9a9; cursor: not-allowed; }`;
        document.head.appendChild(style);
    }

    // No live preview: the device's camera picker (a phone opens its camera app).
    function pickFromDeviceCamera() {
        return new Promise(resolve => {
            const input = document.createElement('input');
            input.type = 'file'; input.accept = 'image/*'; input.capture = 'user';
            input.style.display = 'none';
            input.addEventListener('change', () => { resolve(input.files && input.files[0] || null); input.remove(); });
            document.body.appendChild(input);
            input.click();
        });
    }

    function takeCameraPhoto() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.isSecureContext)
            return pickFromDeviceCamera();
        injectStyles();
        return new Promise(resolve => {
            const prevFocus = document.activeElement;
            const overlay = document.createElement('div');
            overlay.className = 'pc-overlay';
            overlay.innerHTML = `
                <div class="pc-dialog" role="dialog" aria-modal="true" aria-labelledby="pcTitle">
                    <div class="pc-title" id="pcTitle">Take a Photo</div>
                    <div class="pc-sub">Face the camera, then press Capture. You can adjust the photo next.</div>
                    <div class="pc-view">
                        <video autoplay playsinline muted></video>
                        <div class="pc-msg">Starting the camera…</div>
                    </div>
                    <div class="pc-actions">
                        <button type="button" class="pc-cancel">Cancel</button>
                        <button type="button" class="pc-shoot" disabled><i class="fas fa-camera"></i> Capture</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);
            const video = overlay.querySelector('video');
            const msg = overlay.querySelector('.pc-msg');
            const shoot = overlay.querySelector('.pc-shoot');
            let stream = null, finished = false;

            const finish = file => {
                if (finished) return;
                finished = true;
                if (stream) stream.getTracks().forEach(t => t.stop());
                overlay.remove();
                document.removeEventListener('keydown', onKey, true);
                if (prevFocus && prevFocus.focus) prevFocus.focus();
                resolve(file);
            };
            const onKey = e => { if (e.key === 'Escape') { e.preventDefault(); finish(null); } };
            document.addEventListener('keydown', onKey, true);
            overlay.querySelector('.pc-cancel').addEventListener('click', () => finish(null));
            overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
            shoot.addEventListener('click', () => {
                if (!video.videoWidth) return;
                const canvas = document.createElement('canvas');
                canvas.width = video.videoWidth; canvas.height = video.videoHeight;
                const ctx = canvas.getContext('2d');
                ctx.translate(canvas.width, 0); ctx.scale(-1, 1);      // same mirror view as the preview
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                canvas.toBlob(blob => finish(blob ? new File([blob], 'camera.jpg', { type: 'image/jpeg' }) : null),
                              'image/jpeg', 0.92);
            });
            overlay.querySelector('.pc-cancel').focus();

            navigator.mediaDevices.getUserMedia(
                { video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false })
                .then(s => {
                    stream = s;
                    if (finished) { s.getTracks().forEach(t => t.stop()); return; }   // closed while asking
                    video.srcObject = s;
                    return video.play().catch(() => {});
                })
                .then(() => {
                    if (finished || !stream) return;
                    msg.hidden = true;
                    shoot.disabled = false;
                    shoot.focus();
                })
                .catch(e => {
                    msg.textContent = e && e.name === 'NotAllowedError'
                        ? 'Camera access was blocked. Allow the camera in your browser settings, or choose a photo from your files instead.'
                        : e && e.name === 'NotFoundError'
                            ? 'No camera was found on this device. Please choose a photo from your files instead.'
                            : 'The camera could not be started. Please choose a photo from your files instead.';
                });
        });
    }

    window.takeCameraPhoto = takeCameraPhoto;
})();
