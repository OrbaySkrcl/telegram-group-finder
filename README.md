# tgfinder

Telegram "call" kanallarının **gerçekten bir avantajı olup olmadığını ölçen** araç.

Amaç kanal *bulmak* değil — kanal bulmak kolay. Amaç, bulduğun 50 kanaldan
hangi 3 tanesinin gerçekten para kazandırdığını **sayıyla** ayırmak.

---

## Neden X'te aramak çalışmıyor

`"t.me" "CA:"` araması seni sadece kendini pazarlayan kanallara götürür. Çünkü:

- X'te görünür olmak *iyi olmakla* değil, *reklam bütçesiyle* ilgili.
- Bir kanalın "geçmiş performansı" olarak gösterdiği şey, kazanan çağrıların
  ekran görüntüsüdür. Kaybedenler silinmiştir.
- "10x yaptı" demek hiçbir şey ifade etmez — o fiyattan **satamazsın**. Tepe
  noktası bir tik'tir, likidite değil.

Bu araç problemi tersine çevirir: kanalın ne dediğine bakmaz, **ne yaptığının
defterini tutar**.

---

## Ölçtüğü tek dürüst soru

> Bu kanalın **her** çağrısını, mesajı gördükten 60 saniye sonra, %3 slipaj
> ödeyerek, mekanik bir kuralla (2x'te sat / -%50'de kes / 24 saatte çık)
> körü körüne alsaydım — cebimde ne kalırdı?

Bu sayı `AVG$` sütunu. Sıralama buna göre yapılır. Tepe çarpanları (`PEAK`)
sadece bağlam için gösterilir, sıralamaya girmez.

### Ek olarak ölçtükleri

| Sütun | Anlamı | Neden önemli |
|---|---|---|
| `1ST%` | İzlediğin kanallar arasında bu tokeni **ilk** çağıran o mu | Asıl avantaj bu. Düşükse kanal sadece kopyalıyor |
| `UNQ%` | Başka hiçbir kanalın çağırmadığı çağrı oranı | Bağımsız kaynak mı, yoksa aynı havuzdan mı besleniyor |
| `RUG%` | 24 saat içinde sıfırlanan çağrı oranı | Erken çağrı yapıp rug yiyorsan avantaj değil, tuzak |
| `/DAY` | Günlük çağrı sayısı | Günde 30 coin atan kanalın "isabeti" istatistik değil, kepçe |
| `MED-MC` | Çağrı anındaki medyan market cap | $2M'de çağıran kanal sana geç kalmış bilgi satıyor |
| `SCORE` | Örneklem büyüklüğüne göre sıfıra çekilmiş `AVG$`, spam/rug/kopyalama cezalı | Tek sayıya indirilmiş hali |

### Sistemin kasten kaçındığı iki tuzak

**1. Survivorship bias.** Rug olan bir tokenin havuzu kaybolur, fiyat verisi
kalmaz. Bu çağrıları sessizce atarsan rug basan her kanal mükemmel görünür.
Burada bunlar `DEAD-LINKS` olarak sayılır ve raporlanır. Havuzu bulunup sonra
sıfırlanan tokenler ise gerçek kaybı kadar zarar yazar.

**2. Ölü adresle "erkenci" görünmek.** Kimsenin bahsetmediği çöp adres atan bir
kanal, teknik olarak o tokenin "ilk çağıranı" olur. `1ST%` sadece gerçekten
işlem görmüş tokenler üzerinden hesaplanır, bu yüzden bu numara işlemez.

---

## Bayrak (flag) sözlüğü

| Bayrak | Ne demek |
|---|---|
| `EARLY` | Çağrılarının %35'inden fazlasında ilk o çağırmış — incelemeye değer |
| `UNIQUE` | Çağrılarının yarısından fazlasını başka kimse yapmamış |
| `LATE` | Sistematik olarak başkasının arkasından geliyor |
| `SPAM` | Günde 10+ çağrı |
| `RUGGY` | Çağrılarının %35'inden fazlası sıfırlanmış |
| `DEAD-LINKS` | Adreslerinin %30'undan fazlası hiç işlem görmemiş |
| `HIGH-MC` | Medyan çağrı MC'si $1M üstü — haber değil, dedikodu |
| `INSIDER?` | Tokenleri doğuşundan ~5 dk içinde çağırıyor **ve** rug oranı yüksek |

