"""Set pertanyaan uji: pertanyaan, kunci jawaban dari XBRL, dan rencana jawaban.

Kunci jawaban dihitung dari fakta XBRL lewat jalur kode yang terpisah dari
``neraca.alat``, sehingga set ini bisa menguji alat dan datanya. Setiap
pertanyaan juga punya ``rencana``: urutan panggilan alat yang benar. Menjalankan
rencana tanpa LLM mengukur apakah alat dan database bisa menjawab; nanti agen
LLM dinilai dengan kunci yang sama, tanpa diberi rencananya.

Jenis kunci:

- ``angka``: nilai (teks desimal) dan satuan (IDR, USD, persen, kali,
  IDR/saham), opsional ``alternatif`` untuk pertanyaan yang ambigu
- ``pilihan``: kode emiten
- ``tidak_tersedia``: sistem harus menyatakan data tidak tersedia, beserta
  alasan yang diharapkan

Hasil setiap sistem penjawab disimpan ke ``eval/hasil/<sistem>.jsonl`` (satu
baris per pertanyaan: jawaban, benar/salah, keterangan, dan jejak panggilan
alat), lalu semua hasil di folder itu dirangkum berdampingan di
``eval/hasil/ringkasan.md``. Isi file tidak memuat waktu, supaya diff git hanya
berubah bila hasilnya berubah.

Cara pakai::

    neraca-uji            # tulis eval/pertanyaan.jsonl, jalankan rencana, simpan hasil
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from neraca.alat import POS_BEBAN, Alat
from neraca.db import koneksi
from neraca.normalisasi.akun import POS


class KunciXBRL:
    """Pembaca fakta XBRL untuk menghitung kunci jawaban, terpisah dari ``Alat``."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def v(self, emiten: str, pos: str, tahun: int, tl: int | None = None) -> Decimal:
        tl = tl or tahun
        for konsep in POS[pos].kunci:
            r = self.con.execute("SELECT nilai FROM xbrl_fakta WHERE emiten=? AND tahun_laporan=? AND "
                                 "konsep=? AND tahun_periode=? ORDER BY file_html LIMIT 1",
                                 (emiten, tl, konsep, tahun)).fetchone()
            if r is not None:
                nilai = Decimal(r["nilai"])
                return abs(nilai) if pos in POS_BEBAN else nilai
        raise LookupError(f"kunci XBRL tidak ada: {emiten} {pos} {tahun} (laporan {tl})")

    def tumbuh(self, emiten: str, pos: str, awal: int, akhir: int) -> Decimal:
        a, b = self.v(emiten, pos, awal, akhir), self.v(emiten, pos, akhir, akhir)
        if a < 0 and b < 0:
            return (abs(b) / abs(a) - 1) * 100
        return (b / a - 1) * 100

    def rasio(self, emiten: str, pembilang: str, penyebut: str, tahun: int, persen: bool = True) -> Decimal:
        return self.v(emiten, pembilang, tahun) / self.v(emiten, penyebut, tahun) * (100 if persen else 1)


def _angka(nilai: Decimal, satuan: str, *alternatif: Decimal) -> dict[str, Any]:
    if satuan in ("persen", "kali"):
        nilai = nilai.quantize(Decimal("0.0001"))
    kunci = {"tipe": "angka", "nilai": str(nilai), "satuan": satuan}
    if alternatif:
        kunci["alternatif"] = [str(a) for a in alternatif]
    return kunci


def _pilihan(kode: str) -> dict[str, Any]:
    return {"tipe": "pilihan", "nilai": kode}


def _tidak(alasan: str) -> dict[str, Any]:
    return {"tipe": "tidak_tersedia", "alasan": alasan}


def _langkah(alat: str, ambil: str = "nilai", **argumen: Any) -> list[dict[str, Any]]:
    return [{"alat": alat, "argumen": argumen, "ambil": ambil}]


def _terbesar(k: KunciXBRL, emiten: list[str], f: Callable[[str], Decimal]) -> str:
    return max(emiten, key=f)


BANK = ["BBCA", "BBRI", "BMRI", "BRIS"]

