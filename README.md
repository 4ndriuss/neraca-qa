# neraca-qa

**Neraca: tanya-jawab laporan keuangan emiten BEI dengan perhitungan terverifikasi dan sitasi halaman.**

> ⚠️ Proyek ini untuk tujuan edukasi dan portofolio. Jawaban yang dihasilkan **bukan saran investasi**.

---

## Daftar isi

- [Latar belakang](#latar-belakang)
- [Contoh interaksi](#contoh-interaksi)
- [Ide kunci: LLM tidak boleh berhitung sendiri](#ide-kunci-llm-tidak-boleh-berhitung-sendiri)
- [Arsitektur](#arsitektur)
- [Tahapan](#tahapan)
- [Evaluasi](#evaluasi)
- [Masalah sulit yang perlu ditangani](#masalah-sulit-yang-perlu-ditangani)
- [Rencana pengerjaan](#rencana-pengerjaan)
- [Stack](#stack)
- [Data XBRL: kunci jawaban](#data-xbrl-kunci-jawaban)
- [Ekstraksi tabel PDF](#ekstraksi-tabel-pdf)
- [Normalisasi nama akun](#normalisasi-nama-akun)
- [Database](#database)
- [Alat dan set pertanyaan uji](#alat-dan-set-pertanyaan-uji)
- [Struktur folder](#struktur-folder)

---

## Latar belakang

Sistem RAG (*Retrieval-Augmented Generation*) biasa cukup baik untuk teks naratif, tetapi sering gagal pada **tabel dan angka**: salah membaca kolom, salah satuan, atau salah menghitung. Laporan keuangan penuh dengan tabel dan angka, sehingga menjadi tempat uji yang tepat.

Tujuan proyek ini adalah membangun sistem tanya-jawab atas laporan keuangan perusahaan publik di Bursa Efek Indonesia yang:

1. menjawab pertanyaan angka dengan **akurasi terukur**,
2. menunjukkan **dari mana angka berasal** (dokumen dan halaman), dan
3. **menolak** pertanyaan yang datanya tidak tersedia, alih-alih menebak.

## Contoh interaksi

> **Tanya:** "Laba bersih BBCA 2024 naik berapa persen dibanding 2023?"
>
> **Jawab:** "Naik sekitar X%, dari Rp A triliun menjadi Rp B triliun.
> Sumber: Laporan Keuangan 2024, Laporan Laba Rugi, hal. 7."

Angkanya harus benar, dan perhitungannya harus bisa ditelusuri.

## Ide kunci: LLM tidak boleh berhitung sendiri

LLM sering salah menghitung. Karena itu sistem dibagi menjadi dua jalur:

| Jalur | Untuk pertanyaan | Cara kerja |
|---|---|---|
| **Angka** | "Berapa total aset…", "Pertumbuhan pendapatan…", "ROE…" | Tabel diekstrak ke database terstruktur. LLM menulis query SQL atau memanggil fungsi kalkulator; **kode** yang menghitung. |
| **Naratif** | "Apa risiko utama…", "Apa strategi ekspansi…" | RAG biasa atas teks laporan (pembahasan manajemen, profil usaha, dsb.). |

Sebuah **router** di depan memutuskan jalur mana yang dipakai, atau keduanya.

## Arsitektur

```
                 ┌──────────────┐
  Pertanyaan ──▶ │    Router    │
                 └──────┬───────┘
          ┌─────────────┴─────────────┐
          ▼                           ▼
 ┌─────────────────┐         ┌─────────────────┐
 │   Jalur angka   │         │  Jalur naratif  │
 │ LLM → SQL/tool  │         │ hybrid search + │
 │ → hitung di kode│         │ rerank → LLM    │
 └────────┬────────┘         └────────┬────────┘
          │                           │
          ▼                           ▼
 ┌─────────────────┐         ┌─────────────────┐
 │ Tabel keuangan  │         │  Potongan teks  │
 │ (Postgres)      │         │  (pgvector)     │
 └─────────────────┘         └─────────────────┘
          └─────────────┬─────────────┘
                        ▼
              Jawaban + sitasi halaman
```

Skema tabel angka (gambaran awal):

| Kolom | Contoh |
|---|---|
| `emiten` | `BBCA` |
| `tahun` | `2024` |
| `periode` | `FY` / `Q1` … |
| `laporan` | `laba_rugi` / `posisi_keuangan` / `arus_kas` |
| `pos_akun` (ternormalisasi) | `laba_bersih` |
| `pos_akun_asli` | `Laba tahun berjalan` |
| `nilai` | `54836000000000` (dalam Rupiah penuh) |
| `sumber_dokumen` | `BBCA_LK_2024.pdf` |
| `halaman` | `7` |
| `disajikan_kembali` | `true` / `false` |

## Tahapan

### 1. Kumpulkan data

- Unduh laporan keuangan dari situs IDX (idx.co.id) untuk **10–20 emiten** selama **3–5 tahun**.
- Pilih sektor yang beragam karena formatnya berbeda, misalnya **bank**, **consumer goods**, dan **telekomunikasi**.
- Cek apakah file **XBRL** tersedia untuk emiten yang dipilih. Jika ada, data ini bisa menjadi kunci jawaban (*ground truth*) untuk evaluasi.

### 2. Ekstrak tabel

Bagian tersulit sekaligus paling berharga. Bandingkan beberapa alat, misalnya pdfplumber, Camelot, Docling, dan vision LLM, lalu catat akurasinya.

Masalah yang perlu ditangani:

- Format angka Indonesia: `1.234.567,89`
- Satuan di kepala tabel: "dalam jutaan Rupiah"
- Angka negatif dalam kurung: `(12.345)`
- Laporan dwibahasa dengan kolom Indonesia dan Inggris berdampingan
- Tabel yang terpotong ke halaman berikutnya

### 3. Normalisasi nama akun

Nama pos yang berbeda harus dipetakan ke pos yang sama, misalnya:

| Nama asli | Pos ternormalisasi |
|---|---|
| Laba tahun berjalan | `laba_bersih` |
| Laba bersih | `laba_bersih` |
| Profit for the year | `laba_bersih` |

Perhatikan juga bahwa **bank** memiliki pos akun yang tidak ada di perusahaan lain (misalnya pendapatan bunga bersih).

### 4. Jawab dengan sitasi

- Setiap angka dalam jawaban menyertakan dokumen dan halaman sumbernya.
- Perhitungan (selisih, persentase, rasio) dilakukan oleh kode dan ditampilkan langkahnya.
- Jika data tidak ada, sistem menjawab "data tidak ditemukan".

## Evaluasi

Bagian ini yang membuat proyek layak portofolio.

### Set pertanyaan uji (~100 pertanyaan)

| Kategori | Contoh |
|---|---|
| Lookup langsung | "Total aset UNVR 2023?" |
| Perhitungan | "Pertumbuhan pendapatan TLKM 2022–2024?" |
| Rasio | "ROE BBRI 2024?" |
| Perbandingan | "Mana yang margin labanya lebih tinggi, ICBP atau MYOR?" |
| Naratif | "Apa rencana ekspansi yang disebut manajemen?" |
| Tak terjawab | Pertanyaan yang datanya memang tidak tersedia |

### Metrik

| Metrik | Artinya |
|---|---|
| Akurasi angka | Jawaban numerik benar dengan toleransi pembulatan |
| Ketepatan sitasi | Halaman yang disebut memang memuat angka tersebut |
| Penolakan yang benar | Sistem menolak pertanyaan yang tidak bisa dijawab |
| Akurasi ekstraksi tabel | Persentase sel tabel yang terekstrak dengan benar |

### Baseline

Bandingkan sistem akhir dengan **RAG biasa** yang langsung membaca teks PDF (dipotong per karakter, tanpa jalur angka). Selisih hasil keduanya adalah inti cerita proyek ini.

Tabel hasil (diisi setelah evaluasi):

| Sistem | Akurasi angka | Ketepatan sitasi | Penolakan benar |
|---|---|---|---|
| Baseline RAG | – | – | – |
| neraca-qa | – | – | – |

## Masalah sulit yang perlu ditangani

**Angka yang disajikan kembali (*restatement*).** Laporan 2024 kadang memuat angka 2023 yang berbeda dari laporan 2023 aslinya. Sistem harus memutuskan angka mana yang dipakai dan menjelaskannya kepada pengguna. Ini masalah nyata yang juga dihadapi analis keuangan.

Masalah lain yang perlu dicatat keputusannya:

- Laporan konsolidasian vs. entitas induk
- Periode tahunan vs. kuartalan
- Perbedaan satuan antar laporan (ribuan, jutaan, miliar)

## Rencana pengerjaan

| Waktu | Target |
|---|---|
| Minggu 1–2 | Satu emiten, satu tahun, hanya pertanyaan lookup |
| Minggu 3–4 | Ekstraksi tabel yang andal dan fungsi kalkulator |
| Minggu 5 | Set evaluasi dan perbandingan dengan baseline |
| Minggu 6+ | Tambah emiten, router naratif, dan demo online |

## Stack

- **Python** untuk seluruh pipeline
- **pdfplumber / Docling** untuk ekstraksi PDF dan tabel
- **Postgres** untuk tabel angka, **pgvector** untuk teks naratif
- **API LLM dengan tool calling** untuk router, pembuatan query, dan penyusunan jawaban
- **Model embedding multilingual** (misalnya bge-m3) untuk pencarian teks
- **Streamlit** untuk demo

## Data XBRL: kunci jawaban

Angka resmi dari file XBRL IDX dipakai sebagai kunci jawaban untuk mengukur akurasi ekstraksi PDF.

### Cakupan

12 emiten × 4 tahun (2022–2025), semuanya laporan tahunan yang diaudit.

| Emiten | Sektor | Kasus khusus yang diuji |
|---|---|---|
| BBCA, BBRI, BMRI | Bank konvensional | Restatement, tag tertukar |
| BRIS | Bank syariah | Tanpa pendapatan bunga, dana syirkah temporer |
| UNVR | Consumer | Operasi yang dihentikan |
| ICBP | Consumer | Kepentingan nonpengendali besar |
| MYOR | Consumer | Angka dalam satuan penuh |
| TLKM | Telekomunikasi | Angka dalam miliaran, tanpa laba kotor |
| ASII | Konglomerat | Kepentingan nonpengendali sangat besar |
| ADRO | Tambang | Dolar AS, ganti nama, pelepasan bisnis |
| PTBA | Tambang | Penulisan nama tidak konsisten |
| GOTO | Teknologi | Angka rugi, penurunan nilai goodwill |

### Persiapan

Proyek ini memakai virtual environment khusus di `.venv/` (Python 3.10+), jadi tidak ada pustaka yang terpasang global.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .
```

Di macOS/Linux, ganti `.venv/Scripts/python` dengan `.venv/bin/python`. Perintah kedua memasang dependensi dari `pyproject.toml` dan proyek ini sendiri dalam mode *editable*, sehingga perubahan kode langsung berlaku.

### Menjalankan parser

Taruh zip XBRL dari IDX di `data/raw/` dengan nama `KODE_TAHUN_xbrl.zip` (misalnya `BBCA_2024_xbrl.zip`), lalu jalankan:

```bash
.venv/Scripts/neraca-xbrl
```

Parser XBRL hanya memakai pustaka standar Python. Hasilnya ditulis ke `data/processed/`:

| File | Isi |
|---|---|
| `xbrl_fakta.csv` | Satu baris per angka: emiten, tahun laporan, pos, nilai penuh, mata uang, tahun periode, dimensi, file sumber |
| `xbrl_laporan.csv` | Satu baris per laporan: nama entitas, periode, mata uang, pembulatan, jumlah angka |
| `xbrl_pemeriksaan.csv` | Hasil pemeriksaan konsistensi per laporan |
| `xbrl_konflik.csv` | Pos yang muncul dua kali di satu laporan dengan nilai berbeda |

Nilai disimpan dalam satuan penuh (Rupiah atau Dolar), bukan jutaan. Satu angka bisa muncul di beberapa laporan (misalnya laba 2024 di laporan 2024 dan sebagai pembanding di laporan 2025), jadi kolom `tahun_laporan` menentukan versi mana yang dipakai.

Tes unit:

```bash
.venv/Scripts/python -m unittest discover -s tests
```

### Pemeriksaan konsistensi

| Pemeriksaan | Hasil (48 laporan) |
|---|---|
| Aset = liabilitas + dana syirkah temporer + ekuitas | 96/96 cocok |
| Pendapatan − beban pokok = laba kotor | 56/56 cocok (40 dilewati: bank dan TLKM tidak punya laba kotor) |
| Laba = bagian induk + bagian nonpengendali | 96/96 cocok |
| Saldo awal ekuitas = saldo akhir tahun lalu | 45/48 cocok |

Tiga ketidakcocokan saldo awal ekuitas (BBRI 2022, BBRI 2023, BMRI 2023) berasal dari data IDX sendiri: kolom komponen ekuitas di laporan tahun berjalan dan tahun lalu berbeda, padahal ada di laporan yang sama. Total ekuitasnya tetap seimbang.

### Pelajaran dari data

- **File XBRL dari IDX tidak punya header konteks.** Periode harus dibaca dari nama konteks (`CurrentYearInstant`, `PriorYearDuration`, `Prior2YearsInstant`, dan seterusnya). Artinya diverifikasi dengan mencocokkan saldo kas antarlaporan.
- **Saldo awal dan akhir di laporan perubahan ekuitas memakai konteks yang sama.** Tanggalnya baru jelas dari nama pos (`EquityPositionBeginningOfThePeriod` vs `EquityPositionEndOfThePeriod`).
- **Tag kosong (`xsi:nil="true"`) ditulis tanpa tag penutup.** Pembacaan dengan pola teks menelan angka berikutnya; parser HTML yang benar diperlukan.
- **Empat satuan dan dua mata uang**: penuh (MYOR), ribuan (ADRO, USD), jutaan (kebanyakan), miliaran (TLKM, ASII). Rugi ditandai atribut `sign="-"`, bukan tanda minus di angka.
- **Restatement sering terjadi.** Contoh terbesar: pendapatan ADRO 2023 berubah dari USD 6.518 juta menjadi USD 2.135 juta di laporan 2024 karena pelepasan bisnis.
- **Kode saham lebih andal daripada nama.** ADRO berganti nama, dan nama PTBA ditulis tiga cara berbeda dalam empat tahun.
- **XBRL juga bisa salah tag.** Dua arus kas BBRI 2024 tampak tertukar di laporan 2025, dan kolom 2022 di laporan ekuitas BMRI 2023 mencatat −Rp 16,8 T sebagai "penerapan awal standar akuntansi" tanpa baris dividen.

## Ekstraksi tabel PDF

Tabel laporan keuangan dibaca dari posisi setiap kata di halaman (`pdfplumber`), bukan dari teks per baris. Teks per baris mencampur kolom nomor catatan dengan angka: `Kas 40,43 29.315.878 21.701.514`.

Taruh PDF laporan keuangan audit di `data/raw/` dengan nama `KODE_TAHUN_lk.pdf` (misalnya `BBCA_2024_lk.pdf`), lalu jalankan:

```bash
.venv/Scripts/neraca-pdf data/raw/*_lk.pdf
.venv/Scripts/neraca-cocok data/processed/pdf_*_lk.csv
```

Perintah pertama mencari sendiri halaman neraca, laba rugi, dan arus kas, lalu menghasilkan satu baris per angka: halaman, jenis laporan, label, induk (judul bagian di atasnya), nomor catatan, tahun kolom, mata uang, dan nilai dalam satuan penuh. Halaman tertentu bisa dipilih dengan `--halaman 8-11,14-15`. Perintah kedua mencocokkan hasilnya ke kunci jawaban XBRL; kode emiten dan tahun dibaca dari nama file.

Gunakan PDF laporan keuangan **audit** yang disusun perusahaan, bukan `FinancialStatement-…-Tahunan-KODE.pdf` dari IDX. File IDX itu adalah cetakan data XBRL, sehingga mengujinya sama saja dengan memeriksa kunci jawaban dengan dirinya sendiri.

### Hasil: 12 emiten, laporan tahunan 2024

| Emiten | Angka | Cocok XBRL | Konsisten internal | **Terverifikasi** | Tanpa kunci | Tidak cocok |
|---|---|---|---|---|---|---|
| ADRO | 308 | 94,8% | 2,3% | **97,1%** | – | 2,9% |
| ASII | 293 | 99,7% | – | **99,7%** | – | 0,3% |
| BBCA | 274 | 93,8% | 6,2% | **100,0%** | – | – |
| BBRI | 416 | 67,5% | 14,9% | **82,5%** | 7,9% | 9,6% |
| BMRI | 506 | 85,2% | 9,5% | **94,7%** | – | 5,3% |
| BRIS | 378 | 80,2% | 10,6% | **90,7%** | – | 9,3% |
| GOTO | 282 | 93,6% | 0,7% | **94,3%** | – | 5,7% |
| ICBP | 209 | 99,0% | – | **99,0%** | – | 1,0% |
| MYOR | 204 | 98,0% | 1,0% | **99,0%** | – | 1,0% |
| PTBA | 216 | 95,8% | 3,2% | **99,1%** | – | 0,9% |
| TLKM | 232 | 98,3% | 0,9% | **99,1%** | – | 0,9% |
| UNVR | 165 | 98,2% | – | **98,2%** | – | 1,8% |
| **Total** | **3.483** | **89,7%** | **5,4%** | **95,1%** | 0,9% | 4,0% |

PTBA ditambahkan terakhir dan langsung terbaca tanpa perubahan kode ekstraktor, jadi aturan dari 11 emiten sebelumnya sudah cukup umum.

Arti status:

| Status | Arti |
|---|---|
| Cocok XBRL | Sama persis, sama dengan tanda terbalik (beban dalam kurung), turunan dari 2–3 fakta (misalnya bruto − cadangan, atau giro + tabungan + deposito), gabungan 2–4 baris PDF berurutan yang menjadi satu fakta, atau sama persis dengan angka pembanding di XBRL laporan tahun berikutnya |
| Konsisten internal | Tidak ada di XBRL, tetapi subtotal PDF (`Jumlah`, `Total`, `Bersih`, atau baris tanpa label) sama dengan jumlah baris tepat di atasnya. Subtotal dan penyusunnya diberi status ini, karena satu angka yang salah baca akan membuat penjumlahannya meleset |
| Tanpa kunci | XBRL hampir tidak punya data untuk tahun kolom itu (kolom 2022 di neraca tiga kolom BBRI hanya punya 4 fakta) |
| Tidak cocok | Belum terverifikasi; perlu diperiksa manual |

Angka yang tidak cocok sudah diperiksa per emiten. Yang tersisa hampir semuanya keterbatasan XBRL, bukan salah baca:

- **XBRL bank jauh lebih tipis daripada PDF.** BRIS dan BMRI hanya punya sekitar 90–110 fakta neraca per tahun, sedangkan PDF-nya memuat rincian per pihak dan subtotal bertingkat (`Jumlah simpanan wadiah`, `Jumlah giro, tabungan dan deposito mudharabah`).
- **XBRL BBRI tidak sama dengan PDF audit-nya.** Angka 2023 di XBRL BBRI 2024 identik dengan XBRL BBRI 2023, padahal PDF memakai angka 2023 yang disajikan kembali (`2023*)`). Arus kas 2024 juga berbeda: kas neto dari operasi Rp 24.283.108 juta di PDF vs Rp 24.100.898 juta di XBRL. Keduanya konsisten secara internal, jadi kemungkinan XBRL dibuat dari versi laporan yang berbeda.
- **XBRL PTBA 2024 juga berbeda dari PDF final-nya**, tetapi angka PDF sama persis dengan angka pembanding 2024 di XBRL laporan 2025. Contohnya total aset Rp 41.785.576 juta di PDF dan di XBRL laporan 2025, tetapi Rp 41.854.334 juta di XBRL laporan 2024. Karena itu XBRL laporan tahun berikutnya dipakai sebagai kunci kedua, dengan pencocokan persis saja.
- **XBRL BBCA tidak konsisten di dalam dirinya sendiri.** Halaman catatan (`4631100.html`) masih memuat pendapatan syariah 2023 sebelum restatement (Rp 855.189 juta), sedangkan laba ruginya memakai angka yang disajikan kembali (Rp 663.932 juta).
- **Beberapa pos tidak ditandai atau dikelompokkan ulang**, misalnya laba usaha (MYOR, ICBP, ADRO), EBITDA (UNVR), laba per ADS (TLKM), dan rincian arus kas operasi GOTO.

Batasan: pencocokan memeriksa **nilai**, bukan apakah label PDF sesuai dengan nama pos XBRL. Status turunan, gabungan, dan konsisten internal bisa kebetulan cocok, terutama untuk angka kecil. Laporan perubahan ekuitas belum didukung karena berbentuk lanskap dengan belasan kolom.

### Pelajaran dari PDF

Tata letak yang ditemui di 11 PDF:

| Variasi | Emiten | Penanganan |
|---|---|---|
| Dwibahasa, label Inggris di kanan | Semua kecuali BBCA dan TLKM | Kata di kanan kolom tahun terakhir diabaikan |
| Koma sebagai pemisah ribuan | ADRO, ASII, BRIS, GOTO, UNVR | Format dideteksi per halaman |
| Header dua baris (`Catatan/` + `Notes 2024 2023`) | Laporan dwibahasa | Tahun dicari di sekitar kata `Catatan` |
| Catatan di antara kolom tahun | MYOR | Kolom ditentukan dari posisi header, bukan diasumsikan |
| Angka terpotong jadi dua kata (`4` + `02.141.744.744`, `(` + `34.270…)`) | MYOR, BMRI | Kata bersentuhan disambung |
| Tiga kolom tahun (`2024 2023*) 2022*)`) | BBRI | Tanda setelah tahun diabaikan |
| Tanpa kolom catatan | Arus kas ASII | Header cadangan: baris yang isinya hanya tahun |
| Satuan penuh, ribuan, jutaan, miliaran | MYOR, ADRO, mayoritas, TLKM/ASII | Dibaca dari keterangan satuan per halaman |
| Butir `- Pihak ketiga` | ASII, GOTO, UNVR | Butir selalu memulai pos baru |

Pelajaran lain:

- **Kolom catatan terpotong ke baris di atas labelnya.** `2b,2g,4,37,` ada di atas baris Kas, dan `40,43` sebaris dengan Kas.
- **Jarak vertikal lebih andal daripada huruf kapital.** Label yang terpotong berjarak rapat, sedangkan antarpos ada baris kosong. Dengan petunjuk ini, judul seperti `LABA BERSIH YANG DAPAT DIATRIBUSIKAN` / `KEPADA:` bisa digabung tanpa ikut menggabungkan `LIABILITAS` di bawah `LIABILITAS, DANA SYIRKAH TEMPORER, DAN EKUITAS`. Kurung yang belum ditutup juga menandakan baris lanjutan (`(dalam` / `Rupiah penuh)`).
- **Angka di dalam label bukan nilai**, misalnya `sebesar Rp 638 pada tanggal 31 Desember 2024`.
- **Laba per saham selalu dalam satuan penuh**, meskipun halamannya bersatuan jutaan atau miliaran. Keterangannya bisa ada di label (BBCA) atau di judul induknya (TLKM). Pos yang menyebut nilai nominal per saham (modal saham UNVR) tetap memakai satuan halaman.
- **Kurung negatif bisa terpisah dari angkanya.** Tanpa penanganan, beban MYOR dan BMRI terbaca sebagai pendapatan.
- **Kolom 2024 tidak selalu berarti 31 Desember 2024.** `KAS DAN SETARA KAS, AWAL TAHUN` di kolom 2024 adalah saldo 31 Desember 2023.
- **XBRL menyimpan komponen, PDF menampilkan totalnya**, dan sebaliknya. Karena itu kunci jawaban perlu pencocokan turunan dan gabungan, bukan hanya pencocokan persis.
- **Satu emiten tidak cukup untuk menguji ekstraktor.** Di BBCA saja hasilnya 96,6%, tetapi di awal pengujian 11 emiten ada yang hanya 1,3% (TLKM, karena satuan miliaran tidak terdeteksi) dan 48% (MYOR).

## Normalisasi nama akun

Label di laporan keuangan berbeda-beda antaremiten ("Laba tahun berjalan", "LABA BERSIH", "Laba bersih tahun berjalan"), dan sebagian hanya bermakna dalam konteksnya: "Pemilik entitas induk" bisa berarti laba bersih atau laba komprehensif milik induk, tergantung judul di atasnya. Normalisasi memetakan setiap baris PDF ke salah satu dari **36 pos standar**, misalnya `total_aset`, `laba_bersih_induk`, atau `arus_kas_operasi`.

```bash
.venv/Scripts/neraca-akun data/processed/pdf_*_lk_cocok.csv
```

Hasilnya `data/processed/akun_standar.csv`: satu angka per emiten, tahun, dan pos standar, lengkap dengan mata uang, halaman sumber, label aslinya, dan status verifikasinya. Tabel ini menjadi dasar database jawaban.

### Cara kerja

Pemetaan memakai aturan berurutan atas tiga hal: jenis laporan, label, dan judul di atasnya. Judulnya bisa berupa `induk` (judul yang lebih menjorok ke kiri) atau `konteks` (judul terakhir di halaman, untuk tata letak yang judul dan butirnya sejajar). Contoh aturan yang dibutuhkan data asli:

| Kasus | Aturan |
|---|---|
| "Pemilik entitas induk" di bawah judul laba vs laba komprehensif | Dibedakan dari judulnya |
| "Kepentingan nonpengendali" di neraca vs laba rugi | Dibedakan dari jenis laporan |
| "Penjualan" di bawah judul "Beban usaha" (MYOR) | Beban penjualan, bukan pendapatan |
| "Penghasilan bunga" di perusahaan non-bank | Tidak dipetakan ke pendapatan bunga bank |
| Subtotal tanpa label di bawah "(BEBAN) MANFAAT PAJAK PENGHASILAN" (TLKM) | Mewarisi pos dari judulnya |
| Subtotal tanpa label di bawah "… diatribusikan kepada:" | Sengaja tidak dipetakan: di BMRI isinya laba total, di ADRO jumlah milik induk |
| "Kas bersih … aktivitas operasi" dicetak dua kali (BBCA) | Dipilih kemunculan terakhir (sesudah pajak) |
| "Laba per saham dasar dan dilusian" | Mengisi dua pos |
| Emiten tanpa kepentingan nonpengendali (BRIS, UNVR) | Bagian induk diturunkan dari totalnya, ditandai `turunan_tanpa_nonpengendali` |

### Evaluasi

Setiap pos standar terhubung ke konsep XBRL padanannya. Baris PDF yang nilainya cocok persis dengan XBRL sudah tahu konsepnya, sehingga aturan label bisa dinilai tanpa pelabelan manual:

| Ukuran | Hasil |
|---|---|
| Presisi (pos hasil aturan sesuai konsep XBRL) | **99,7%** (652 benar, 2 salah) |
| Recall per baris | 92,4% (yang terlewat sebagian besar subtotal tanpa label yang posnya sudah tersedia dari baris lain) |
| Cakupan: emiten × tahun × pos yang punya nilai di XBRL | **92,3%** (631 dari 684) |
| Cakupan dari nilai yang memang ada di PDF | **97,8%** (631 dari 645) |

39 dari 684 nilai XBRL tidak ada di PDF sama sekali (misalnya dividen yang dikelompokkan berbeda atau XBRL yang tidak sama dengan PDF final), jadi tidak bisa dipetakan dari PDF. Sisa kesalahannya memang ambigu: XBRL ADRO menandai laba per saham dasar dan dilusian untuk dua set angka yang berbeda, dan GOTO memberi label "Jumlah penghasilan/(beban) komprehensif tahun berjalan" untuk penghasilan komprehensif *lain*.

### Pelajaran

- **Konsep XBRL adalah label gratis.** Pencocokan nilai di tahap sebelumnya sekaligus menghasilkan data latih dan data uji untuk pemetaan label, tanpa perlu melabeli manual.
- **Ukur cakupan, bukan hanya akurasi per baris.** Yang penting untuk tanya-jawab adalah setiap pos tersedia per emiten dan tahun, bukan setiap baris terpetakan.
- **Lebih baik tidak memetakan daripada salah petakan.** Aturan untuk subtotal di bawah "… diatribusikan kepada:" sempat menaikkan recall ke 97%, tetapi salah di ADRO, jadi dihapus.
- **Normalisasi menemukan kesalahan ekstraksi.** Dua kesalahan label baru ketahuan di tahap ini: angka ASII sejajar dengan baris *pertama* label, bukan baris terakhir; dan label bergaya "Huruf Kapital Tiap Kata" yang terpotong setelah kata sambung (ICBP: "… dari Aktivitas" / "Operasi").

## Database

Semua hasil olahan dimuat ke satu file SQLite, `data/processed/neraca.db`. Database ini selalu dibangun ulang dari CSV, jadi tidak pernah diedit manual. Urutan lengkap dari data mentah:

```bash
.venv/Scripts/neraca-xbrl
.venv/Scripts/neraca-pdf data/raw/*_lk.pdf
.venv/Scripts/neraca-cocok data/processed/pdf_*_lk.csv
.venv/Scripts/neraca-akun data/processed/pdf_*_lk_cocok.csv
.venv/Scripts/neraca-db
```

| Tabel | Isi | Baris |
|---|---|---|
| `emiten` | Kode, nama terbaru, sektor, subsektor | 12 |
| `laporan` | Satu baris per laporan: periode, mata uang, pembulatan, file XBRL dan PDF | 48 |
| `pos` | 36 pos standar dan konsep XBRL padanannya | 36 |
| `angka` | **Tabel utama untuk menjawab**: emiten, tahun laporan, tahun, pos, nilai, halaman sumber, status verifikasi | 685 |
| `baris_pdf` | Semua baris PDF dengan label asli, untuk pertanyaan di luar 36 pos | 3.483 |
| `xbrl_fakta` | Fakta XBRL tanpa dimensi, kunci jawaban untuk set pertanyaan uji | 18.049 |

Nilai disimpan sebagai teks desimal supaya tidak ada pembulatan floating point. Kolom `tahun_laporan` membedakan versi angka yang sama: laba 2023 bisa diambil dari laporan 2023 atau, setelah disajikan kembali, dari laporan 2024.

Contoh dari Python:

```python
from decimal import Decimal
from neraca.db import koneksi

con = koneksi()
r = con.execute("""SELECT nilai, mata_uang, halaman FROM angka
                   WHERE emiten = 'BBCA' AND pos = 'laba_bersih_induk'
                     AND tahun = 2024 AND tahun_laporan = 2024""").fetchone()
Decimal(r["nilai"])  # Decimal('54836305000000'), IDR, halaman 11
```

SQLite dipilih untuk tahap ini karena tidak perlu server dan repo bisa langsung dicoba. Semua SQL-nya SQL standar, jadi pindah ke Postgres (misalnya untuk pgvector di jalur naratif) cukup mengganti koneksi dan menjalankan ulang pemuat.

## Alat dan set pertanyaan uji

### Alat

[`src/neraca/alat.py`](src/neraca/alat.py) berisi fungsi yang nanti dipanggil LLM lewat *tool calling*. Semua perhitungan dilakukan di sini dengan `Decimal`; LLM tidak pernah berhitung sendiri.

| Alat | Fungsi |
|---|---|
| `daftar_emiten`, `daftar_pos` | Emiten (sektor, mata uang, tahun laporan) dan 36 pos standar |
| `ambil_angka` | Satu angka beserta sumbernya (PDF dan halaman, atau XBRL) dan status verifikasinya |
| `pertumbuhan` | Selisih dan persen antartahun, dengan kedua angka dari laporan yang sama |
| `rasio` | Margin kotor, margin usaha, margin bersih, ROE, ROA, DER, lengkap dengan definisinya |
| `bandingkan` | Peringkat beberapa emiten menurut pos atau rasio |
| `cari_baris` | Cari label di baris PDF, untuk pos di luar 36 pos standar |

Definisi JSON Schema untuk tool calling ada di `DEFINISI_ALAT`, dan `Alat.jalankan(nama, argumen)` memanggil alat menurut nama, dengan galat dikembalikan sebagai data, bukan exception.

Perilaku yang sengaja dirancang dari temuan sebelumnya:

- **Alasan bila data tidak ada.** "Berapa laba kotor Telkom?" dijawab dengan "Telkom menyajikan beban per jenis, sehingga tidak ada laba kotor", bukan "tidak ditemukan". Hal yang sama untuk pendapatan bank, bunga bank syariah, dan tahun di luar cakupan.
- **Penanda disajikan kembali.** Laba Telkom 2024 dari laporan 2024 otomatis disertai versi yang disajikan kembali di laporan 2025.
- **Dasar sebanding.** `pertumbuhan` memakai laporan tahun terakhir untuk kedua angka, sehingga pendapatan ADRO 2023→2024 dihitung dari USD 2,14 miliar (sudah disajikan kembali), bukan USD 6,52 miliar.
- **Rugi ke rugi.** Bila kedua tahun rugi, persen menyatakan perubahan besarnya rugi (GOTO: −94,30%, rugi menyusut). Bila tandanya berganti, persen tidak dihitung.
- **Mata uang.** `bandingkan` menolak membandingkan nilai absolut ADRO (USD) dengan PTBA (IDR) tanpa kurs; rasio tetap bisa dibandingkan.
- **Pos beban selalu positif**, karena PDF menulisnya dalam kurung sedangkan XBRL positif.

### Set pertanyaan uji

```bash
.venv/Scripts/neraca-uji
```

Perintah ini menulis [`eval/pertanyaan.jsonl`](eval/pertanyaan.jsonl) (33 pertanyaan), lalu menjawab setiap pertanyaan dengan menjalankan **rencana** jawabannya, yaitu urutan panggilan alat yang benar, tanpa LLM. Kunci jawabannya dihitung dari fakta XBRL lewat jalur kode yang terpisah dari alat, sehingga hasil ini menguji alat dan datanya. Nanti agen LLM dinilai dengan kunci yang sama, tetapi tanpa diberi rencananya.

Hasilnya disimpan per sistem penjawab:

| File | Isi |
|---|---|
| `eval/hasil/<sistem>.jsonl` | Satu baris per pertanyaan: jawaban, kunci, benar/salah, keterangan, dan jejak panggilan alat |
| [`eval/hasil/ringkasan.md`](eval/hasil/ringkasan.md) | Semua sistem berdampingan: per kategori, per pertanyaan, dan daftar jawaban yang salah |

Ringkasan dibuat ulang dari semua file `.jsonl` di folder itu setiap kali `neraca-uji` dijalankan, sehingga sistem baru (agen LLM, baseline) otomatis muncul sebagai kolom baru. Isi file tidak memuat waktu, jadi diff git hanya berubah bila hasilnya berubah.

| Kategori | Jumlah | Contoh | Hasil rencana |
|---|---|---|---|
| Lookup | 8 | Total aset BMRI; laba per saham UNVR (satuan penuh); liabilitas ADRO (USD) | 8/8 |
| Perhitungan | 6 | Laba BBCA naik berapa persen; rugi GOTO menyusut berapa persen | 6/6 |
| Rasio | 6 | Margin kotor UNVR; ROE BBRI; DER ASII | 6/6 |
| Perbandingan | 4 | Margin kotor UNVR vs ICBP; bank dengan laba terbesar | 4/4 |
| Jebakan | 7 | Laba kotor TLKM; bunga BRIS; laba ICBP (total atau milik induk); pendapatan ADRO 2023 dua versi; ADRO vs PTBA | 7/7 |
| Tak terjawab | 2 | Total aset 2026; segmen es krim UNVR | 2/2 |
| **Total** | **33** | | **33/33** |

Penilaian: nilai uang benar bila selisihnya di bawah 0,5%, persen dan rasio bila selisihnya di bawah 0,05 poin, dan pertanyaan tak terjawab hanya benar bila sistem menyatakan data tidak tersedia.

**Batasan:** pertanyaan dipilih dari pos yang XBRL-nya bisa dipercaya sebagai kunci. Kasus yang XBRL-nya terbukti berbeda dari PDF (arus kas BBRI 2024, dana syirkah temporer BRIS) tidak dimasukkan, karena di kasus itu jawaban dari PDF justru yang benar. Hasil 33/33 juga hanya berarti alat dan datanya mampu menjawab; kemampuan LLM memilih alat yang tepat belum diukur.

## Struktur folder

```
neraca-qa/
├── data/
│   ├── raw/                  # zip XBRL dan PDF asli dari IDX (tidak di-commit)
│   └── processed/            # hasil olahan, bisa dibuat ulang (tidak di-commit)
├── src/neraca/
│   ├── alat.py               # fungsi alat untuk tool calling LLM
│   ├── db.py                 # pembangun database SQLite
│   ├── ingest/
│   │   └── xbrl.py           # parser XBRL IDX → CSV kunci jawaban
│   ├── extract/
│   │   └── pdf_tabel.py      # ekstraksi tabel PDF dari posisi kata
│   ├── evaluasi/
│   │   ├── pdf_vs_xbrl.py    # akurasi ekstraksi PDF terhadap XBRL
│   │   └── pertanyaan.py     # set pertanyaan uji, kunci dari XBRL, dan penilaian
│   ├── normalisasi/
│   │   └── akun.py           # label PDF → 36 pos standar, dengan evaluasi
│   ├── retrieval/            # (rencana) hybrid search dan reranking
│   ├── tools/                # (rencana) SQL dan fungsi kalkulator
│   └── answer/               # (rencana) router dan penyusunan jawaban
├── tests/
│   ├── test_xbrl.py          # tes parser XBRL
│   ├── test_pdf_tabel.py     # tes ekstraktor PDF dan pencocokan
│   ├── test_akun.py          # tes normalisasi nama akun
│   ├── test_db.py            # tes pembangun database
│   └── test_alat.py          # tes alat dan penilaian
├── eval/
│   ├── pertanyaan.jsonl      # 33 pertanyaan uji dengan kunci dan rencana jawaban
│   └── hasil/                # hasil per sistem penjawab dan ringkasan.md
├── app/                      # (rencana) demo Streamlit
├── pyproject.toml
└── README.md
```