`INSIDER?` bayrağı hakkında dürüst olmak gerekirse: aradığın "küçük, spam
yapmayan, çok erken çağıran grup" tarifi ile **dağıtım yapan içeriden bir ekip**
tarifi dışarıdan neredeyse aynı görünür. Fark, kimin çıkış likiditesi olduğunda
ortaya çıkar. Bu bayrak tam olarak o örtüşmeyi işaretlemek için var — gördüğünde
o kanalın `RUG%` ve `UNQ%` değerlerine iki kez bak.

---

## En önemli özellik: `backfill`

Normalde bir kanalı değerlendirmek için haftalarca beklemen gerekir.
`backfill` beklemeyi ortadan kaldırır:

Telegram'da Kayıtlı Mesajlar'a şunu yaz:

```
/backfill @birkanal 30
```

Kanalın son 30 günlük mesaj geçmişini okur, contract adreslerini çıkarır,
**o günkü mum verisini** GeckoTerminal'den çeker ve simülasyonu geçmişe dönük
çalıştırır. Birkaç dakika içinde kanalın sicil defteri önünde olur.

> Not: dakikalık mum verisi sadece yakın geçmiş için mevcuttur; daha eski
> çağrılarda otomatik olarak saatlik muma düşer. Saatlik veride bir mum içinde
> hem 2x hem -%50 görülebilir ve sistem **kötümser** davranıp stop'u varsayar.
> Yani eski dönem sonuçları gerçekten biraz daha kötü görünür, iyimser değil.

---

## Sık sorulanlar

### Bot kendi kendine gruplara katılıyor mu?

**Hayır.** Hiçbir koşulda kendiliğinden katılmaz. Katılmanın tek yolu, senin
önce `/approve @kanal` sonra `/join` yazman. Bu iki komutu hiç kullanmasan da
sistem tam çalışır.

Dahası, **bir kanalı değerlendirmek için üye olman bile gerekmiyor**: herkese
açık kanalların geçmişini Telegram üye olmadan da okutuyor. Yani:

1. `/backfill @kanal 30` — üye olmadan sicilini çıkar
2. `/score` — rakamlara bak
3. Beğenirsen elle katıl, sonra `/monitor @kanal` de
4. Beğenmezsen hiçbir şey yapma; zaten katılmamıştın

Backfill sonucunda üye olmadığın kanalları ayrıca yazar. Üye olmadığın bir
kanal **canlı takibe alınmaz** — çünkü Telegram yalnızca üye olduğun sohbetlerin
yeni mesajlarını gönderir. Geçmiş verisi yine de puanlanır.

Bir gruptan çıkarsan topladığın veri silinmez; skoru tabloda kalır.

> Kendi ana numaranı kullanacaksan: `/join` komutundan uzak dur ve `/backfill`'i
> tek seferde 3-5 kanaldan fazlasına verme. Sistem kanallar arasında bekliyor ve
> Telegram "yavaşla" derse elindekini kaydedip duruyor, ama toplu okuma yine de
> hız limitine takılabilen bir davranış.

### En fazla 14 gün mü çekebiliyorum?

Hayır, 14 sadece örnekti — varsayılan 30 gün ve istediğin sayıyı yazabilirsin:

```
/backfill @kanal 90
```

Gerçek sınır Telegram değil, **fiyat geçmişi**:

- Dakikalık mum verisi sadece yakın geçmiş için var. Daha eski çağrılarda sistem
  otomatik olarak saatlik muma düşer.
- Saatlik veride bir mum içinde hem 2x hem -%50 görülebilir; sistem bu durumda
  **kötümser** davranıp stop'u varsayar. Yani eski dönem sonuçları gerçekte
  olduğundan biraz kötü görünür, iyimser değil.
- Çok eski çağrılarda tokenlerin havuzu tamamen silinmiş olur; bunlar
  `DEAD-LINKS` olarak sayılır.

Pratikte **30-60 gün** en sağlıklı aralık. 90 gün de çalışır ama eski kısmı
daha kaba ölçülür.

### BSC / Solana / başka ağlar — hepsini algılıyor mu?

Sistem zincir listesi tutmuyor, **adres biçimini** tanıyor:

| Biçim | Örnek | Kapsadığı |
|---|---|---|
| Solana (base58, 32 bayt) | `EKpQGS...zcjm` | Solana |
| EVM (`0x` + 40 hex) | `0x2170ed...33f8` | BSC, Ethereum, Base, Arbitrum, Polygon ve DexScreener'ın listelediği tüm EVM ağları |
| Tron (base58check) | `TLa2f6...YjU7` | Tron |