# (id, kategori, pertanyaan, fungsi kunci, rencana, catatan)
SPESIFIKASI: list[tuple[str, str, str, Callable[[KunciXBRL], dict], list[dict], str]] = [
    # ---- lookup
    ("L01", "lookup", "Berapa total aset Bank Mandiri pada akhir 2024?",
     lambda k: _angka(k.v("BMRI", "total_aset", 2024), "IDR"),
     _langkah("ambil_angka", emiten="BMRI", pos="total_aset", tahun=2024), ""),
    ("L02", "lookup", "Berapa laba bersih yang diatribusikan kepada pemilik entitas induk BCA tahun 2024?",
     lambda k: _angka(k.v("BBCA", "laba_bersih_induk", 2024), "IDR"),
     _langkah("ambil_angka", emiten="BBCA", pos="laba_bersih_induk", tahun=2024), ""),
    ("L03", "lookup", "Berapa pendapatan Telkom Indonesia tahun 2024?",
     lambda k: _angka(k.v("TLKM", "pendapatan", 2024), "IDR"),
     _langkah("ambil_angka", emiten="TLKM", pos="pendapatan", tahun=2024), "satuan PDF miliaran"),
    ("L04", "lookup", "Berapa kas dan setara kas GoTo pada akhir tahun 2024?",
     lambda k: _angka(k.v("GOTO", "kas_akhir_tahun", 2024), "IDR"),
     _langkah("ambil_angka", emiten="GOTO", pos="kas_akhir_tahun", tahun=2024), ""),
    ("L05", "lookup", "Berapa dana syirkah temporer BCA pada akhir 2024?",
     lambda k: _angka(k.v("BBCA", "dana_syirkah_temporer", 2024), "IDR"),
     _langkah("ambil_angka", emiten="BBCA", pos="dana_syirkah_temporer", tahun=2024),
     "bank konvensional dengan unit syariah"),
    ("L06", "lookup", "Berapa laba per saham dasar Unilever Indonesia tahun 2024?",
     lambda k: _angka(k.v("UNVR", "laba_per_saham_dasar", 2024), "IDR/saham"),
     _langkah("ambil_angka", emiten="UNVR", pos="laba_per_saham_dasar", tahun=2024),
     "satuan penuh, bukan jutaan"),
    ("L07", "lookup", "Berapa arus kas bersih dari aktivitas operasi Astra International tahun 2024?",
     lambda k: _angka(k.v("ASII", "arus_kas_operasi", 2024), "IDR"),
     _langkah("ambil_angka", emiten="ASII", pos="arus_kas_operasi", tahun=2024), ""),
    ("L08", "lookup", "Berapa total liabilitas Alamtri Resources (ADRO) pada akhir 2024?",
     lambda k: _angka(k.v("ADRO", "total_liabilitas", 2024), "USD"),
     _langkah("ambil_angka", emiten="ADRO", pos="total_liabilitas", tahun=2024), "Dolar AS"),
    # ---- perhitungan
    ("H01", "perhitungan", "Laba bersih BCA yang diatribusikan kepada pemilik entitas induk tahun 2024 "
     "naik berapa persen dibanding 2023?",
     lambda k: _angka(k.tumbuh("BBCA", "laba_bersih_induk", 2023, 2024), "persen"),
     _langkah("pertumbuhan", "persen", emiten="BBCA", pos="laba_bersih_induk", tahun_awal=2023,
              tahun_akhir=2024), ""),
    ("H02", "perhitungan", "Pendapatan Indofood CBP tumbuh berapa persen dari 2023 ke 2024?",
     lambda k: _angka(k.tumbuh("ICBP", "pendapatan", 2023, 2024), "persen"),
     _langkah("pertumbuhan", "persen", emiten="ICBP", pos="pendapatan", tahun_awal=2023,
              tahun_akhir=2024), ""),
    ("H03", "perhitungan", "Laba bersih Bukit Asam yang diatribusikan kepada pemilik entitas induk turun "
     "berapa persen dari 2023 ke 2024?",
     lambda k: _angka(k.tumbuh("PTBA", "laba_bersih_induk", 2023, 2024), "persen"),
     _langkah("pertumbuhan", "persen", emiten="PTBA", pos="laba_bersih_induk", tahun_awal=2023,
              tahun_akhir=2024), "hasil negatif"),
    ("H04", "perhitungan", "Berapa kenaikan total aset BSI (BRIS) dari akhir 2023 ke akhir 2024 dalam Rupiah?",
     lambda k: _angka(k.v("BRIS", "total_aset", 2024) - k.v("BRIS", "total_aset", 2023, 2024), "IDR"),
     _langkah("pertumbuhan", "selisih", emiten="BRIS", pos="total_aset", tahun_awal=2023,
              tahun_akhir=2024), "selisih, bukan persen"),
    ("H05", "perhitungan", "Rugi bersih GoTo yang diatribusikan kepada pemilik entitas induk menyusut berapa "
     "persen dari 2023 ke 2024?",
     lambda k: _angka(k.tumbuh("GOTO", "laba_bersih_induk", 2023, 2024), "persen"),
     _langkah("pertumbuhan", "persen", emiten="GOTO", pos="laba_bersih_induk", tahun_awal=2023,
              tahun_akhir=2024), "kedua tahun rugi; persen perubahan besarnya rugi"),
    ("H06", "perhitungan", "Pendapatan bunga (termasuk syariah) BCA tumbuh berapa persen dari 2023 ke 2024?",
     lambda k: _angka(k.tumbuh("BBCA", "pendapatan_bunga", 2023, 2024), "persen"),
     _langkah("pertumbuhan", "persen", emiten="BBCA", pos="pendapatan_bunga", tahun_awal=2023,
              tahun_akhir=2024), "angka 2023 disajikan kembali di laporan 2024"),
    # ---- rasio
    ("R01", "rasio", "Berapa margin laba kotor Unilever Indonesia tahun 2024?",
     lambda k: _angka(k.rasio("UNVR", "laba_kotor", "pendapatan", 2024), "persen"),
     _langkah("rasio", emiten="UNVR", nama="margin_kotor", tahun=2024), ""),
    ("R02", "rasio", "Berapa margin laba bersih Indofood CBP tahun 2024 (laba milik entitas induk dibagi "
     "pendapatan)?",
     lambda k: _angka(k.rasio("ICBP", "laba_bersih_induk", "pendapatan", 2024), "persen"),
     _langkah("rasio", emiten="ICBP", nama="margin_bersih", tahun=2024), ""),
    ("R03", "rasio", "Berapa ROE Bank Rakyat Indonesia tahun 2024?",
     lambda k: _angka(k.rasio("BBRI", "laba_bersih_induk", "ekuitas_induk", 2024), "persen"),
     _langkah("rasio", emiten="BBRI", nama="roe", tahun=2024), "ekuitas akhir tahun"),
    ("R04", "rasio", "Berapa rasio liabilitas terhadap ekuitas (DER) Astra International pada akhir 2024?",
     lambda k: _angka(k.rasio("ASII", "total_liabilitas", "total_ekuitas", 2024, persen=False), "kali"),
     _langkah("rasio", emiten="ASII", nama="der", tahun=2024), ""),
    ("R05", "rasio", "Berapa ROA Telkom Indonesia tahun 2024?",
     lambda k: _angka(k.rasio("TLKM", "laba_bersih", "total_aset", 2024), "persen"),
     _langkah("rasio", emiten="TLKM", nama="roa", tahun=2024), "laba bersih total"),
    ("R06", "rasio", "Berapa margin laba kotor Bukit Asam tahun 2024?",
     lambda k: _angka(k.rasio("PTBA", "laba_kotor", "pendapatan", 2024), "persen"),
     _langkah("rasio", emiten="PTBA", nama="margin_kotor", tahun=2024), ""),
    # ---- perbandingan
    ("P01", "perbandingan", "Mana yang margin laba kotornya lebih tinggi pada 2024, Unilever Indonesia atau "
     "Indofood CBP?",
     lambda k: _pilihan(_terbesar(k, ["UNVR", "ICBP"],
                                  lambda e: k.rasio(e, "laba_kotor", "pendapatan", 2024))),
     _langkah("bandingkan", "peringkat.0.emiten", emiten=["UNVR", "ICBP"], ukuran="margin_kotor",
              tahun=2024), ""),
    ("P02", "perbandingan", "Di antara BCA, BRI, Mandiri, dan BSI, bank mana yang laba bersih milik entitas "
     "induknya terbesar pada 2024?",
     lambda k: _pilihan(_terbesar(k, BANK, lambda e: k.v(e, "laba_bersih_induk", 2024))),
     _langkah("bandingkan", "peringkat.0.emiten", emiten=BANK, ukuran="laba_bersih_induk", tahun=2024), ""),
    ("P03", "perbandingan", "Mana yang margin laba bersihnya lebih tinggi pada 2024, Indofood CBP atau Mayora?",
     lambda k: _pilihan(_terbesar(k, ["ICBP", "MYOR"],
                                  lambda e: k.rasio(e, "laba_bersih_induk", "pendapatan", 2024))),
     _langkah("bandingkan", "peringkat.0.emiten", emiten=["ICBP", "MYOR"], ukuran="margin_bersih",
              tahun=2024), "MYOR melapor dalam satuan penuh"),
    ("P04", "perbandingan", "Mana yang ROE-nya lebih tinggi pada 2024, BCA atau Bank Mandiri?",
     lambda k: _pilihan(_terbesar(k, ["BBCA", "BMRI"],
                                  lambda e: k.rasio(e, "laba_bersih_induk", "ekuitas_induk", 2024))),
     _langkah("bandingkan", "peringkat.0.emiten", emiten=["BBCA", "BMRI"], ukuran="roe", tahun=2024), ""),
    # ---- jebakan
    ("J01", "jebakan", "Berapa laba kotor Telkom Indonesia tahun 2024?",
     lambda k: _tidak("Telkom menyajikan beban per jenis, tanpa laba kotor."),
     _langkah("ambil_angka", emiten="TLKM", pos="laba_kotor", tahun=2024), ""),
    ("J02", "jebakan", "Berapa pendapatan bunga Bank Syariah Indonesia tahun 2024?",
     lambda k: _tidak("Bank syariah tidak memakai bunga."),
     _langkah("ambil_angka", emiten="BRIS", pos="pendapatan_bunga", tahun=2024), ""),
    ("J03", "jebakan", "Berapa laba bersih Indofood CBP tahun 2024?",
     lambda k: _angka(k.v("ICBP", "laba_bersih", 2024), "IDR", k.v("ICBP", "laba_bersih_induk", 2024)),
     _langkah("ambil_angka", emiten="ICBP", pos="laba_bersih", tahun=2024),
     "ambigu: laba total atau milik induk; jawaban yang baik menyebut keduanya"),
    ("J04", "jebakan", "Berapa pendapatan Alamtri Resources (ADRO) tahun 2023 menurut laporan keuangan 2024, "
     "yang sudah disajikan kembali?",
     lambda k: _angka(k.v("ADRO", "pendapatan", 2023, 2024), "USD"),
     _langkah("ambil_angka", emiten="ADRO", pos="pendapatan", tahun=2023, tahun_laporan=2024),
     "bisnis yang dilepas dikeluarkan dari pendapatan"),
    ("J05", "jebakan", "Berapa pendapatan Alamtri Resources (ADRO) tahun 2023 menurut laporan keuangan 2023?",
     lambda k: _angka(k.v("ADRO", "pendapatan", 2023, 2023), "USD"),
     _langkah("ambil_angka", emiten="ADRO", pos="pendapatan", tahun=2023, tahun_laporan=2023),
     "sebelum disajikan kembali; hanya ada di XBRL"),
    ("J06", "jebakan", "Berapa laba bersih Telkom yang diatribusikan kepada pemilik entitas induk tahun 2024 "
     "setelah disajikan kembali di laporan keuangan 2025?",
     lambda k: _angka(k.v("TLKM", "laba_bersih_induk", 2024, 2025), "IDR"),
     _langkah("ambil_angka", emiten="TLKM", pos="laba_bersih_induk", tahun=2024, tahun_laporan=2025),
     "berbeda dari laporan 2024"),
    ("J07", "jebakan", "Mana yang laba bersihnya lebih besar pada 2024, Alamtri Resources (ADRO) atau Bukit "
     "Asam (PTBA)?",
     lambda k: _tidak("Mata uang berbeda (USD dan IDR); perlu kurs untuk membandingkan nilai absolut."),
     _langkah("bandingkan", "peringkat.0.emiten", emiten=["ADRO", "PTBA"], ukuran="laba_bersih_induk",
              tahun=2024), ""),
    # ---- tak terjawab
    ("T01", "tak_terjawab", "Berapa total aset BCA pada akhir 2026?",
     lambda k: _tidak("Data 2026 belum tersedia."),
     _langkah("ambil_angka", emiten="BBCA", pos="total_aset", tahun=2026), ""),
    ("T02", "tak_terjawab", "Berapa pendapatan Unilever Indonesia dari segmen es krim tahun 2024?",
     lambda k: _tidak("Rincian segmen tidak ada di laporan utama yang diekstrak."),
     _langkah("cari_baris", "jumlah", emiten="UNVR", kata="es krim", tahun=2024), ""),
]


