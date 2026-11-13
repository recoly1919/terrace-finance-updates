"""Terrace Finance — signed, staged, owner-approved local updates (Mac).

A remote host only distributes a signed manifest and a signed-package hash.
No shell commands, local data files or executable installation instructions are
accepted from the remote manifest. Updates can only be applied by the local
launcher after the HTTP server is stopped.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('TERRACE_DATA_DIR', ROOT / 'data'))
UPDATE_DIR = DATA / 'updates'
UPDATE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = UPDATE_DIR / 'config.json'
STATE = UPDATE_DIR / 'state.json'
STAGED = UPDATE_DIR / 'staged.zip'
STAGED_INFO = UPDATE_DIR / 'staged.json'
COMMAND = UPDATE_DIR / 'command.json'
HEARTBEAT = UPDATE_DIR / 'supervisor.json'
VERSION_FILE = ROOT / 'version.json'
DEV_MODE = os.environ.get('TERRACE_UPDATES_TEST_MODE') == '1'
MAX_MANIFEST = 16 * 1024
MAX_DOWNLOAD = 35 * 1024 * 1024
MAX_UNCOMPRESSED = 100 * 1024 * 1024
MAX_FILES = 150
APP_ID = 'terrace-finance'


def version() -> str:
    try:
        return str(json.loads(VERSION_FILE.read_text('utf-8'))['version'])
    except Exception:
        return '2.3.0'


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, json.JSONDecodeError):
        return fallback if fallback is not None else {}


def write_state(status: str, detail: str = '', **kwargs):
    save_json(STATE, {'status': status, 'detail': detail,
                      'time': datetime.now(timezone.utc).isoformat(), **kwargs})


def parse_semver(s):
    if not isinstance(s, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', s):
        raise ValueError('Sürüm numarası geçersiz (örn. 2.4.0)')
    return tuple(int(t) for t in s.split('.'))


def _local_http_allowed(parts):
    return DEV_MODE and parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')


def check_url(url: str, *, manifest_origin: str | None = None) -> str:
    if not isinstance(url, str) or len(url) > 1500:
        raise ValueError('Güncelleme adresi geçersiz')
    u = urllib.parse.urlsplit(url)
    if u.username or u.password or u.fragment or u.query:
        raise ValueError('Güncelleme URL adresinde kimlik, sorgu veya # kullanılamaz')
    if not u.hostname or (u.scheme != 'https' and not _local_http_allowed(u)):
        raise ValueError('HTTPS adresi gereklidir')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', u.hostname):
        raise ValueError('Geçersiz yayın sunucusu')
    try:
        port = u.port
    except ValueError:
        raise ValueError('Port geçersiz') from None
    if not _local_http_allowed(u) and port not in (None, 443):
        raise ValueError('Yalnızca HTTPS 443 portu destekleniyor')
    if manifest_origin is not None:
        origin = urllib.parse.urlsplit(manifest_origin)
        if (u.scheme, u.hostname, u.port) != (origin.scheme, origin.hostname, origin.port):
            raise ValueError('Paket manifest ile aynı HTTPS kaynağında olmalı')
    if not _local_http_allowed(u):
        try:
            addr = ipaddress.ip_address(u.hostname)
        except ValueError:
            addr = None
        if addr and not addr.is_global:
            raise ValueError('Özel / yerel ağ adresine erişim engellendi')
        if u.hostname in ('localhost',) or u.hostname.endswith('.local'):
            raise ValueError('Yerel ağ sunucusu kullanılamaz')
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Güncelleme sunucusu yönlendirmeye izin vermez')


_OPENER = urllib.request.build_opener(_NoRedirect())


def fetch(url: str, max_bytes: int):
    check_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'TerraceFinanceUpdater/2.3', 'Accept': 'application/octet-stream, application/json'})
    try:
        with _OPENER.open(request, timeout=15) as response:
            if response.status != 200:
                raise ValueError('Sunucu HTTP 200 döndürmedi')
            length = response.headers.get('Content-Length')
            if length and int(length) > max_bytes:
                raise ValueError('Güncelleme paketi izin verilen boyuttan büyük')
            buf = bytearray()
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - len(buf)))
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise ValueError('Güncelleme boyutu sınırı aşıldı')
            return bytes(buf)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise ValueError('Güncelleme sunucusuna bağlanılamadı: ' + str(exc)) from exc


def canonical_manifest(manifest):
    return json.dumps({k: v for k, v in manifest.items() if k != 'signature'}, sort_keys=True,
                      separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def verify_signature(manifest: dict, public_key_b64: str):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        public = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(manifest['signature'], validate=True)
        if len(public) != 32 or len(signature) != 64:
            raise ValueError('Geçersiz Ed25519 anahtar veya imza uzunluğu')
        Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical_manifest(manifest))
    except ImportError:
        raise ValueError('İmza doğrulama kütüphanesi eksik. cryptography kurulumu gerekli') from None
    except Exception as exc:
        raise ValueError('Dijital imza doğrulanamadı: ' + str(exc)) from exc


def verify_manifest(manifest: dict, config: dict):
    if not isinstance(manifest, dict) or manifest.get('schema') != 1 or manifest.get('app') != APP_ID:
        raise ValueError('Manifest formatı / uygulama kimliği geçersiz')
    if manifest.get('platform') != 'mac' or manifest.get('channel') != 'stable':
        raise ValueError('Yanlış platform veya güncelleme kanalı')
    remote = parse_semver(manifest.get('version'))
    parse_semver(manifest.get('min_version', '2.3.0'))
    if not isinstance(manifest.get('sha256'), str) or not re.fullmatch(r'[a-fA-F0-9]{64}', manifest['sha256']):
        raise ValueError('SHA-256 özeti geçersiz')
    size = manifest.get('size')
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError('Paket boyutu geçersiz')
    check_url(manifest.get('url', ''), manifest_origin=config['feed_url'])
    notes = manifest.get('notes', {})
    if not isinstance(notes, dict) or any(not isinstance(v, str) or len(v) > 2000 for v in notes.values()):
        raise ValueError('Sürüm notları geçersiz')
    verify_signature(manifest, config['public_key'])
    if parse_semver(version()) < parse_semver(manifest.get('min_version', '2.3.0')):
        raise ValueError('Bu güncelleme için daha yeni bir başlangıç sürümü gerekiyor')
    return remote


def load_config():
    config = read_json(CONFIG, {})
    if config.get('feed_url') and config.get('public_key'):
        check_url(config['feed_url'])
        try:
            if len(base64.b64decode(config['public_key'], validate=True)) != 32:
                raise ValueError()
        except Exception:
            raise ValueError('Yayıncının açık anahtarı geçersiz') from None
        return config
    return None


def set_config(url, pub):
    check_url(url)
    try:
        if len(base64.b64decode(pub, validate=True)) != 32:
            raise ValueError()
    except Exception:
        raise ValueError('Ed25519 açık anahtar 32 bayt base64 olmalı') from None
    save_json(CONFIG, {'feed_url': url, 'public_key': pub, 'channel': 'stable'})
    STAGED.unlink(missing_ok=True)
    STAGED_INFO.unlink(missing_ok=True)
    return {'configured': True}


def get_manifest():
    config = load_config()
    if not config:
        raise ValueError('Önce güncelleme sunucusunu ve yayıncı anahtarını tanımlayın')
    raw = fetch(config['feed_url'], MAX_MANIFEST)
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError('Manifest geçerli JSON değil') from None
    verify_manifest(manifest, config)
    return manifest


def check_release():
    manifest = get_manifest()
    return {'version': manifest['version'], 'available': parse_semver(manifest['version']) > parse_semver(version()),
            'notes': manifest.get('notes', {}), 'size': manifest['size']}


_REQUIRED = {'app.py', 'launcher.py', 'update_service.py', 'start.sh',
             'requirements.txt', 'version.json', 'static/index.html', 'static/v2.css', 'static/updater.css', 'static/updater.js'}
_ALLOWED = _REQUIRED | {'start.bat', 'README.md'}


def inspect_zip(path: Path, expected_version: str):
    try:
        with zipfile.ZipFile(path, 'r') as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > MAX_FILES:
                raise ValueError('Arşivde çok fazla dosya var')
            names = {i.filename for i in infos}
            if len(names) != len(infos):
                raise ValueError('Arşivde tekrarlanan dosya isimleri var')
            if not _REQUIRED.issubset(names):
                raise ValueError('Arşiv gerekli uygulama dosyalarını içermiyor')
            expanded = 0
            for info in infos:
                name = info.filename
                parts = Path(name).parts
                if (name.startswith('/') or '..' in parts or '\\' in name or name.startswith('.')):
                    raise ValueError('Arşivde tehlikeli yol')
                if name not in _ALLOWED and not (name.startswith('static/') and len(parts) == 2):
                    raise ValueError('İzin verilmeyen paket içeriği: ' + name)
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('Sembolik bağlantı içeren paket yasak')
                expanded += info.file_size
                if expanded > MAX_UNCOMPRESSED:
                    raise ValueError('Arşiv açıldığında çok büyük')
                if info.compress_size and info.file_size / info.compress_size > 300:
                    raise ValueError('Aşırı sıkıştırılmış dosya engellendi')
            try:
                vf = json.loads(z.read('version.json'))
            except Exception:
                raise ValueError('Paketin version.json dosyası geçersiz') from None
            if vf.get('app') != APP_ID or vf.get('version') != expected_version:
                raise ValueError('Paket sürümü manifest ile eşleşmiyor')
            return names
    except zipfile.BadZipFile:
        raise ValueError('Güncelleme arşivi bozuk') from None


def stage_release():
    if STATE.exists() and read_json(STATE).get('status') in ('installing', 'rolling_back'):
        raise ValueError('Başka bir güncelleme devam ediyor')
    manifest = get_manifest()
    if parse_semver(manifest['version']) <= parse_semver(version()):
        raise ValueError('Daha yeni bir sürüm yayımlanmamış')
    blob = fetch(manifest['url'], MAX_DOWNLOAD)
    if len(blob) != manifest['size'] or hashlib.sha256(blob).hexdigest().lower() != manifest['sha256'].lower():
        raise ValueError('Paket boyutu / SHA-256 doğrulaması başarısız')
    tmp = STAGED.with_suffix('.part')
    tmp.write_bytes(blob)
    try:
        inspect_zip(tmp, manifest['version'])
        os.replace(tmp, STAGED)
        save_json(STAGED_INFO, {'manifest': manifest, 'sha256': manifest['sha256']})
        write_state('staged', 'İndirildi, imza ve bütünlük doğrulandı', version=manifest['version'])
    finally:
        tmp.unlink(missing_ok=True)
    return {'ready': True, 'version': manifest['version']}


def verify_staged():
    meta = read_json(STAGED_INFO)
    manifest = meta.get('manifest', {})
    config = load_config()
    if not config or not STAGED.exists():
        raise ValueError('Önce güncelleme paketini indirin')
    verify_manifest(manifest, config)
    if parse_semver(manifest['version']) <= parse_semver(version()):
        raise ValueError('Bu paket mevcut sürümden daha yeni değil')
    with open(STAGED, 'rb') as candidate:
        digest = hashlib.file_digest(candidate, 'sha256').hexdigest().lower()
    if STAGED.stat().st_size != manifest['size'] or digest != manifest['sha256'].lower():
        raise ValueError('İndirilen paket değişmiş; yeniden indirin')
    inspect_zip(STAGED, manifest['version'])
    return manifest


def has_supervisor():
    heartbeat = read_json(HEARTBEAT, {})
    try:
        from time import time
        if time() - float(heartbeat.get('time', 0)) >= 7:
            return False
        pid = heartbeat.get('pid')
        if not isinstance(pid, int) or pid <= 1:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def request_install():
    if not has_supervisor():
        raise ValueError('Güncelleme yöneticisi çalışmıyor. Uygulamayı start.sh ile başlatın')
    manifest = verify_staged()
    if COMMAND.exists():
        raise ValueError('Bir güncelleme işlemi zaten sırada')
    write_state('queued', 'Güncelleme sırası oluşturuldu', version=manifest['version'])
    save_json(COMMAND, {'action': 'install', 'version': manifest['version']})
    return {'queued': True, 'version': manifest['version']}


def backups():
    folder = UPDATE_DIR / 'backups'
    if not folder.exists():
        return []
    result = []
    for sub in folder.iterdir():
        if sub.is_dir() and re.fullmatch(r'backup-[0-9]{8}-[0-9]{6}(-[0-9]+)?', sub.name):
            m = read_json(sub / 'meta.json')
            if m.get('version'):
                result.append({'id': sub.name, 'version': m['version'], 'created_at': m.get('created_at')})
    return sorted(result, key=lambda x: x['id'], reverse=True)


def request_rollback(backup_id):
    if not has_supervisor():
        raise ValueError('Güncelleme yöneticisi çalışmıyor')
    if backup_id not in [b['id'] for b in backups()]:
        raise ValueError('Geri dönülecek yedek bulunamadı')
    if COMMAND.exists():
        raise ValueError('Başka bir işlem sürüyor')
    write_state('queued', 'Geri alma sırası oluşturuldu', backup_id=backup_id)
    save_json(COMMAND, {'action': 'rollback', 'backup_id': backup_id})
    return {'queued': True, 'backup_id': backup_id}


def status():
    conf = read_json(CONFIG, {})
    return {'version': version(), 'configured': bool(conf.get('feed_url') and conf.get('public_key')),
            'feed_url': conf.get('feed_url', ''), 'state': read_json(STATE, {'status': 'idle'}),
            'supervisor': has_supervisor(), 'backups': backups()[:20]}
