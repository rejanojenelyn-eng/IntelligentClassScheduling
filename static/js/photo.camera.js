/* ============================================================
   PROFILE PHOTO CAMERA
   Included in: base.html (Acad), base_faculty.html, base_admin.html
   openPhotoCamera() opens a live camera preview (the browser asks for camera
   permission only now) and resolves with a JPEG File of the captured picture,
   or null if the user cancels. Where a live preview isn't available (an old
   browser, or a page not served over HTTPS) it opens the device's own camera
   picker instead. Nothing is uploaded here -- callers crop and save.
   ============================================================ */


function openPhotoCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.isSecureContext) {
        return _cameraPickerFallback();
    }
    return new Promise(resolve => _showCamera(resolve));
}


/* The device's camera picker (on phones this opens the camera app). */
function _cameraPickerFallback() {
    return new Promise(resolve => {
        const input = document.createElement('input');
        input.type = 'file'; input.accept = 'image/*'; input.setAttribute('capture', 'user');
        input.style.display = 'none';
        input.addEventListener('change', () => {
            const f = input.files && input.files[0];
            input.remove();
            resolve(f || null);
        });
        document.body.appendChild(input);
        input.click();
    });
}


function _showCamera(resolve) {
    let stream = null, done = false;
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:10050;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px;';
    overlay.innerHTML = `
        <div role="dialog" aria-modal="true" aria-label="Take a photo"
             style="background:#fff;border-radius:14px;padding:20px;width:480px;max-width:100%;box-shadow:0 10px 30px rgba(0,0,0,.3);font-family:inherit;">
            <div style="font-weight:700;font-size:1.05rem;color:#222;">Take a Photo</div>
            <div style="font-size:.8rem;color:#777;margin:2px 0 14px;">Face the camera, then press Capture. You can adjust the photo next.</div>
            <div style="position:relative;background:#111;border-radius:10px;overflow:hidden;aspect-ratio:4/3;">
                <video class="pcam-video" autoplay playsinline muted
                       style="width:100%;height:100%;object-fit:cover;transform:scaleX(-1);display:block;"></video>
                <div class="pcam-msg" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px;color:#fff;font-size:.86rem;line-height:1.5;">Starting the camera…</div>
            </div>
            <div style="display:flex;gap:10px;margin-top:14px;">
                <button type="button" class="pcam-cancel" style="flex:1;padding:10px;border-radius:8px;border:1px solid #ccc;background:#fff;color:#333;font-weight:600;cursor:pointer;">Cancel</button>
                <button type="button" class="pcam-shoot" disabled style="flex:1;padding:10px;border-radius:8px;border:none;background:#800000;color:#fff;font-weight:700;cursor:pointer;opacity:.5;">Capture</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    const video = overlay.querySelector('.pcam-video');
    const msg   = overlay.querySelector('.pcam-msg');
    const shoot = overlay.querySelector('.pcam-shoot');

    function finish(file) {
        if (done) return;
        done = true;
        if (stream) stream.getTracks().forEach(t => t.stop());   // the camera turns off right away
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(file);
    }
    function onKey(e) { if (e.key === 'Escape') finish(null); }
    document.addEventListener('keydown', onKey);
    overlay.querySelector('.pcam-cancel').addEventListener('click', () => finish(null));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });
    shoot.addEventListener('click', () => {
        if (!video.videoWidth) return;
        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.translate(canvas.width, 0); ctx.scale(-1, 1);          // same mirror view as the preview
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(blob => finish(blob ? new File([blob], 'camera.jpg', { type: 'image/jpeg' }) : null),
                      'image/jpeg', 0.92);
    });

    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } },
                                          audio: false })
        .then(s => {
            stream = s;
            if (done) { s.getTracks().forEach(t => t.stop()); return; }   // closed while asking for permission
            video.srcObject = s;
            return video.play().catch(() => {});
        })
        .then(() => {
            if (done || !stream) return;
            msg.style.display = 'none';
            shoot.disabled = false; shoot.style.opacity = '';
            shoot.focus();
        })
        .catch(e => {
            msg.textContent = e && e.name === 'NotAllowedError'
                ? 'Camera access was blocked. Allow the camera in your browser settings, or choose a photo from your files instead.'
                : e && e.name === 'NotFoundError'
                    ? 'No camera was found on this device. Please choose a photo from your files instead.'
                    : 'The camera could not be started. Please choose a photo from your files instead.';
        });
}
