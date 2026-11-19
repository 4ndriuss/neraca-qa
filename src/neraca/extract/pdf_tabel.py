"""Ekstraksi tabel laporan keuangan dari PDF berdasarkan posisi kata.

Laporan keuangan emiten berbentuk tabel tanpa garis: label di kiri, kolom
nomor catatan, lalu satu kolom nilai per tahun. Tabel dibaca dari posisi
setiap kata (``pdfplumber``), bukan dari teks per baris, karena teks per baris
mencampur nomor catatan (``40,43``) dengan angka.

Aturan yang dipakai (diturunkan dari laporan BBCA 2024):

- Header tabel adalah baris yang memuat kata ``Catatan`` dan tahun-tahun kolom.
- Kata di kiri kolom catatan adalah label; kata di kanan batas kolom nilai
  dimasukkan ke kolom tahun terdekat.
- Label yang terpotong ke beberapa baris digabung sampai ketemu baris yang
  punya nilai. Baris lanjutan berjarak rapat (tanpa baris kosong) dari baris
  sebelumnya dan diawali huruf kecil, angka, kurung, atau ``Rp``, atau
  merupakan baris kapital semua di bawah baris kapital semua (judul seperti
  ``LABA BERSIH YANG DAPAT DIATRIBUSIKAN`` / ``KEPADA:``). Baris rapat yang
  diawali huruf kapital biasa memulai pos baru (``Pihak berelasi``).
- Nomor catatan bisa terpotong ke baris di atas labelnya; baris yang hanya
  berisi nomor catatan disimpan untuk pos berikutnya.
- Baris tanpa nilai yang diikuti pos baru dianggap judul bagian (``ASET``).
- Catatan kaki halaman (``Catatan atas laporan keuangan ...``) dan semua baris
  di bawahnya diabaikan.
- Kolom ``induk`` berisi judul bagian yang membawahi pos itu, ditentukan dari
  indentasi (misalnya ``Kredit yang diberikan`` untuk ``Pihak ketiga``).

Cara pakai::

    neraca-pdf data/raw/BBCA_2024_lk.pdf --halaman 8-11,14-15
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from pathlib import Path

_ANGKA = re.compile(r"^\(?-?\d{1,3}(?:\.\d{3})*(?:,\d+)?\)?$")
_STRIP = re.compile(r"^[-–—]$")
_CATATAN = re.compile(r"^[0-9a-z]+(?:[,.][0-9a-z]+)*[,.]?$")
_TAHUN = re.compile(r"^(19|20)\d{2}$")
_SATUAN = (("miliar", 9), ("jutaan", 6), ("ribuan", 3))


@dataclass
class Baris:
    halaman: int
    urutan: int
    label: str
    catatan: str
    jenis: str  # nilai | judul
    induk: str = ""
    tahun: int | None = None
    nilai: str = ""
    teks_nilai: str = ""
    skala: int = 0
    y: float = 0.0
    indentasi: float = 0.0


@dataclass
class _Kolom:
    tahun: int
    tengah: float


@dataclass
class _Tunda:
    label: list[str] = field(default_factory=list)
    catatan: list[str] = field(default_factory=list)
    x0: float | None = None
    y: float = 0.0
    y_terakhir: float = 0.0


def parse_angka_id(teks: str) -> Decimal | None:
    """Ubah angka format Indonesia (``1.234.567``, ``(1.234)``, ``-``) ke Decimal.

    Mengembalikan ``None`` bila teks bukan angka.
    """
    teks = teks.strip()
    if _STRIP.match(teks):
        return Decimal(0)
    if not _ANGKA.match(teks):
        return None
    negatif = teks.startswith("(") and teks.endswith(")") or teks.startswith("-")
    bersih = teks.strip("()").lstrip("-").replace(".", "").replace(",", ".")
    nilai = Decimal(bersih)
    return -nilai if negatif else nilai


def deteksi_skala(teks_halaman: str) -> int:
    """Baca satuan dari keterangan seperti ``(Dalam jutaan Rupiah ...)``."""
    m = re.search(r"\(Dalam ([^)]*)\)", teks_halaman, re.IGNORECASE)
    if not m:
        return 0
    keterangan = m.group(1).lower()
    for kata, skala in _SATUAN:
        if kata in keterangan:
            return skala
    return 0


def _kelompokkan_baris(kata: list[dict], toleransi: float = 2.0) -> list[list[dict]]:
    """Kelompokkan kata menjadi baris berdasarkan posisi vertikal."""
    hasil: list[list[dict]] = []
    for w in sorted(kata, key=lambda w: (w["top"], w["x0"])):
        if hasil and abs(hasil[-1][0]["top"] - w["top"]) <= toleransi:
            hasil[-1].append(w)
        else:
            hasil.append([w])
    return [sorted(b, key=lambda w: w["x0"]) for b in hasil]


def _cari_header(baris: list[list[dict]]) -> tuple[int, dict, list[_Kolom]] | None:
    for i, b in enumerate(baris):
        catatan = next((w for w in b if w["text"] == "Catatan"), None)
        tahun = [w for w in b if _TAHUN.match(w["text"]) and catatan and w["x0"] > catatan["x1"]]
        if catatan and tahun:
            kolom = [_Kolom(int(w["text"]), (w["x0"] + w["x1"]) / 2) for w in tahun]
            return i, catatan, kolom
    return None


_CATATAN_KAKI = re.compile(r"^Catatan atas laporan keuangan", re.IGNORECASE)


def _kapital_semua(teks: str) -> bool:
    huruf = [c for c in re.sub(r"\([^)]*\)", "", teks) if c.isalpha()]
    return bool(huruf) and all(c.isupper() for c in huruf)


def _skala_baris(label: str, skala_halaman: int) -> int:
    """Pos bertanda ``(nilai penuh)``, misalnya laba per saham, tidak ikut satuan halaman."""
    return 0 if re.search(r"\(nilai penuh\)", label, re.IGNORECASE) else skala_halaman


def _lanjutan(teks: str, sebelumnya: str, rapat: bool) -> bool:
    """Apakah baris label ``teks`` melanjutkan label ``sebelumnya``."""
    if not teks or not rapat:
        return False
    if teks[0].islower() or teks[0].isdigit() or teks[0] in "(-" or teks.startswith("Rp "):
        return True
    return _kapital_semua(teks) and _kapital_semua(sebelumnya)


def ekstrak_halaman(kata: list[dict], halaman: int, teks_halaman: str = "") -> list[Baris]:
    """Ekstrak baris tabel dari daftar kata satu halaman (format ``pdfplumber``)."""
    semua = _kelompokkan_baris(kata)
    header = _cari_header(semua)
    if header is None:
        return []
    idx, kata_catatan, kolom = header
    skala = deteksi_skala(teks_halaman)
    tinggi = sorted(w["bottom"] - w["top"] for w in kata)[len(kata) // 2]
    jarak_rapat = tinggi * 1.5  # lebih jauh dari ini berarti ada baris kosong
    batas_label = kata_catatan["x0"] - 15
    batas_nilai = (kata_catatan["x1"] + min(k.tengah for k in kolom) - 20) / 2

    hasil: list[Baris] = []
    tunda = _Tunda()
    catatan_lepas: list[str] = []  # nomor catatan di baris tanpa label
    judul_aktif: list[tuple[float, str]] = []  # (indentasi, label) judul yang membawahi

    def induk_untuk(indentasi: float) -> str:
        judul_aktif[:] = [(x, t) for x, t in judul_aktif if x < indentasi - 1]
        return " > ".join(t for _, t in judul_aktif)

    def simpan_judul() -> None:
        if tunda.label:
            x0, label = tunda.x0 or 0.0, " ".join(tunda.label)
            hasil.append(Baris(halaman, len(hasil), label, "".join(tunda.catatan), "judul",
                               induk=induk_untuk(x0), skala=skala, y=tunda.y, indentasi=x0))
            judul_aktif.append((x0, label))

    for b in semua[idx + 1:]:
        if _CATATAN_KAKI.match(" ".join(w["text"] for w in b)):
            break
        label = [w for w in b if (w["x0"] + w["x1"]) / 2 < batas_label]
        catatan = [w["text"] for w in b if batas_label <= (w["x0"] + w["x1"]) / 2 < batas_nilai
                   and _CATATAN.match(w["text"])]
        nilai = [w for w in b if (w["x0"] + w["x1"]) / 2 >= batas_nilai
                 and parse_angka_id(w["text"]) is not None]
        teks_label = " ".join(w["text"] for w in label)

        if not label and not nilai:
            catatan_lepas.extend(catatan)
            continue
        if label:
            rapat = label[0]["top"] - tunda.y_terakhir <= jarak_rapat
            if tunda.label and not _lanjutan(teks_label, tunda.label[-1], rapat):
                simpan_judul()
                tunda = _Tunda()
            if not tunda.label:
                tunda.x0, tunda.y = label[0]["x0"], label[0]["top"]
            tunda.label.append(teks_label)
            tunda.y_terakhir = label[0]["top"]
        tunda.catatan.extend(catatan_lepas + catatan)
        catatan_lepas = []

        if nilai and tunda.label:
            urut = len(hasil)
            induk = induk_untuk(tunda.x0 or 0.0)
            label_lengkap = " ".join(tunda.label)
            skala_pos = _skala_baris(label_lengkap, skala)
            for w in nilai:
                tengah = (w["x0"] + w["x1"]) / 2
                kol = min(kolom, key=lambda k: abs(k.tengah - tengah))
                hasil.append(Baris(
                    halaman=halaman, urutan=urut, label=label_lengkap,
                    catatan="".join(tunda.catatan), jenis="nilai", induk=induk, tahun=kol.tahun,
                    nilai=format(parse_angka_id(w["text"]).scaleb(skala_pos).normalize(), "f"),
                    teks_nilai=w["text"], skala=skala_pos, y=w["top"],
                    indentasi=tunda.x0 or 0.0,
                ))
            tunda = _Tunda()
    simpan_judul()
    return hasil


def ekstrak_pdf(path: Path, halaman: list[int]) -> list[Baris]:
    import pdfplumber  # impor di sini agar modul tetap bisa diuji tanpa PDF

    hasil: list[Baris] = []
    with pdfplumber.open(path) as pdf:
        for nomor in halaman:
            page = pdf.pages[nomor - 1]
            kata = page.extract_words(x_tolerance=1.5)
            hasil.extend(ekstrak_halaman(kata, nomor, page.extract_text() or ""))
    return hasil


def parse_rentang(teks: str) -> list[int]:
    """``"8-11,14"`` -> ``[8, 9, 10, 11, 14]``."""
    hasil: list[int] = []
    for bagian in teks.split(","):
        awal, _, akhir = bagian.partition("-")
        hasil.extend(range(int(awal), int(akhir or awal) + 1))
    return hasil


def tulis_csv(path: Path, baris: list[Baris]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        penulis = csv.DictWriter(fh, fieldnames=[f.name for f in fields(Baris)])
        penulis.writeheader()
        penulis.writerows(asdict(b) for b in baris)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("pdf", type=Path, help="file PDF laporan keuangan")
    parser.add_argument("--halaman", required=True, help="halaman yang dibaca, misalnya 8-11,14-15")
    parser.add_argument("--out", type=Path, help="file CSV keluaran (bawaan: data/processed/pdf_<nama>.csv)")
    args = parser.parse_args(argv)

    baris = ekstrak_pdf(args.pdf, parse_rentang(args.halaman))
    out = args.out or Path("data/processed") / f"pdf_{args.pdf.stem}.csv"
    tulis_csv(out, baris)
    nilai = [b for b in baris if b.jenis == "nilai"]
    print(f"{len({(b.halaman, b.urutan) for b in nilai})} baris bernilai, {len(nilai)} angka, "
          f"{sum(b.jenis == 'judul' for b in baris)} judul -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