def buat_set(con: sqlite3.Connection) -> list[dict[str, Any]]:
    k = KunciXBRL(con)
    return [{"id": i, "kategori": kat, "pertanyaan": tanya, "kunci": f(k), "rencana": rencana,
             "catatan": catatan} for i, kat, tanya, f, rencana, catatan in SPESIFIKASI]


def _ambil_jalur(hasil: Any, jalur: str) -> Any:
    for bagian in jalur.split("."):
        hasil = hasil[int(bagian)] if isinstance(hasil, list) else hasil[bagian]
    return hasil


def jawab_dengan_rencana(alat: Alat, soal: dict[str, Any]) -> dict[str, Any]:
    """Jawab satu pertanyaan dengan menjalankan rencananya (tanpa LLM)."""
    hasil: dict[str, Any] = {}
    for langkah in soal["rencana"]:
        hasil = alat.jalankan(langkah["alat"], langkah["argumen"])
    if "galat" in hasil:
        return {"tipe": "galat", "pesan": hasil["galat"]}
    if hasil.get("tersedia") is False or hasil.get("jumlah") == 0:
        return {"tipe": "tidak_tersedia", "alasan": hasil.get("alasan", "tidak ditemukan")}
    nilai = _ambil_jalur(hasil, soal["rencana"][-1]["ambil"])
    if soal["kunci"]["tipe"] == "pilihan":
        return {"tipe": "pilihan", "nilai": str(nilai)}
    return {"tipe": "angka", "nilai": str(nilai)}


