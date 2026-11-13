"""Mac loopback supervisor that stops the app for signed updates and restarts it.

The web server NEVER overwrites its own Python files. The separate supervisor
performs installation while the server is stopped, with a recoverable backup.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import update_service as upd

ROOT = upd.ROOT
DATA = upd.DATA
BACKUPS = upd.UPDATE_DIR / 'backups'
APP_FILES = ('app.py', 'launcher.py', 'update_service.py', 'start.sh',
             'start.bat', 'requirements.txt', 'version.json', 'static')
PORT = int(os.environ.get('TERRACE_PORT', '8000'))
CHILD = None
STOP = False


def log(*parts):
    print('[Terrace Update]', *parts, flush=True)


def on_signal(sig, frame):
    global STOP
    STOP = True


def start_server():
    env = dict(os.environ)
    env['TERRACE_SUPERVISOR'] = '1'
    return subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:APP',
                             '--host', '127.0.0.1', '--port', str(PORT)],
                            cwd=ROOT, env=env)


def stop_server(child):
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def check_health(child, expected_version, seconds=24):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('Güncellenen sunucu açılışta kapandı')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/health', timeout=2) as r:
                result = json.load(r)
            if result.get('ok') and result.get('version') == expected_version:
                return
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError('Yeni sürüm sağlık kontrolünü geçemedi')


def copy_safe_tree(src, dst):
    if src.exists():
        shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=True)


def make_backup():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    name = 'backup-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    d = BACKUPS / name
    suffix = 0
    while d.exists():
        suffix += 1
        d = BACKUPS / f'{name}-{suffix}'
    (d / 'code').mkdir(parents=True)
    (d / 'data').mkdir(parents=True)
    for name in APP_FILES:
        src = ROOT / name
        dst = d / 'code' / name
        if src.is_dir():
            copy_safe_tree(src, dst)
        elif src.is_file():
            shutil.copy2(src, dst)
    live = DATA / 'terrace.db'
    if live.exists():
        with sqlite3.connect(str(live), timeout=15) as c:
            with sqlite3.connect(str(d / 'data' / 'terrace.db')) as backup:
                c.backup(backup)
        with sqlite3.connect(str(d / 'data' / 'terrace.db')) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite yedeği bütünlük kontrolünden geçemedi')
    copy_safe_tree(DATA / 'uploads', d / 'data' / 'uploads')
    upd.save_json(d / 'meta.json', {'created_at': datetime.now(timezone.utc).isoformat(),
                                   'version': upd.version()})
    return d


def install_archive(path, target_version):
    upd.inspect_zip(path, target_version)
    # No remote-controlled paths are permitted, all files are from the signed archive.
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            dst = ROOT / info.filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(dir=dst.parent, prefix='.terrace-stage-')
            try:
                with os.fdopen(fd, 'wb') as out, z.open(info) as src:
                    shutil.copyfileobj(src, out)
                os.chmod(temp_name, 0o644)
                os.replace(temp_name, dst)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)


def restore_code(backup):
    previous = backup / 'code'
    if not (previous / 'app.py').is_file() or not (previous / 'version.json').is_file():
        raise RuntimeError('Geri dönüş dosyaları eksik')
    # Restore only application files, NEVER delete the data directory here.
    for name in APP_FILES:
        old = previous / name
        dest = ROOT / name
        if old.is_dir():
            if dest.is_dir():
                shutil.rmtree(dest)
            shutil.copytree(old, dest)
        elif old.is_file():
            fd, tmp = tempfile.mkstemp(dir=ROOT, prefix='.terrace-restore-')
            try:
                with os.fdopen(fd, 'wb') as f, open(old, 'rb') as src:
                    shutil.copyfileobj(src, f)
                os.replace(tmp, dest)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        elif dest.is_file():
            dest.unlink()


def restore_data(backup):
    """Only after automatic failed installation, never for a user-requested rollback."""
    db = backup / 'data' / 'terrace.db'
    if db.exists():
        for name in ('terrace.db-wal', 'terrace.db-shm'):
            (DATA / name).unlink(missing_ok=True)
        shutil.copy2(db, DATA / 'terrace.db')
    uploads = backup / 'data' / 'uploads'
    target = DATA / 'uploads'
    if uploads.exists():
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(uploads, target)


def install_requirements():
    # Signed package, local requirements only; a failed dependency installation is treated
    # as an update failure. Older dependencies may remain in the Python environment.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-r', str(ROOT / 'requirements.txt')], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, timeout=150)


def execute(command, child):
    action = command.get('action')
    if action not in ('install', 'rollback'):
        return child
    if action == 'install':
        manifest = upd.verify_staged()
        target_version = manifest['version']
    else:
        backup_id = command.get('backup_id')
        match = [b for b in upd.backups() if b['id'] == backup_id]
        if not match:
            raise RuntimeError('Geri alınacak geçerli yedek bulunamadı')
        dest_backup = BACKUPS / backup_id
        target_version = match[0]['version']
    log('Durduruluyor; işlem:', action, '→', target_version)
    upd.write_state('installing' if action == 'install' else 'rolling_back',
                    'Uygulama durduruluyor, yedek alınıyor', version=target_version)
    stop_server(child)
    try:
        prior = make_backup()
    except Exception as exc:
        # Nothing has been replaced yet; restart the original application.
        restarted = start_server()
        check_health(restarted, upd.version())
        upd.write_state('failed_rolled_back', 'Yedek alınamadı: ' + str(exc), version=upd.version())
        return restarted
    upd.write_state('installing' if action == 'install' else 'rolling_back',
                    'Yedek alındı; uygulama dosyaları değiştiriliyor',
                    version=target_version, backup_id=prior.name)
    new_child = None
    try:
        if action == 'install':
            install_archive(upd.STAGED, target_version)
        else:
            restore_code(dest_backup)
        install_requirements()
        new_child = start_server()
        check_health(new_child, target_version)
        upd.STAGED.unlink(missing_ok=True)
        upd.STAGED_INFO.unlink(missing_ok=True)
        upd.save_json(upd.HEARTBEAT, {'time': time.time(), 'pid': os.getpid(),
                                      'version': upd.version()})
        upd.write_state('installed' if action == 'install' else 'rolled_back',
                        'Uygulama sağlıklı şekilde yeniden başlatıldı',
                        version=target_version, backup_id=prior.name)
        log('İşlem başarılı:', target_version)
        return new_child
    except Exception as exc:
        log('HATA:', exc, '— otomatik geri dönüş')
        stop_server(new_child)
        try:
            restore_code(prior)
            # Restore pre-upgrade SQLite and uploaded documents ONLY on failed update.
            restore_data(prior)
            install_requirements()
            old_child = start_server()
            check_health(old_child, upd.version())
            upd.save_json(upd.HEARTBEAT, {'time': time.time(), 'pid': os.getpid(),
                                          'version': upd.version()})
            upd.write_state('failed_rolled_back', str(exc),
                            version=upd.version(), backup_id=prior.name)
            return old_child
        except Exception as restore_exc:
            upd.write_state('critical', f'{exc}; geri dönüş de başarısız: {restore_exc}',
                            backup_id=prior.name)
            raise


def recover_interrupted():
    """On power loss during install, restore known-good code and data snapshot."""
    state = upd.read_json(upd.STATE, {})
    if state.get('status') not in ('installing', 'rolling_back'):
        return
    bid = state.get('backup_id')
    backup = BACKUPS / bid if isinstance(bid, str) and bid.startswith('backup-') else None
    if not backup or not (backup / 'meta.json').exists():
        # No application file changes occur before a complete backup exists.
        upd.write_state('failed_rolled_back', 'Güncelleme yedek aşamasında kesildi; eski kod korundu', version=upd.version())
        return
    restore_code(backup)
    restore_data(backup)
    upd.write_state('failed_rolled_back', 'Kesintiden sonra güvenli yedek geri yüklendi',
                    version=upd.version(), backup_id=backup.name)


def main():
    global STOP
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    recover_interrupted()
    child = start_server()
    log('Mac uygulaması başlatıldı. http://127.0.0.1:8000')
    try:
        while not STOP:
            upd.save_json(upd.HEARTBEAT, {'time': time.time(), 'pid': os.getpid(),
                                          'version': upd.version()})
            if upd.COMMAND.exists():
                command = upd.read_json(upd.COMMAND)
                upd.COMMAND.unlink(missing_ok=True)
                time.sleep(1.0)  # Let the HTTP request return before taking the web process down.
                try:
                    child = execute(command, child)
                except Exception as exc:
                    log('Kritik güncelleme hatası:', exc)
                    STOP = True
                    break
            if child.poll() is not None:
                log('Web sunucusu kapandı, çıkış kodu:', child.returncode)
                break
            time.sleep(.4)
    finally:
        stop_server(child)
        upd.HEARTBEAT.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
