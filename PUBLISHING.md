# Terrace Finance — imzalı güncelleme yayını

Bu herkese açık depo yalnızca statik **yayın** dosyalarını barındırır. **Finans verisi, fatura, kullanıcı verisi, parola, SQLite DB ve özel imza anahtarı yüklemeyin.**

## Durum
- `index.html`: güncellemeler için bilgilendirme sayfası.
- `stable/manifest.json`: henüz yayımlanmadı. **Geçerli imza olmadan manifest yayınlamayın.**
- `packages/`: henüz uygulama sürümü yüklenmedi.

## GitHub Pages
Repository > Settings > Pages > Build and deployment > Source: Deploy from a branch > Branch: main, /(root) > Save.

Bu durumda yayın adresleri:
- https://recoly1919.github.io/terrace-finance-updates/
- https://recoly1919.github.io/terrace-finance-updates/stable/manifest.json (yalnızca imzalı sürüm yayımlanınca)

## Yeni sürüm yayımlama
1. Güvenilir Mac üzerinde, **GitHub deposunun dışında** Ed25519 özel/açık anahtar çifti üretin. Özel anahtarı hiçbir yere göndermeyin.
2. Uygulamanın V2.3 `publisher.py` aracıyla sonraki sürüm ZIP'ini ve imzalı `manifest.json` üretin. `--base-url` değerini `https://recoly1919.github.io/terrace-finance-updates` olarak belirleyin.
3. Yalnızca üretilen `stable/manifest.json` ve `packages/*.zip` dosyalarını yükleyin.
4. Mac'teki Terrace Finance > Güncellemeler ekranına manifest adresi ve **açık** imza anahtarını kaydedin.
5. Önce test Mac'inde doğrulayın; ardından yeni sürümü yayımlayın.

Not: İstemci yönlendirme kabul etmiyor ve aynı origin üzerinden indirme bekliyor; bu nedenle manifest ve ZIP bağlantıları aynı GitHub Pages host ve repo yolu altında olmalıdır.