def nilai_jawaban(kunci: dict[str, Any], jawaban: dict[str, Any]) -> tuple[bool, str]:
    """Bandingkan jawaban terstruktur dengan kunci. Mengembalikan (benar, keterangan)."""
    if kunci["tipe"] == "tidak_tersedia":
        return jawaban.get("tipe") == "tidak_tersedia", "harus menyatakan tidak tersedia"
    if jawaban.get("tipe") != kunci["tipe"]:
        return False, f"tipe jawaban {jawaban.get('tipe')!r}, seharusnya {kunci['tipe']!r}"
    if kunci["tipe"] == "pilihan":
        benar = str(jawaban["nilai"]).upper() == kunci["nilai"]
        return benar, f"jawab {jawaban['nilai']}, kunci {kunci['nilai']}"
    prediksi = Decimal(str(jawaban["nilai"]))
    for target in [kunci["nilai"]] + kunci.get("alternatif", []):
        t = Decimal(target)
        if kunci["satuan"] in ("persen", "kali"):
            toleransi = Decimal("0.05")  # poin persen atau kali
        else:
            toleransi = abs(t) * Decimal("0.005")  # 0,5% untuk nilai uang
        if abs(prediksi - t) <= toleransi:
            return True, f"jawab {prediksi}, kunci {t}"
    return False, f"jawab {prediksi}, kunci {kunci['nilai']}"


