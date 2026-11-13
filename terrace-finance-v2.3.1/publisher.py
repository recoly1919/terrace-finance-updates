#!/usr/bin/env python3
"""Publisher-side signer. Run on the trusted release maker's Mac, never the public server.

Usage:
  python3 publisher.py keygen --private ~/TerraceKeys/signing.key --public ~/TerraceKeys/signing.pub
  python3 publisher.py publish --source ../terrace-finance-v2.4 --version 2.4.0 \\
      --base-url https://updates.example.com --private ~/TerraceKeys/signing.key \\
      --output ./release-server/public

Only the PUBLIC manifest, version ZIP and server config are uploaded to hosting.
The private signing key MUST stay outside the web root and outside the user app ZIP.
"""
import argparse
import base64
import hashlib
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ALLOWED = {'app.py', 'launcher.py', 'update_service.py', 'start.sh', 'start.bat',
           'requirements.txt', 'version.json', 'README.md',
           'static/index.html', 'static/v2.css', 'static/updater.css', 'static/updater.js'}
REQUIRED = ALLOWED - {'README.md','start.bat'}


def generate(args):
    key = Path(args.private).expanduser()
    pub = Path(args.public).expanduser()
    if key.exists() or pub.exists():
        raise SystemExit('HATA: Anahtar dosyaları var; imza anahtarını yanlışlıkla değiştirmeyin.')
    private = Ed25519PrivateKey.generate()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    key.chmod(0o600)
    raw = private.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                            format=serialization.PublicFormat.Raw)
    pub.parent.mkdir(parents=True, exist_ok=True)
    pub.write_text(base64.b64encode(raw).decode('ascii')+'\n', encoding='utf-8')
    print('Özel anahtar (saklayın, PAYLAŞMAYIN):', key)
    print('Açık anahtar (uygulamaya girilebilir):', pub)
    print('PUBLIC KEY BASE64: ',base64.b64encode(raw).decode('ascii'))


def publish(args):
    src = Path(args.source).expanduser().resolve()
    out = Path(args.output).expanduser().resolve()
    private_path = Path(args.private).expanduser()
    if not private_path.is_file():
        raise SystemExit('HATA: İmza özel anahtarı yok')
    try:
        private_path.resolve().relative_to(out)
    except ValueError:
        pass
    else:
        raise SystemExit('HATA: Özel anahtar yayınlanacak public dizininin içinde tutulamaz')
    if not src.is_dir():
        raise SystemExit('HATA: Yeni sürüm kaynak klasörü bulunamadı')
    u = urlsplit(args.base_url.rstrip('/'))
    if u.scheme != 'https' or not u.hostname or u.query or u.fragment or u.username or u.password:
        raise SystemExit('HATA: HTTPS base URL zorunlu')
    if not __import__('re').fullmatch(r'\d+\.\d+\.\d+', args.version):
        raise SystemExit('HATA: --version x.y.z olmalı')
    v = json.loads((src/'version.json').read_text('utf-8'))
    if v.get('app')!='terrace-finance' or v.get('version')!=args.version:
        raise SystemExit('HATA: version.json beklenen sürümle eşleşmiyor')
    if not all((src/f).is_file() for f in REQUIRED):
        raise SystemExit('HATA: Sürüm dosyaları eksik')
    private = serialization.load_pem_private_key(private_path.read_bytes(),password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise SystemExit('HATA: Ed25519 özel anahtarı gerekli')
    package = f'TerraceFinance_{args.version}_Mac.zip'
    packages = out/'packages'; packages.mkdir(parents=True,exist_ok=True)
    filename = packages/package
    with zipfile.ZipFile(filename,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(ALLOWED):
            if (src/name).is_file():
                z.write(src/name,name)
    data = filename.read_bytes()
    manifest = {'schema':1, 'app':'terrace-finance', 'platform':'mac', 'channel':'stable',
                'version':args.version, 'min_version':args.min_version,
                'url':args.base_url.rstrip('/')+'/packages/'+package,
                'sha256':hashlib.sha256(data).hexdigest(), 'size':len(data),
                'notes':{'tr':args.notes_tr,'zh':args.notes_zh}}
    canonical=json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    manifest['signature']=base64.b64encode(private.sign(canonical)).decode('ascii')
    stable=out/'stable';stable.mkdir(parents=True,exist_ok=True)
    (stable/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Yayın klasörü:',out)
    print('Manifest:',stable/'manifest.json')
    print('İmzalı ZIP:',filename)
    print('Uygulama sunucu adresi:',args.base_url.rstrip('/')+'/stable/manifest.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Terrace Finance signed release publisher')
    sub=parser.add_subparsers(dest='cmd',required=True)
    gen=sub.add_parser('keygen',help='Create Ed25519 publisher keypair')
    gen.add_argument('--private',required=True);gen.add_argument('--public',required=True)
    gen.set_defaults(fn=generate)
    rel=sub.add_parser('publish',help='Build signed manifest + release ZIP')
    rel.add_argument('--source',required=True);rel.add_argument('--version',required=True)
    rel.add_argument('--base-url',required=True);rel.add_argument('--private',required=True)
    rel.add_argument('--output',default='./release-server/public')
    rel.add_argument('--min-version',default='2.3.0')
    rel.add_argument('--notes-tr',default='Güncelleme ve iyileştirmeler')
    rel.add_argument('--notes-zh',default='系统更新与改进')
    rel.set_defaults(fn=publish)
    args=parser.parse_args();args.fn(args)