EVM adresinin hangi ağda olduğunu tahmin etmiyor — DexScreener'a soruyor ve
cevabı ne gelirse onu kullanıyor. Yani **BSC'yi ayrıca ayarlaman gerekmiyor**,
kendiliğinden çalışıyor. Aynı mesajda üç farklı ağdan CA varsa üçünü de ayrı
çağrı olarak kaydeder.

Mum verisi için GeckoTerminal'in ağ listesini çalışma anında çekiyor, dolayısıyla
bu araç yazıldığında var olmayan yeni bir ağ da kod değişmeden çalışabiliyor.

**Emin olmanın yolu:** birkaç kanal backfill ettikten sonra

```
/chains
```

yaz. Hangi ağda kaç çağrı toplandığını ve kaçının puanlanabildiğini gösterir:

```
ZİNCİR                 ÇAĞRI   PUANLI  BEKLİYOR  DESTEKSİZ
solana                 48      42      6         0
bsc                    18      18      0         0
base                   7       7       0         0
tron                   3       3       0         0
yenibirzincir          4       0       0         4
```

`DESTEKSİZ` sütunu, o ağ için mum verisi bulunamayan çağrılar. Bunlar
**hiçbir kanalın puanına dahil edilmez** — bu bizim eksiğimiz, kanalın suçu
değil. Orada sıfırdan büyük bir sayı görürsen bana ağın adını söyle, ekleyelim.

---

## Kurulum — kod bilmene gerek yok

Kurduktan sonra sistemi **Telegram'dan kendine mesaj atarak** yönetiyorsun.
Terminal, komut satırı, bilgisayara program kurma yok.

### Adım 1 — Telegram API bilgilerini al

https://my.telegram.org → telefon numaranla giriş → **API development tools** →
bir uygulama oluştur (isim/kısa isim ne olursa olur).

Sana iki şey verecek, ikisini de bir yere not et:
- `api_id` — kısa bir sayı
- `api_hash` — uzun bir harf-rakam dizisi

### Adım 2 — Giriş anahtarını üret (tarayıcıdan)

https://colab.research.google.com adresine git → **New notebook**.

Açılan hücreye şunu yapıştır ve ▶ tuşuna bas:

```python
!pip install -q telethon nest_asyncio
import nest_asyncio; nest_asyncio.apply()
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("api_id: ").strip())
api_hash = input("api_hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nGiris basarili:", (client.get_me()).username)
    print("\nTG_SESSION degerin (tamamini kopyala):\n")
    print(client.session.save())
```

Sırayla soracak: `api_id`, `api_hash`, telefon numaran (`+905551112233`
biçiminde), sonra Telegram'dan gelen kod. İki adımlı doğrulaman varsa şifreni de
soracak. En sonda uzun bir metin basacak — bu senin `TG_SESSION` değerin.

