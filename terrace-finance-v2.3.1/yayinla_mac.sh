#!/usr/bin/env bash
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SECRET="$HOME/TerracePublisherSecrets/signing.key"
OUT="$HOME/TerraceRelease/public"
if [ ! -f "$SECRET" ]; then
  echo 'HATA: Özel anahtar bulunamadı. İşlemi durdurdum.' >&2
  exit 1
fi
python3 "$SRC/publisher.py" publish \
  --source "$SRC" \
  --version 2.3.1 \
  --base-url 'https://recoly1919.github.io/terrace-finance-updates' \
  --private "$SECRET" \
  --output "$OUT" \
  --min-version 2.3.0 \
  --notes-tr 'İlk güvenli otomatik güncelleme testi ve sürüm göstergesi' \
  --notes-zh '首次安全自动更新测试与版本显示'
echo ''
echo 'GitHub depoya yüklemek için yalnızca şunlar gerekir:'
echo "$OUT/stable/manifest.json"
echo "$OUT/packages/TerraceFinance_2.3.1_Mac.zip"
echo ''
echo 'Özel imza anahtarını asla yüklemeyin.'
