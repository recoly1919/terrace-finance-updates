# Terrace Finance V2.3 — Test raporu

**Tarih:** 13 Kasım 2026  
**Test ortamı:** Linux tabanlı çalışma ortamı, Python 3.13, Chromium; hedef kurulum: macOS + kullanıcıda Python 3.15.2 (kullanıcının Mac'inde ayrıca doğrulanmalıdır).

## Geçen işlev testleri

| Kontrol | Sonuç |
|---|---|
| V2.2 klasöründen V2.3'e geçiş; kullanıcı hesabı ve gider kaydı korunması | Başarılı |
| SQLite çevrimiçi yedeği / `PRAGMA integrity_check` | Başarılı |
| Yüklenen belge yedeği / geri yükleme | Başarılı |
| Güncelleme yöneticisinin ayrı başlatıcı olarak çalışması | Başarılı |
| Ed25519 imzalı manifestten güncelleme denetimi | Başarılı |
| İmzadan sonra değiştirilen manifestin reddi | Başarılı |
| SHA-256 ve paket boyutu kontrolü | Başarılı |
| ZIP `../` yol manipülasyonunun reddi | Başarılı |
| 2.3.0 → 2.4.0 kod güncellemesi / veri ve belge korunması | Başarılı |
| Açılmayan 2.5.0 paketine karşı otomatik kod + veri geri yükleme | Başarılı |
| Manuel eski kod sürümüne geri dönüş / mevcut finans kayıtlarının korunması | Başarılı |
| Yayıncı Ed25519 anahtar üretimi + imzalı sürüm üretme CLI | Başarılı |
| Güncelleme panelinin JS sözdizimi kontrolü | Başarılı |
| 1440 px panel render ve Türkçe / Çince başlık geçişi | Başarılı, çevrimdışı Chromium test verisiyle |

## Sınırlamalar

- **Gerçek, kamuya açık bir HTTPS sunucusu henüz kurulmuş değildir.** Sunucu için alan adı veya barındırma erişimi gerekmektedir. Bu nedenle gerçek internet üzerinden sürüm yayınlama uçtan uca doğrulanmadı.
- Mac'teki mevcut V2.2 ile gerçek yerinde güncelleme henüz yapılmadı. Kullanıcının Terminal çıktısıyla doğrulanacak.
- Yeni sürümlerin otomatik yayımlanması için yayıncı anahtarı ve dağıtım süreci gerekir; ChatGPT arka planda yayın yapamaz.
- Elle geri alma yeni finans verilerini korumak için sadece **uygulama kodunu** değiştirir. Eski uygulama daha yeni veritabanı şemasını desteklemeyebilir.
- Güncelleme sonrası kurulan Python paketlerinin tümü için bağımsız sanal ortam sürümleme uygulanmamaktadır; kod/SQLite/belge geri yüklemesi test edildi.
- Disk arızası veya cihaz kaybı için harici yedek ayrıca gereklidir.

## Güvenlik sınırları

- Güncelleme kontrolü ve yükleme sadece giriş yapmış owner hesabına açıktır, CSRF kontrolü gerektirir.
- HTTP üzerinden internet dağıtımı, origin değişikliği, redirect, sembolik bağlantı, `data/` içeren ZIP paketleri reddedilir.
- Özel imza anahtarı kullanıcı paketine **dahil değildir**.
- Yayın sunucusu yalnızca program dosyalarını barındırır; kullanıcı verilerini almaz.
