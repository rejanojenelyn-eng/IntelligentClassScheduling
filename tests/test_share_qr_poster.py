"""
Landing page Share popup: the QR code and the downloadable QR poster are built
from the public URL (PUBLIC_SITE_URL, else the request's own address), come back
as PNGs, and the QR code decodes to that URL even with the seal over its centre.
"""
import io

import pytest
from PIL import Image

import qr_poster


def _decode(png_bytes):
    cv2 = pytest.importorskip('cv2')
    import numpy as np
    img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    value, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
    return value


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from config import Config
    monkeypatch.setattr(Config, 'PUBLIC_SITE_URL', 'https://puplc.example.app')
    return app_module.app.test_client()


def test_poster_is_print_resolution_png_and_scans():
    url = 'https://puplc-scheduling-system.up.railway.app/'
    png = qr_poster.poster_png(url)
    img = Image.open(io.BytesIO(png))
    assert img.format == 'PNG'
    assert img.size == (2400, 3000)              # 8 x 10 in at 300 dpi, portrait
    assert round(img.info['dpi'][0]) == 300
    assert _decode(png) == url
    # Shrunk to a quarter (as a phone camera sees a small print) it still scans.
    small = io.BytesIO()
    img.convert('RGB').resize((600, 750), Image.LANCZOS).save(small, 'PNG')
    assert _decode(small.getvalue()) == url


def test_popup_qr_scans():
    url = 'https://puplc-scheduling-system.up.railway.app/'
    assert _decode(qr_poster.qr_png(url)) == url


def test_download_route_uses_public_url_and_filename(client):
    resp = client.get('/share/qr-poster.png')
    assert resp.status_code == 200
    assert resp.mimetype == 'image/png'
    assert 'attachment' in resp.headers['Content-Disposition']
    assert 'PUPLC_Scheduling_System_QR.png' in resp.headers['Content-Disposition']
    assert _decode(resp.data) == 'https://puplc.example.app/'


def test_falls_back_to_request_address(client, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'PUBLIC_SITE_URL', '')
    monkeypatch.setattr(Config, 'TRUST_PROXY', True)
    resp = client.get('/share/qr.png', base_url='http://scheduling.example.org',
                      headers={'X-Forwarded-Proto': 'https'})
    assert resp.status_code == 200
    assert _decode(resp.data) == 'https://scheduling.example.org/'


def test_landing_page_shows_share_popup(client):
    html = client.get('/').get_data(as_text=True)
    assert 'id="shareModal"' in html
    assert 'data-share-url="https://puplc.example.app/"' in html
    assert '/share/qr-poster.png' in html