(Aynı işi yapan hazır defter depoda `login_colab.ipynb` olarak da duruyor;
Colab'da *File → Upload notebook* ile açabilirsin.)

> ⚠️ Bu metin hesabına tam erişimdir. Kimseyle paylaşma, hiçbir yere gönderme.
>
> Bu araç kanallara katılıyor ve Telegram toplu katılımı hız limitine takıyor.
> **Ana hesabın yerine ayrı bir numarayla açılmış hesap kullanman önerilir.**

### Adım 3 — Railway'de değişkenleri gir

Servisin sayfasında **Variables** sekmesi → şu dördünü ekle:

| Değişken | Değer |
|---|---|
| `TG_API_ID` | Adım 1'deki sayı |
| `TG_API_HASH` | Adım 1'deki uzun dizi |
| `TG_SESSION` | Adım 2'deki uzun metin |
| `DB_PATH` | `/data/tgfinder.db` |

### Adım 4 — Volume ekle (atlanırsa veri kaybolur)

Servisin sayfasında **Volume** ekle, mount path: `/data`

Bunu yapmazsan her güncellemede topladığın tüm geçmiş silinir.

### Adım 5 — Telegram'dan kullan

Deploy bitince Telegram'da **Kayıtlı Mesajlar**'ı (Saved Messages) aç.
Sistem oraya "tgfinder çalışıyor" yazmış olacak.

```
/help                    komut listesi
/backfill @kanal 30      kanalın son 30 gününü çek ve puanla
/score                   liderlik tablosu
/detail @kanal           o kanalın çağrı çağrı defteri
/status                  sistem özeti
```

**Buradan başla:** zaten üye olduğun kanalları tek tek `/backfill @kanal 30`
yaz. Birkaç dakika içinde sicil defterleri önüne gelir.

---

## Telegram komutları

| Komut | Ne yapar |
|---|---|
| `/backfill @kanal 30` | Geçmişi çeker ve puanlar — **buradan başla** |
| `/score` | Liderlik tablosu |
| `/detail @kanal` | O kanalın çağrı çağrı defteri |
| `/channels` | İzlenen kanallar |
| `/monitor @kanal` | Canlı dinlemeye al / `/unmonitor` bırak |
| `/discover solana calls` | Telegram dizininde kanal ara |
| `/candidates` | Aday havuzu (bahsedilme sıralı) |
| `/approve @kanal` | Adayı onayla / `/reject` ele |
| `/join` | Onaylı adaylara katıl (günlük limitli) |
| `/chains` | Hangi ağlarda veri toplandı, kaçı puanlanabildi |
| `/status` | Sistem özeti |

Servis ayrıca her gün UTC 06:00'da liderlik tablosunu Kayıtlı Mesajlar'a gönderir.

Kullandığı iki veri servisi (DexScreener, GeckoTerminal) API anahtarı istemez ve
ücretsizdir. Sistem tek bir Python süreci + SQLite, kaynak kullanımı çok düşük —
ama Railway'in ücretsiz kullanım limiti zaman içinde değişebiliyor, hesabındaki
güncel plana bakmakta fayda var.

---

## Terminal kullanmayı tercih edersen

```bash
git clone https://github.com/OrbaySkrcl/telegram-group-finder
cd telegram-group-finder
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # api_id / api_hash'i içine yaz
python login.py           # TG_SESSION üretir

python -m tgfinder backfill @kanal --days 30
python -m tgfinder score
python -m tgfinder detail @kanal
python -m tgfinder channels
python -m tgfinder discover "solana calls"
python -m tgfinder candidates
python -m tgfinder approve @kanal
python -m tgfinder join
python -m tgfinder monitor @kanal        # --off ile bırakır
python -m tgfinder run                   # servis modu
```

### Gizlilik notu

`run` hesabının üye olduğu her sohbeti otomatik dinlemez. Sadece bilerek
eklediğin kanalları okur (`/backfill`, `/join` veya `/monitor` ile eklenenler).
Kişisel grupların kapsam dışında kalır.

### Kendi kendini büyüten aday havuzu

İzlenen kanallarda geçen her `t.me` linki ve her **forward kaynağı** otomatik
olarak aday havuzuna düşer. Forward'lar ayrıca ağırlıklıdır: takip ettiğin üç
kanal aynı yerden forward yapıyorsa, o kaynak muhtemelen zincirin yukarısıdır.
Bu, X'te arama yapmaktan çok daha verimli bir keşif yoludur — havuz kendi
kendini besler.

`/join` günlük katılım limitine uyar ve katılımlar arasında bekler; Telegram
toplu katılımı cezalandırır.

---

## Nasıl kullanılmalı

1. Üye olduğun kanalları `/backfill` et. Muhtemelen çoğunun `AVG$` değeri
   negatif çıkacak. Bu normal ve zaten öğrenmek istediğin şey buydu.
2. `/discover` ile havuzu genişlet, `/candidates` listesinden mantıklı
   görünenleri `/approve` edip `/join` et.
3. Birkaç hafta topla.
4. Haftalık `/score` bak. Sürekli pozitif `AVG$` + yüksek `1ST%` + düşük `RUG%`
   olan **2-3 kanalla** çalış, gerisini bırak.
5. Bir kanalın skoru düşmeye başlarsa bırak. Avantaj kalıcı değildir; kanal
   büyüdükçe avantajı kendiliğinden erir.

Ölçtüğün şey geçmiş performanstır ve geleceği garanti etmez; bu araç sana bir
kanalın kazandıracağını söylemez, **kazandırmadığını** çok daha güvenilir bir
şekilde söyler. Asıl değeri budur: 50 kanalın 47'sini elemek.

---

## Testler

```bash
python -m pytest tests/ -q
```

Simülatör, skorlama ve uçtan uca boru hattı ağ olmadan test edilir
(piyasa verisi sahte veriyle yerine konur).
