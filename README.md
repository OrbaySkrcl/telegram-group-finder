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

```bash
python -m tgfinder backfill @birkanal @baskabirkanal --days 14
```

Kanalın son 14 günlük mesaj geçmişini okur, contract adreslerini çıkarır,
**o günkü mum verisini** GeckoTerminal'den çeker ve simülasyonu geçmişe dönük
çalıştırır. Birkaç dakika içinde kanalın sicil defteri önünde olur.

> Not: dakikalık mum verisi sadece yakın geçmiş için mevcuttur; daha eski
> çağrılarda otomatik olarak saatlik muma düşer. Saatlik veride bir mum içinde
> hem 2x hem -%50 görülebilir ve sistem **kötümser** davranıp stop'u varsayar.
> Yani eski dönem sonuçları gerçekten biraz daha kötü görünür, iyimser değil.

---

## Kurulum

### 1. Telegram API anahtarı
https://my.telegram.org → *API development tools* → `api_id` ve `api_hash` al.

### 2. Yerel kurulum

```bash
git clone https://github.com/OrbaySkrcl/telegram-group-finder
cd telegram-group-finder
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # api_id / api_hash'i içine yaz
python login.py           # telefon + kod → TG_SESSION çıktısını .env'e yapıştır
```

> `TG_SESSION` hesabına tam erişimdir. Repoya commit'leme, kimseyle paylaşma.
> **Ana hesabınla değil, ayrı bir numara/hesapla kullan** — bu araç kanallara
> katılıyor ve Telegram toplu katılımı hız limitine takar.

### 3. İlk veri

```bash
# Zaten üye olduğun kanalları anında değerlendir
python -m tgfinder backfill @kanal1 @kanal2 --days 14
python -m tgfinder score
```

### 4. Sürekli çalıştırma (Railway)

1. Repoyu Railway'de yeni bir servis olarak bağla (Dockerfile'ı otomatik bulur).
2. **Volume ekle**, mount path: `/data` — bunu yapmazsan her deploy'da tüm
   geçmişi kaybedersin.
3. Değişkenleri gir: `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`,
   `DB_PATH=/data/tgfinder.db`.
4. Deploy. Servis kanalları canlı dinler, her gün UTC 06:00'da liderlik
   tablosunu **Kayıtlı Mesajlar**'ına gönderir.

Kaynak kullanımı çok düşük (tek Python süreci + SQLite), ama Railway'in ücretsiz
kullanım limiti zaman içinde değişebiliyor — hesabındaki güncel plana bakmakta
fayda var. Kullandığı iki veri servisi (DexScreener, GeckoTerminal) API anahtarı
istemez ve ücretsizdir.

---

## Komutlar

```bash
python -m tgfinder backfill @kanal --days 14   # geçmişi çek + puanla (buradan başla)
python -m tgfinder score                       # liderlik tablosu
python -m tgfinder detail @kanal               # o kanalın çağrı çağrı defteri
python -m tgfinder channels                    # izlenen kanallar

python -m tgfinder discover "solana calls"     # Telegram'ın kendi dizininde ara
python -m tgfinder seed --file seeds.txt       # X'ten kopyaladığın metni yapıştır
python -m tgfinder candidates                  # aday havuzu (bahsedilme sıralı)
python -m tgfinder approve @kanal              # onayla
python -m tgfinder join                        # onaylıları katıl (günlük limitli)

python -m tgfinder monitor @kanal              # bu kanalı canlı dinlemeye al
python -m tgfinder monitor @kanal --off        # dinlemeyi bırak
python -m tgfinder run                         # servis modu (Railway girişi)
```

### Gizlilik notu

`run` hesabının üye olduğu her sohbeti otomatik olarak dinlemez. Sadece bilerek
eklediğin kanalları okur (`backfill`, `join` veya `monitor` ile eklenenler).
Kişisel grupların kapsam dışında kalır. Hepsini dinlemek istersen `run
--adopt-all` diyebilirsin, ama önerilmez.

### Kendi kendini büyüten aday havuzu

İzlenen kanallarda geçen her `t.me` linki ve her **forward kaynağı** otomatik
olarak aday havuzuna düşer. Forward'lar ayrıca ağırlıklıdır: takip ettiğin üç
kanal aynı yerden forward yapıyorsa, o kaynak muhtemelen zincirin yukarısıdır.
Bu, X'te arama yapmaktan çok daha verimli bir keşif yoludur — havuz kendi
kendini besler.

`join` günlük katılım limitine uyar ve katılımlar arasında bekler; Telegram
toplu katılımı cezalandırır.

---

## Nasıl kullanılmalı

1. Üye olduğun kanalları `backfill` et. Muhtemelen çoğunun `AVG$` değeri
   negatif çıkacak. Bu normal ve zaten öğrenmek istediğin şey buydu.
2. `discover` + `seed` ile havuzu genişlet, `candidates` listesinden mantıklı
   görünenleri onayla, `join` et.
3. `run` ile birkaç hafta topla.
4. Haftalık `score` bak. Sürekli pozitif `AVG$` + yüksek `1ST%` + düşük `RUG%`
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
