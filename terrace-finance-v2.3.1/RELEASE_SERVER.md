# Terrace Finance — Güvenli güncelleme yayını

## Gerçekte neler kurulmuş?

V2.3, **uygulama içinden güncelleme istemcisi + Mac başlatıcısı + yedek ve geri alma sistemi + yayıncı aracı** içerir. Henüz internette yayında olan bir güncelleme sunucusu **yoktur**; bunun için bir HTTPS alan adı/hosting sizin tarafınızdan seçilmeli ve yayınlanmalıdır. V2.3 tek başına yeni sürümleri kendiliğinden internete çıkaramaz. Bu dosya güvenli bir yayının nasıl kurulacağını açıklar.

## Sunucu mimarisi

```
Mac / Terrace Finance V2.3
  Yönetici → Güncellemeler → Güncelle
       | HTTPS manifest.json (Ed25519 imza)
       v
https://updates.sizin-alanadiniz.com/stable/manifest.json
       |  Aynı HTTPS origin, SHA-256 doğrulaması
       v
https://updates.sizin-alanadiniz.com/packages/TerraceFinance_2.4.0_Mac.zip
       |
       v
Mac ayrı başlatıcı: Uygulamayı durdur → SQLite + belgeleri yedekle →
  doğrulanmış dosyaları değiştir → başlat → /api/health kontrolü
       |
       + Başarılı → yeni sürüm
       + Başarısız → önceki kod + önceki veritabanı + belgeler
```

**Önemli:** Dağıtım sunucusunda veritabanınız, kullanıcı şifreleri, faturalar veya ödeme kayıtları bulunmaz; yalnızca yayımlanacak **program sürümleri** bulunur. V2.3'te ayarlanan feed URL ve açık imza anahtarı Mac'te `data/updates/config.json` içinde saklanır. Özel imza anahtarı **bu dizine veya internetteki sunucuya konulmaz**.

## 1. Yayıncı anahtarı oluşturma (yalnızca 1 kez)

Yayıncı Mac'te, uygulamanın kaynak paketinden **yalnızca** `publisher.py` dosyasını güvenilir bir dizine alın. Python ve `cryptography` kurulu olsun.

```bash
python3 publisher.py keygen \
  --private "$HOME/TerracePublisherSecrets/signing.key" \
  --public "$HOME/TerracePublisherSecrets/signing.pub"
```

`signing.key` özel anahtardır: ayrı bir yedekte saklayın, kimseye göndermeyin ve web sunucusuna yüklemeyin. `signing.pub` içindeki base64 metni uygulamanızın **Güncellemeler** sayfasına bir defa girilir.

## 2. Yeni sürümü imzalama

Yeni `terrace-finance-v2.4` klasörünüzün içindeki `version.json` sürümü `2.4.0` olmalıdır.

```bash
python3 publisher.py publish \
  --source "$HOME/Desktop/terrace-finance-v2.4" \
  --version 2.4.0 \
  --base-url https://updates.sizin-alanadiniz.com \
  --private "$HOME/TerracePublisherSecrets/signing.key" \
  --output "$HOME/TerraceRelease/public" \
  --notes-tr "Hata düzeltmeleri ve yeni özellikler"
```

Bu işlem sadece `TerraceRelease/public/stable/manifest.json` ve `TerraceRelease/public/packages/TerraceFinance_2.4.0_Mac.zip` üretir. Sunucuya **yalnızca `public` dizininin içeriğini** gönderin; `signing.key` dosyasını değil. İsterseniz örnek `release-server/nginx.conf.example` kullanarak standart HTTPS statik sunucu yapılandırabilirsiniz. Nginx şart değildir; aynı dizinleri HTTPS üzerinden sunan Cloudflare Pages gibi statik hosting hizmetleri de kullanılabilir. Yönlendirmesiz, aynı origin üzerinden erişim gereklidir.

## 3. Mac'te bir kerelik bağlantı

Terrace Finance > **Güncellemeler** > HTTPS manifest adresini ve `signing.pub` içeriğini kaydedin:

```
https://updates.sizin-alanadiniz.com/stable/manifest.json
```

Sonra **Yeni sürümü kontrol et** düğmesini kullanın. Yeni sürüm varsa **Doğrula, indir ve güncelle** düğmesi tek tıkla paketi doğrular, indirir, yedekler, yükler ve yeniden başlatır.

### Güvenlik ve geri dönüş

- Ed25519 imza ve SHA-256 doğrulanır; hatalı paket kurulmaz.
- HTTP üzerinden internet güncellemesi, yönlendirme ve farklı kaynak hostları reddedilir.
- ZIP içindeki `../`, mutlak yol, sembolik link, `data/` ve bilinmeyen dosyalar reddedilir.
- Güncelleme işlemleri sadece yönetici hesabıyla, CSRF token kontrolüyle yürütülür.
- SQLite veritabanı **online backup API** ile, sunucu durdurulduktan sonra yedeklenir; ayrıca yüklenen belgeler kopyalanır.
- Kurulum sonrası yerel `/api/health` sürüm testi başarısızsa otomatik geri dönülür.
- Elle **geri dönme** yalnızca uygulama kodunu değiştirir ve yeni girilen finans kayıtlarını silmez; eski kodun yeni veritabanıyla uyumsuz olması halinde bunu ayrıca test etmek gerekir.
- Windows/uzak bulut dağıtımı bu pakette desteklenmez; başlatıcı macOS/Linux benzeri yerel ortam içindir.
- Yeni sürümlerin HTTPS'de yayımlanması ve imzalanması ayrıca yapılmalıdır. ChatGPT arka planda sunucu işletemez.

### Güncelleme klasörleri

- Canlı veri: `~/Desktop/terrace-finance/data/terrace.db` ve `data/uploads/`
- Yayın ayarları: `data/updates/config.json`
- İndirilen paket: `data/updates/staged.zip`
- Güvenli geri dönüşler: `data/updates/backups/backup-...`
- İşlem günlüğü: `data/updates/state.json`

**Yedekleri durup dururken silmeyin.** Güncellemeler veri saklar; fakat Mac arızası veya disk kaybına karşı harici/şifreli düzenli yedek de gerekir.
