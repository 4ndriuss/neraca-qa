# neraca-qa

**Tanya-jawab laporan keuangan emiten Bursa Efek Indonesia (BEI) dengan perhitungan terverifikasi (`Decimal`) dan sitasi halaman.**

> ⚠️ Proyek ini dibuat untuk tujuan edukasi dan portofolio. Jawaban yang dihasilkan **bukan saran investasi**.

---

## Daftar isi

- [Tentang proyek ini](#tentang-proyek-ini)
- [Arsitektur & prinsip utama](#arsitektur--prinsip-utama)
- [Fitur utama](#fitur-utama)
- [Panduan penggunaan](#panduan-penggunaan)
  - [1. Persiapan lingkungan](#1-persiapan-lingkungan)
  - [2. Mengumpulkan data dari IDX](#2-mengumpulkan-data-dari-idx)
  - [3. Menjalankan pipeline data](#3-menjalankan-pipeline-data)
  - [4. Demo web interaktif (Streamlit)](#4-demo-web-interaktif-streamlit)
  - [5. Tanya-jawab lewat CLI (`neraca-tanya`)](#5-tanya-jawab-lewat-cli-neraca-tanya)
  - [6. Menjalankan evaluasi (`neraca-uji`)](#6-menjalankan-evaluasi-neraca-uji)
  - [7. Pemakaian langsung dari Python](#7-pemakaian-langsung-dari-python)
  - [8. Menjalankan tes unit](#8-menjalankan-tes-unit)
- [Cakupan data & hasil verifikasi](#cakupan-data--hasil-verifikasi)
- [Alat & penanganan kasus sulit](#alat--penanganan-kasus-sulit)
- [Struktur folder](#struktur-folder)
- [Rencana pengembangan](#rencana-pengembangan)

---

## Tentang proyek ini

Sistem RAG (*Retrieval-Augmented Generation*) biasa cukup baik untuk teks naratif, tetapi sering gagal saat menghadapi **tabel dan angka laporan keuangan**: salah membaca kolom tahun, salah satuan (jutaan vs miliaran vs penuh), tercampur nomor catatan kaki, atau salah berhitung.

**`neraca-qa`** menyelesaikan masalah tersebut dengan mengekstrak laporan keuangan emiten BEI ke dalam database SQLite terstruktur, memverifikasinya secara otomatis terhadap data resmi **inline XBRL IDX**, lalu menghubungkannya ke agen LLM melalui *tool calling*:

1. **Akurasi angka terukur** — setiap angka diekstrak dari koordinat kata PDF audit dan dicocokkan ke kunci jawaban XBRL (95,1% terverifikasi pada 3.483 angka dari 12 emiten; presisi normalisasi akun 99,7%).
2. **Sitasi sumber yang jelas** — setiap angka menyertakan dokumen dan nomor halaman PDF (atau file XBRL untuk tahun yang belum memiliki PDF).
3. **Menolak dengan alasan spesifik** — alih-alih menebak, sistem menjelaskan penyebab ketika data tidak tersedia (misalnya bank syariah tanpa pendapatan bunga, Telkom tidak menyajikan laba kotor, atau perbandingan nilai absolut lintas mata uang IDR vs USD).

### Contoh interaksi

> **Tanya:** "Laba bersih BCA yang diatribusikan kepada pemilik entitas induk tahun 2024 naik berapa persen dibanding 2023?"
>
> **Jawab:** "Laba bersih BCA (BBCA) yang diatribusikan kepada pemilik entitas induk tahun 2024 naik **12,74%** dibanding 2023 (dari Rp 48,64 triliun menjadi Rp 54,84 triliun, naik Rp 6,20 triliun).
> Sumber: Laporan keuangan 2024, hal. 11."

---

## Arsitektur & prinsip utama

Prinsip kunci proyek ini adalah **LLM tidak boleh berhitung sendiri**. LLM hanya bertugas memahami pertanyaan, memilih alat, dan menyusun kalimat jawaban; seluruh pengambilan data dan perhitungan aritmetika dijalankan di kode Python memakai `Decimal`.

```
  PDF Audit (IDX) ──▶ neraca-pdf ──▶ neraca-cocok ──▶ neraca-akun ──┐
                                           ▲                         ▼
  inlineXBRL.zip  ──▶ neraca-xbrl ─────────┴──────────────────▶ neraca.db (SQLite)
                                                                     │
  Pertanyaan ──▶ Agen LLM (Groq / Gemini) ──▶ Alat (neraca.alat) ────┘
                         │                        ├─ ambil_angka
                         ▼                        ├─ pertumbuhan
                Jawaban + sitasi halaman          ├─ rasio / bandingkan
                + jejak pemanggilan alat          └─ cari_baris
```

---

## Fitur utama

- **Parser inline XBRL IDX (`neraca-xbrl`)** — pustaka standar Python saja; mengurai 48 laporan tahunan (12 emiten × 2022–2025) beserta pemeriksaan konsistensi persamaan akuntansi.
- **Ekstraktor tabel PDF berbasis posisi kata (`neraca-pdf`)** — memakai `pdfplumber` untuk menangani laporan dwibahasa, pemisah ribuan titik/koma, satuan penuh/ribuan/jutaan/miliaran, kurung negatif terpisah, dan label multi-baris.
- **Verifikasi otomatis PDF vs XBRL (`neraca-cocok`)** — mencocokkan nilai persis, tanda terbalik, turunan 2–3 komponen, gabungan baris, angka pembanding laporan berikutnya, hingga konsistensi penjumlahan subtotal internal.
- **Normalisasi ke 36 pos standar (`neraca-akun`)** — memetakan beragam label antaremiten ke 36 kode pos seragam (`total_aset`, `laba_bersih_induk`, `arus_kas_operasi`, dst.) berdasarkan jenis laporan, label, dan judul induknya.
- **Database SQLite (`neraca-db`)** — menyimpan angka sebagai teks desimal tanpa galat *floating point* dengan SQL standar.
- **Agen LLM *provider-agnostic*** — mendukung **Groq** (bawaan, tanpa SDK tambahan, model bawaan `qwen/qwen3.8-27b`) dan **Gemini** (`google-genai`), lengkap dengan keluaran terstruktur lewat alat `jawab_akhir`.
- **Demo web Streamlit** — antarmuka 3 tab: **Tanya Agen LLM (Groq)**, **Kalkulator & Eksplorasi (tanpa LLM)**, serta **Cakupan Data & Evaluasi**.

---

## Panduan penggunaan

Seluruh perintah di bawah dijalankan dari folder akar proyek (`neraca-qa/`). Contoh memakai PowerShell di Windows; di macOS/Linux ganti `.venv\Scripts\` dengan `.venv/bin/`.

### 1. Persiapan lingkungan

Membutuhkan **Python 3.10+**. Buat virtual environment khusus di `.venv/` lalu pasang paket dalam mode *editable*:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[demo]"
```

Pilihan grup dependensi di [`pyproject.toml`](pyproject.toml):
- `pip install -e .` — pipeline data, database, alat, evaluasi, dan agen **Groq** (tanpa SDK tambahan).
- `pip install -e ".[demo]"` — menambahkan **Streamlit** untuk demo web.
- `pip install -e ".[gemini,demo]"` — menambahkan SDK `google-genai` bila ingin memakai adaptor Gemini juga.

Salin `.env.example` menjadi `.env` lalu isi API key:

```powershell
Copy-Item .env.example .env
```

Isi file `.env` (file ini diabaikan git, jangan di-commit):

```env
# API key gratis Groq (untuk chat demo & agen bawaan): https://console.groq.com/keys
GROQ_API_KEY=gsk_...

# Opsional, hanya jika ingin memakai --penyedia gemini / --sistem gemini
GEMINI_API_KEY=
```

### 2. Mengumpulkan data dari IDX

Unduh laporan keuangan tahunan (**Audit**) dari situs [idx.co.id](https://www.idx.co.id) (**Perusahaan Tercatat → Laporan Keuangan dan Tahunan → Laporan Keuangan**), lalu simpan di `data/raw/` dengan pola penamaan:

| Jenis file di IDX | Nama simpan di `data/raw/` | Keterangan |
|---|---|---|
| PDF laporan keuangan **audit** buatan perusahaan/KAP (tebal 100–400+ hal., teks bisa diseleksi) | `KODE_TAHUN_lk.pdf` (mis. `BBCA_2024_lk.pdf`) | **Jangan** pakai `FinancialStatement-…-Tahunan-KODE.pdf` (cetakan XBRL IDX), *Annual Report*, atau *Checklist OJK*. |
| `inlineXBRL.zip` | `KODE_TAHUN_xbrl.zip` (mis. `BBCA_2024_xbrl.zip`) | **Bukan** `instance.zip`; parser membaca halaman `.html` di dalam `inlineXBRL.zip`. |

### 3. Menjalankan pipeline data

Jalankan berurutan dari data mentah di `data/raw/` untuk membangun `data/processed/neraca.db`:

```powershell
# 1. Ekstrak seluruh zip inline XBRL menjadi kunci jawaban CSV + cek konsistensi
.venv\Scripts\neraca-xbrl

# 2. Ekstrak tabel dari PDF laporan keuangan audit (otomatis mendeteksi halaman utama)
.venv\Scripts\neraca-pdf (Get-ChildItem data\raw\*_lk.pdf).FullName
# (opsional untuk 1 PDF dengan halaman tertentu: neraca-pdf data/raw/BBCA_2024_lk.pdf --halaman 8-11,14-15)

# 3. Verifikasi angka hasil ekstraksi PDF terhadap fakta XBRL + cek subtotal internal
.venv\Scripts\neraca-cocok (Get-ChildItem data\processed\pdf_*_lk.csv).FullName

# 4. Normalisasi label PDF ke 36 pos standar + ukur presisi & cakupan
.venv\Scripts\neraca-akun (Get-ChildItem data\processed\pdf_*_lk_cocok.csv).FullName

# 5. Bangun ulang database SQLite (data/processed/neraca.db)
.venv\Scripts\neraca-db
```

### 4. Demo web interaktif (Streamlit)

```powershell
.venv\Scripts\streamlit run app/streamlit_app.py
```

Buka `http://localhost:8501` di browser. Aplikasi menyediakan tiga tab:
- **Tanya Agen LLM**: chat memakai **Groq** (bawaan `qwen/qwen3.8-27b`, juga tersedia `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, atau model kustom), contoh pertanyaan cepat, ringkasan nilai terstruktur, dan panel **Jejak pemanggilan alat**. `GROQ_API_KEY` bisa dibaca dari `.env` atau dimasukkan sementara di sidebar.
- **Kalkulator & Eksplorasi (Tanpa LLM)**: akses langsung ke `ambil_angka`, `pertumbuhan`, `rasio`, `bandingkan` (tabel + grafik batang), dan `cari_baris` — langsung bekerja tanpa API key.
- **Cakupan & Evaluasi**: daftar 12 emiten (sektor, mata uang, tahun XBRL/PDF) dan ringkasan hasil evaluasi dari [`eval/hasil/ringkasan.md`](eval/hasil/ringkasan.md).

### 5. Tanya-jawab lewat CLI (`neraca-tanya`)

```powershell
# Bawaan memakai Groq (qwen/qwen3.8-27b)
.venv\Scripts\neraca-tanya "Laba bersih BCA 2024 naik berapa persen dibanding 2023?"

# Memilih model Groq lain
.venv\Scripts\neraca-tanya --model openai/gpt-oss-120b "Berapa ROE BBRI tahun 2024?"

# Memakai adaptor Gemini (membutuhkan GEMINI_API_KEY dan pip install -e ".[gemini]")
.venv\Scripts\neraca-tanya --penyedia gemini --model gemini-3.5-flash-lite "Total aset BMRI 2024?"
```

### 6. Menjalankan evaluasi (`neraca-uji`)

Set uji berisi **33 pertanyaan** ([`eval/pertanyaan.jsonl`](eval/pertanyaan.jsonl)) dalam 6 kategori (*lookup, perhitungan, rasio, perbandingan, jebakan, tak terjawab*) dengan kunci jawaban yang dihitung independen dari XBRL:

```powershell
# Jalankan oracle "rencana" tanpa LLM (menguji alat & database: 33/33)
.venv\Scripts\neraca-uji

# Coba agen LLM pada beberapa soal saja (tidak menimpa hasil tersimpan)
.venv\Scripts\neraca-uji --sistem groq --id L01,H05,J01

# Evaluasi penuh 33 soal dengan jeda agar aman dari batas TPM tier gratis
.venv\Scripts\neraca-uji --sistem groq --jeda 15
```

Setiap putaran penuh disimpan ke `eval/hasil/<sistem>.jsonl` dan otomatis dirangkum berdampingan di [`eval/hasil/ringkasan.md`](eval/hasil/ringkasan.md).

### 7. Pemakaian langsung dari Python

```python
from neraca.alat import Alat

alat = Alat("data/processed/neraca.db")

# Ambil satu angka + sitasi halaman & info restatement bila ada
print(alat.ambil_angka("BBCA", "laba_bersih_induk", 2024))

# Pertumbuhan YoY (otomatis memakai dasar sebanding dari laporan tahun terakhir)
print(alat.pertumbuhan("BBCA", "laba_bersih_induk", 2023, 2024))

# Rasio keuangan (margin_kotor, margin_usaha, margin_bersih, roe, roa, der)
print(alat.rasio("BBRI", "roe", 2024))

# Peringkat beberapa emiten
print(alat.bandingkan(["BBCA", "BBRI", "BMRI", "BRIS"], "laba_bersih_induk", 2024))

# Pencarian bebas pada baris PDF asli (untuk pos di luar 36 pos standar)
print(alat.cari_baris("UNVR", "royalti", tahun=2024))
```

### 8. Menjalankan tes unit

Seluruh tes unit (termasuk LLM tiruan dan uji *headless* Streamlit tanpa koneksi jaringan/API):

```powershell
.venv\Scripts\python -m unittest discover -s tests
```

---

## Cakupan data & hasil verifikasi

### Emiten (12 emiten × laporan tahunan 2022–2025)

| Emiten | Sektor | Kasus khusus yang diuji |
|---|---|---|
| `BBCA`, `BBRI`, `BMRI` | Bank konvensional | *Restatement*, neraca 3 kolom, tag XBRL tertukar |
| `BRIS` | Bank syariah | Tanpa pendapatan bunga, dana syirkah temporer |
| `UNVR` | Consumer | Operasi yang dihentikan, laba per saham satuan penuh |
| `ICBP` | Consumer | Kepentingan nonpengendali besar |
| `MYOR` | Consumer | Angka dalam satuan penuh, kurung negatif terpisah |
| `TLKM` | Telekomunikasi | Angka dalam miliaran, tanpa laba kotor |
| `ASII` | Konglomerat | Kepentingan nonpengendali besar, tanpa kolom catatan di arus kas |
| `ADRO` | Tambang | Pelaporan USD, ganti nama (Adaro → Alamtri), pelepasan bisnis (*restatement* besar) |
| `PTBA` | Tambang | Penulisan nama entitas tidak konsisten, XBRL 2024 beda dari PDF final |
| `GOTO` | Teknologi | Rugi bersih ke rugi (perubahan besarnya rugi) |

### Ringkasan akurasi pipeline (evaluasi 12 PDF audit 2024 & 48 XBRL)

| Tahap | Metrik | Hasil |
|---|---|---|
| **Konsistensi XBRL** (`neraca-xbrl`) | Neraca seimbang, laba kotor, pembagian laba | 96/96 neraca, 56/56 laba kotor, 96/96 pembagian laba, 45/48 saldo awal ekuitas |
| **Ekstraksi PDF** (`neraca-pdf` + `neraca-cocok`) | Terverifikasi (XBRL + konsisten subtotal internal) | **95,1%** dari 3.483 angka (89,7% cocok XBRL + 5,4% konsisten internal) |
| **Normalisasi akun** (`neraca-akun`) | Presisi pemetaan terhadap konsep XBRL | **99,7%** (652 benar, 2 salah) |
| **Normalisasi akun** (`neraca-akun`) | Cakupan pos standar dari nilai yang ada di PDF | **97,8%** (631 dari 645) |
| **Set uji 33 pertanyaan** (`neraca-uji`) | Oracle `rencana` (batas atas alat & data) | **33/33 (100%)** |

> Detail temuan tiap emiten (termasuk perbedaan XBRL vs PDF audit pada BBRI, PTBA, dan BBCA) dapat ditelusuri di modul evaluasi serta [`eval/hasil/ringkasan.md`](eval/hasil/ringkasan.md).

---

## Alat & penanganan kasus sulit

[`src/neraca/alat.py`](src/neraca/alat.py) menyediakan 7 fungsi untuk *tool calling* LLM: `daftar_emiten`, `daftar_pos`, `ambil_angka`, `pertumbuhan`, `rasio`, `bandingkan`, dan `cari_baris`.

Perilaku yang sengaja dirancang dari temuan data nyata:
- **Alasan jelas saat data tidak ada**: pertanyaan *"laba kotor Telkom"* atau *"pendapatan bunga BSI"* dijawab dengan alasan struktur laporan keuangannya, bukan sekadar "tidak ditemukan".
- **Penanda *restatement***: bila angka tahun N berubah pada laporan tahun N+1, `ambil_angka` otomatis menyertakan blok `disajikan_kembali`.
- **Dasar sebanding antartahun**: `pertumbuhan(awal, akhir)` mengambil kedua angka dari laporan `tahun_akhir` terlebih dahulu agar sudah memakai angka *restated* (contoh: pendapatan ADRO 2023→2024).
- **Rugi ke rugi**: bila kedua tahun negatif, persen menyatakan perubahan besarnya rugi (negatif berarti rugi menyusut, seperti GOTO −94,30%); bila berganti tanda, persentase tidak dihitung.
- **Lintas mata uang**: `bandingkan` menolak membandingkan nilai absolut IDR vs USD tanpa kurs (contoh: ADRO vs PTBA), tetapi tetap mengizinkan perbandingan rasio.
- **Konvensi beban**: pos beban (`beban_pokok_pendapatan`, `beban_bunga`, `beban_pajak`, `dividen_dibayar`) selalu dilaporkan positif agar konsisten antara PDF dan XBRL.
- **Sumber PDF + *fallback* XBRL**: angka PDF (dengan nomor halaman) selalu diutamakan; bila PDF untuk suatu tahun laporan belum tersedia, sistem otomatis mengambil dari XBRL dan menandai sumbernya.

---

## Struktur folder

```text
neraca-qa/
├── app/
│   └── streamlit_app.py        # demo web interaktif (chat Groq + kalkulator tanpa LLM)
├── data/
│   ├── raw/                    # zip inlineXBRL dan PDF audit IDX (diabaikan git)
│   └── processed/              # hasil CSV olahan & neraca.db (diabaikan git)
├── eval/
│   ├── pertanyaan.jsonl        # 33 pertanyaan uji dengan kunci XBRL dan rencana
│   └── hasil/
│       ├── rencana.jsonl       # hasil oracle tanpa LLM
│       └── ringkasan.md        # tabel perbandingan semua sistem penjawab
├── src/neraca/
│   ├── agen/
│   │   ├── __init__.py         # factory buat_agen & CLI neraca-tanya
│   │   ├── inti.py             # loop agen, system prompt, jawab_akhir (provider-agnostic)
│   │   ├── groq.py             # adaptor Groq API (pustaka standar Python, tanpa SDK)
│   │   └── gemini.py           # adaptor Gemini API (google-genai)
│   ├── evaluasi/
│   │   ├── pdf_vs_xbrl.py      # verifikasi ekstraksi PDF terhadap XBRL
│   │   └── pertanyaan.py       # generator set uji, kunci XBRL, dan penilaian
│   ├── extract/
│   │   └── pdf_tabel.py        # ekstraksi tabel PDF berbasis koordinat kata
│   ├── ingest/
│   │   └── xbrl.py             # parser inline XBRL IDX + cek konsistensi
│   ├── normalisasi/
│   │   └── akun.py             # pemetaan label PDF -> 36 pos standar
│   ├── alat.py                 # 7 fungsi tool calling (Decimal) + JSON Schema
│   └── db.py                   # pembangun database SQLite neraca.db
├── tests/
│   ├── test_xbrl.py            # tes parser XBRL
│   ├── test_pdf_tabel.py       # tes ekstraktor PDF dan pencocokan
│   ├── test_akun.py            # tes normalisasi nama akun
│   ├── test_db.py              # tes pembangun database
│   ├── test_alat.py            # tes alat dan penilaian
│   ├── test_agen.py            # tes agen (LLM tiruan, Groq, Gemini)
│   └── test_app.py             # tes demo Streamlit (headless)
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Rencana pengembangan

- Menjalankan evaluasi penuh 33 pertanyaan terhadap agen LLM (Groq & Gemini) dan membandingkannya dengan **baseline RAG teks PDF biasa**.
- Memperluas ekstraksi PDF audit ke seluruh tahun 2022, 2023, dan 2025 (saat ini verifikasi lengkap di database baru mencakup 12 PDF audit 2024 + 48 XBRL 2022–2025).
- Menambahkan **jalur naratif** (hybrid search + reranking atas teks Catatan atas Laporan Keuangan / pembahasan manajemen) dan **router** otomatis antara jalur angka dan jalur naratif.
