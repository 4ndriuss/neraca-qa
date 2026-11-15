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
- [Struktur folder (rencana)](#struktur-folder-rencana)

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

## Struktur folder (rencana)

```
neraca-qa/
├── data/
│   ├── raw/            # PDF laporan keuangan asli
│   └── processed/      # hasil ekstraksi tabel dan teks
├── src/neraca/
│   ├── ingest/         # unduh dan parsing PDF
│   ├── extract/        # ekstraksi dan normalisasi tabel
│   ├── retrieval/      # hybrid search dan reranking
│   ├── tools/          # SQL dan fungsi kalkulator
│   └── answer/         # router dan penyusunan jawaban
├── eval/
│   ├── questions.jsonl # set pertanyaan uji + kunci jawaban
│   └── run_eval.py
├── app/                # demo Streamlit
└── README.md
```
