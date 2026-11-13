#!/usr/bin/env bash
# One-time migration from Terrace Finance V2.2 to signed-updates V2.3.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-$HOME/Desktop/terrace-finance}"
for f in app.py launcher.py update_service.py static/index.html static/v2.css static/updater.css static/updater.js version.json; do
  if [ ! -f "$SOURCE/$f" ]; then echo "HATA: Yeni pakette eksik dosya: $f"; exit 1; fi
done
if [ ! -f "$TARGET/app.py" ] || [ ! -f "$TARGET/static/index.html" ]; then
  echo "HATA: Çalışan Terrace Finance klasörü bulunamadı: $TARGET"
  echo "Örnek: bash upgrade_mac.sh /tam/uygulama/yolu"
  exit 1
fi
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "HATA: Önce uygulamanın Terminal penceresinde Control+C ile uygulamayı durdurun."; exit 1
fi
python3 -m py_compile "$SOURCE/app.py" "$SOURCE/launcher.py" "$SOURCE/update_service.py"
python3 -m pip install --disable-pip-version-check -r "$SOURCE/requirements.txt"
BACKUP="$TARGET/pre-v23-backup-$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p "$BACKUP/static" "$BACKUP/data"
for f in app.py launcher.py update_service.py start.sh start.bat requirements.txt version.json; do
  [ ! -f "$TARGET/$f" ] || cp "$TARGET/$f" "$BACKUP/$f"
done
cp "$TARGET/static/"* "$BACKUP/static/" 2>/dev/null || true
# SQLite online backup even if a WAL file is present (the application is stopped).
if [ -f "$TARGET/data/terrace.db" ]; then
  SOURCE_DB="$TARGET/data/terrace.db" DEST_DB="$BACKUP/data/terrace.db" python3 - <<'PY'
import os, sqlite3, sys
with sqlite3.connect(os.environ['SOURCE_DB']) as source:
    with sqlite3.connect(os.environ['DEST_DB']) as target:
        source.backup(target)
with sqlite3.connect(os.environ['DEST_DB']) as c:
    if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
        sys.exit('HATA: SQLite yedek bütünlüğü başarısız')
print('SQLite yedeği doğrulandı')
PY
fi
if [ -d "$TARGET/data/uploads" ]; then cp -R "$TARGET/data/uploads" "$BACKUP/data/"; fi
restore_old(){
  echo "Güncelleme başarısız; eski program kodları geri yükleniyor..."
  for f in app.py launcher.py update_service.py start.sh start.bat requirements.txt version.json; do
    if [ -f "$BACKUP/$f" ]; then cp "$BACKUP/$f" "$TARGET/$f"; else rm -f "$TARGET/$f"; fi
  done
  rm -rf "$TARGET/static"; mkdir -p "$TARGET/static"
  cp "$BACKUP/static/"* "$TARGET/static/" 2>/dev/null || true
}
trap restore_old ERR
mkdir -p "$TARGET/static"
for f in app.py launcher.py update_service.py start.sh start.bat requirements.txt version.json; do
  [ ! -f "$SOURCE/$f" ] || cp "$SOURCE/$f" "$TARGET/$f"
done
for f in index.html v2.css updater.css updater.js; do cp "$SOURCE/static/$f" "$TARGET/static/$f"; done
chmod +x "$TARGET/start.sh"
trap - ERR
printf '\n===========================================\n'
echo 'TERRACE FINANCE V2.3 KURULDU'
echo "Önceki uygulama ve veri yedeği: $BACKUP"
echo "Mevcut veriler: $TARGET/data"
echo "Başlat: cd \"$TARGET\" && bash start.sh"
echo 'Tarayıcı → Sol menü → Güncellemeler'
echo 'Not: Gerçek otomatik sürüm yayınları için bir HTTPS sunucusu ve yayıncı anahtarı tanımlanmalıdır.'
printf '===========================================\n'