# --------------------------------------------------------------------------
# Menjalankan sistem penjawab dan menyimpan hasilnya
# --------------------------------------------------------------------------

# Penjawab: menerima satu soal, mengembalikan (jawaban terstruktur, jejak panggilan alat).
Penjawab = Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]]

KETERANGAN_SISTEM = {
    "rencana": "Rencana jawaban yang benar dijalankan langsung, tanpa LLM (batas atas alat dan data)",
}


def penjawab_rencana(alat: Alat) -> Penjawab:
    def jawab(soal: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        jejak = [{"alat": l["alat"], "argumen": l["argumen"]} for l in soal["rencana"]]
        return jawab_dengan_rencana(alat, soal), jejak
    return jawab


def jalankan_uji(soal: list[dict[str, Any]], penjawab: Penjawab, sistem: str) -> list[dict[str, Any]]:
    """Jawab dan nilai setiap soal. Galat penjawab dicatat sebagai jawaban salah."""
    hasil = []
    for s in soal:
        try:
            jawaban, jejak = penjawab(s)
        except Exception as exc:  # satu soal gagal tidak boleh menghentikan seluruh uji
            jawaban, jejak = {"tipe": "galat", "pesan": f"{type(exc).__name__}: {exc}"}, []
        benar, keterangan = nilai_jawaban(s["kunci"], jawaban)
        hasil.append({"id": s["id"], "kategori": s["kategori"], "pertanyaan": s["pertanyaan"],
                      "sistem": sistem, "kunci": s["kunci"], "jawaban": jawaban, "benar": benar,
                      "keterangan": keterangan, "jejak": jejak})
    return hasil


def tulis_jsonl(path: Path, baris: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for b in baris:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")


def baca_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(b) for b in fh if b.strip()]


def _sel(teks: Any) -> str:
    """Teks aman untuk sel tabel Markdown."""
    return " ".join(str(teks).split()).replace("|", "\\|")


def ringkasan_markdown(semua: dict[str, list[dict[str, Any]]]) -> str:
    """Tabel perbandingan: benar per kategori untuk setiap sistem, lalu rincian per soal."""
    sistem = list(semua)
    kategori = list(dict.fromkeys(h["kategori"] for hasil in semua.values() for h in hasil))
    baris = ["# Hasil uji pertanyaan", "",
             "Dibuat otomatis oleh `neraca-uji` dari `eval/hasil/*.jsonl`; jangan diedit manual.", ""]
    baris += ["| Sistem | Keterangan |", "|---|---|"]
    baris += [f"| `{s}` | {KETERANGAN_SISTEM.get(s, '')} |" for s in sistem]
    baris += ["", "## Per kategori", "",
              "| Kategori | " + " | ".join(f"`{s}`" for s in sistem) + " |",
              "|---|" + "---|" * len(sistem)]
    for kat in kategori + ["**Total**"]:
        sel = []
        for s in sistem:
            h = [x for x in semua[s] if kat == "**Total**" or x["kategori"] == kat]
            benar = sum(x["benar"] for x in h)
            teks = f"{benar}/{len(h)}"
            sel.append(f"**{teks} ({benar / len(h):.0%})**" if kat == "**Total**" and h else teks)
        baris.append(f"| {kat} | " + " | ".join(sel) + " |")
    baris += ["", "## Per pertanyaan", "",
              "| ID | Pertanyaan | " + " | ".join(f"`{s}`" for s in sistem) + " |",
              "|---|---|" + "---|" * len(sistem)]
    per_id: dict[str, dict[str, dict[str, Any]]] = {}
    for s, hasil in semua.items():
        for h in hasil:
            per_id.setdefault(h["id"], {})[s] = h
    for i, per_sistem in per_id.items():
        contoh = next(iter(per_sistem.values()))
        sel = [("✅" if per_sistem[s]["benar"] else "❌") if s in per_sistem else "–" for s in sistem]
        baris.append(f"| {i} | {_sel(contoh['pertanyaan'])} | " + " | ".join(sel) + " |")
    gagal = [(s, h) for s, hasil in semua.items() for h in hasil if not h["benar"]]
    if gagal:
        baris += ["", "## Jawaban yang salah", "", "| Sistem | ID | Keterangan |", "|---|---|---|"]
        baris += [f"| `{s}` | {h['id']} | {_sel(h['keterangan'])} |" for s, h in gagal]
    return "\n".join(baris) + "\n"


def tulis_ringkasan(folder: Path) -> Path:
    semua = {p.stem: baca_jsonl(p) for p in sorted(folder.glob("*.jsonl"))}
    # sistem rujukan "rencana" selalu di kolom pertama
    semua = dict(sorted(semua.items(), key=lambda kv: (kv[0] != "rencana", kv[0])))
    path = folder / "ringkasan.md"
    path.write_text(ringkasan_markdown(semua), encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=Path("data/processed/neraca.db"))
    parser.add_argument("--set", type=Path, default=Path("eval/pertanyaan.jsonl"))
    parser.add_argument("--hasil", type=Path, default=Path("eval/hasil"),
                        help="folder hasil per sistem dan ringkasan.md")
    args = parser.parse_args(argv)

    con = koneksi(args.db)
    soal = buat_set(con)
    tulis_jsonl(args.set, soal)
    print(f"{len(soal)} pertanyaan ditulis ke {args.set}")

    hasil = jalankan_uji(soal, penjawab_rencana(Alat(con)), "rencana")
    print("\nMenjalankan rencana jawaban (tanpa LLM):")
    for h in hasil:
        print(f"  {'OK  ' if h['benar'] else 'GAGAL'} {h['id']} {h['pertanyaan'][:62]:62} {h['keterangan']}")
    benar = sum(h["benar"] for h in hasil)
    print(f"\n{benar}/{len(hasil)} benar ({benar / len(hasil):.0%})")
    per_kategori = Counter((h["kategori"], h["benar"]) for h in hasil)
    for kat in dict.fromkeys(h["kategori"] for h in hasil):
        print(f"  {kat:14} {per_kategori[(kat, True)]}/{per_kategori[(kat, True)] + per_kategori[(kat, False)]}")

    tulis_jsonl(args.hasil / "rencana.jsonl", hasil)
    ringkasan = tulis_ringkasan(args.hasil)
    print(f"\nHasil ditulis ke {args.hasil / 'rencana.jsonl'} dan {ringkasan}")
    return 0 if benar == len(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
