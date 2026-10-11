/* ============================================================
   PROFILE PHOTO CROPPER
   Included in: base.html (Acad), base_faculty.html, base_admin.html,
                setup/setup_photo.html
   openPhotoCropper(file) shows a modal where the user drags / zooms the
   picture inside the same circle used by every avatar in the system, and
   resolves with a square JPEG Blob of exactly what was inside the circle
   (or null if they cancel). Nothing is uploaded here — callers do that.
   ============================================================ */


const _CROP_OUTPUT_PX = 512;   // saved image is 512×512; avatars are shown far smaller


function openPhotoCropper(file) {
    return new Promise(resolve => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onerror = () => { URL.revokeObjectURL(url); alert('That file could not be read as an image.'); resolve(null); };
        img.onload  = () => _showCropper(img, url, resolve);
        img.src = url;
    });
}


function _showCropper(img, url, resolve) {
    const stagePx = Math.min(300, window.innerWidth - 72);
    const ring    = Math.round(stagePx * 0.06);        // dimmed margin around the circle
    const circle  = stagePx - ring * 2;

    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:10050;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px;';
    overlay.innerHTML = `
        <div role="dialog" aria-modal="true" aria-label="Crop profile photo"
             style="background:#fff;border-radius:14px;padding:20px;width:${stagePx + 40}px;max-width:100%;box-shadow:0 10px 30px rgba(0,0,0,.3);font-family:inherit;">
            <div style="font-weight:700;font-size:1.05rem;color:#222;">Crop Photo</div>
            <div style="font-size:.8rem;color:#777;margin:2px 0 14px;">Drag to reposition. Use the slider to zoom.</div>
            <div class="pc-stage" style="position:relative;width:${stagePx}px;height:${stagePx}px;max-width:100%;margin:0 auto;overflow:hidden;border-radius:10px;background:#222;touch-action:none;cursor:grab;user-select:none;">
                <img class="pc-img" alt="" draggable="false" style="position:absolute;left:0;top:0;transform-origin:0 0;max-width:none;pointer-events:none;">
                <div style="position:absolute;left:${ring}px;top:${ring}px;width:${circle}px;height:${circle}px;border-radius:50%;box-shadow:0 0 0 9999px rgba(0,0,0,.55);border:2px solid rgba(255,255,255,.85);pointer-events:none;"></div>
            </div>
            <div style="display:flex;align-items:center;gap:10px;margin:16px 2px 4px;">
                <span style="font-size:.8rem;color:#777;">−</span>
                <input class="pc-zoom" type="range" min="0" max="1" step="0.001" value="0" style="flex:1;accent-color:#800000;" aria-label="Zoom">
                <span style="font-size:.95rem;color:#777;">+</span>
            </div>
            <div style="display:flex;gap:10px;margin-top:14px;">
                <button type="button" class="pc-cancel" style="flex:1;padding:10px;border-radius:8px;border:1px solid #ccc;background:#fff;color:#333;font-weight:600;cursor:pointer;">Cancel</button>
                <button type="button" class="pc-apply"  style="flex:1;padding:10px;border-radius:8px;border:none;background:#800000;color:#fff;font-weight:700;cursor:pointer;">Use Photo</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    const stage = overlay.querySelector('.pc-stage');
    const view  = overlay.querySelector('.pc-img');
    const zoom  = overlay.querySelector('.pc-zoom');
    view.src = url;

    // Scale at which the image just covers the circle; the user can zoom to 4× that.
    const minScale = circle / Math.min(img.naturalWidth, img.naturalHeight);
    const maxScale = minScale * 4;
    let scale = minScale;
    let x = ring + (circle - img.naturalWidth  * scale) / 2;
    let y = ring + (circle - img.naturalHeight * scale) / 2;

    // Keep the circle fully covered by the image at all times.
    function clamp() {
        const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
        x = Math.min(ring, Math.max(ring + circle - w, x));
        y = Math.min(ring, Math.max(ring + circle - h, y));
    }
    function render() {
        clamp();
        view.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    }
    // Zoom around a point of the stage so what's under it stays put.
    function setScale(next, cx = stagePx / 2, cy = stagePx / 2) {
        next = Math.min(maxScale, Math.max(minScale, next));
        x = cx - (cx - x) * (next / scale);
        y = cy - (cy - y) * (next / scale);
        scale = next;
        zoom.value = (scale - minScale) / (maxScale - minScale);
        render();
    }
    render();

    zoom.addEventListener('input', () => setScale(minScale + Number(zoom.value) * (maxScale - minScale)));
    stage.addEventListener('wheel', e => {
        e.preventDefault();
        const r = stage.getBoundingClientRect();
        setScale(scale * (e.deltaY < 0 ? 1.08 : 1 / 1.08), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    // Drag with one pointer, pinch-zoom with two (touch screens).
    const pointers = new Map();
    let pinchDist = 0;
    stage.addEventListener('pointerdown', e => {
        stage.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        stage.style.cursor = 'grabbing';
    });
    stage.addEventListener('pointermove', e => {
        const prev = pointers.get(e.pointerId);
        if (!prev) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 1) {
            x += e.clientX - prev.x; y += e.clientY - prev.y;
            render();
        } else if (pointers.size === 2) {
            const [a, b] = [...pointers.values()];
            const dist = Math.hypot(a.x - b.x, a.y - b.y);
            const r = stage.getBoundingClientRect();
            if (pinchDist) setScale(scale * dist / pinchDist, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
            pinchDist = dist;
        }
    });
    const release = e => {
        pointers.delete(e.pointerId);
        pinchDist = 0;
        if (!pointers.size) stage.style.cursor = 'grab';
    };
    stage.addEventListener('pointerup', release);
    stage.addEventListener('pointercancel', release);

    function finish(result) {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        URL.revokeObjectURL(url);
        resolve(result);
    }
    function onKey(e) { if (e.key === 'Escape') finish(null); }
    document.addEventListener('keydown', onKey);
    overlay.querySelector('.pc-cancel').addEventListener('click', () => finish(null));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(null); });

    overlay.querySelector('.pc-apply').addEventListener('click', () => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = _CROP_OUTPUT_PX;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#fff';   // PNG transparency → white, since the result is a JPEG
        ctx.fillRect(0, 0, _CROP_OUTPUT_PX, _CROP_OUTPUT_PX);
        ctx.imageSmoothingQuality = 'high';
        // The circle's square, mapped back into the image's own pixels.
        const sx = (ring - x) / scale, sy = (ring - y) / scale, side = circle / scale;
        ctx.drawImage(img, sx, sy, side, side, 0, 0, _CROP_OUTPUT_PX, _CROP_OUTPUT_PX);
        canvas.toBlob(blob => finish(blob), 'image/jpeg', 0.9);
    });
}
