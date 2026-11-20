"""Ekstraksi tabel laporan keuangan dari PDF berdasarkan posisi kata.

Laporan keuangan emiten berbentuk tabel tanpa garis: label, kolom nomor
catatan, dan satu kolom nilai per tahun; laporan dwibahasa menambahkan label
Inggris di sisi kanan. Tabel dibaca dari posisi setiap kata (``pdfplumber``),
bukan dari teks per baris, karena teks per baris mencampur nomor catatan
(``40,43``) dengan angka.

Aturan yang dipakai (diturunkan dari laporan tahunan 2024 sebelas emiten):

- Header tabel adalah kata ``Catatan`` (atau ``Catatan/``) beserta tahun-tahun
  kolom di baris yang sama atau sedikit di atas/bawahnya (``2024``,
  ``2024/``, ``2023*)``). Posisi kolom diambil dari header, sehingga kolom
  catatan boleh berada di kiri kolom tahun (umum) atau di antaranya (MYOR).
- Kata di kiri kolom pertama adalah label Indonesia. Kata di kanan kolom tahun
  terakhir adalah label Inggris dan diabaikan. Kata lain masuk ke kolom
  terdekat: nomor catatan, atau angka untuk tahun tersebut.
- Format angka dideteksi per halaman: titik (``1.234.567``) atau koma
  (``1,234,567``) sebagai pemisah ribuan. Angka yang terpotong menjadi dua kata
  yang bersentuhan (``4`` + ``.601.449.023.397``) disambung dulu.
- Label yang terpotong ke beberapa baris digabung. Angka bisa sejajar dengan
  baris terakhir label (BBCA) atau baris pertamanya (ASII); baris lanjutan
  sesudah baris bernilai disambung ke label baris itu. Baris lanjutan
  berjarak rapat (tanpa baris kosong) dari baris
  sebelumnya dan diawali huruf kecil, angka, kurung, atau ``Rp``, atau
  merupakan baris kapital semua di bawah baris kapital semua. Baris rapat yang
  diawali huruf kapital biasa, atau butir ``- Pihak ketiga``, memulai pos baru.
- Nomor catatan bisa terpotong ke baris di atas labelnya; baris yang hanya
  berisi nomor catatan disimpan untuk pos berikutnya.
- Baris tanpa nilai yang diikuti pos baru dianggap judul bagian (``ASET``).
  Baris bernilai tanpa label (subtotal) dicatat dengan label kosong.
- Catatan kaki ("... merupakan bagian yang tidak terpisahkan ...") dan semua
  baris di bawahnya diabaikan.
- Kolom ``induk`` berisi judul bagian yang membawahi pos itu, ditentukan dari
  indentasi (misalnya ``Kredit yang diberikan`` untuk ``Pihak ketiga``).
  Kolom ``konteks`` berisi judul terakhir di atas pos itu pada halaman yang
  sama, berapa pun indentasinya; berguna bila judul dan butirnya sejajar
  (``Laba yang dapat diatribusikan kepada:`` / ``Pemilik entitas induk``).

Cara pakai::

    neraca-pdf data/raw/BBCA_2024_lk.pdf                 # cari halaman otomatis
    neraca-pdf data/raw/BBCA_2024_lk.pdf --halaman 8-11  # halaman tertentu
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from pathlib import Path

_STRIP = re.compile(r"^[-–—]$")
_ANGKA = {
    ".": re.compile(r"^\(?-?\d{1,3}(?:\.\d{3})*(?:,\d+)?\)?$"),
    ",": re.compile(r"^\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?$"),
}
_GRUP_RIBUAN = {".": re.compile(r"\d\.\d{3}(?!\d)"), ",": re.compile(r"\d,\d{3}(?!\d)")}
_CATATAN = re.compile(r"^[0-9a-zA-Z().,/]+$")
_TAHUN = re.compile(r"^((?:19|20)\d{2})(?:\*\)?|\*|/|,)*$")
_CATATAN_KAKI = re.compile(
    r"^catatan atas laporan keuangan|merupakan bagian yang tidak terpisahkan|"
    r"^the accompanying notes", re.IGNORECASE)
_SATUAN = (
    (r"miliar|billion", 9),
    (r"jutaan|juta\b|million", 6),
    (r"ribuan|thousand", 3),
)
# Judul dwibahasa bisa terselip kata Inggris: "LAPORAN POSISI CONSOLIDATED STATEMENT OF KEUANGAN".
_SELA = r"(?:\W+\S+){0,5}?\W+"
JENIS_LAPORAN = (
    ("perubahan_ekuitas", re.compile(r"perubahan" + _SELA + r"ekuitas", re.IGNORECASE)),
    ("posisi_keuangan", re.compile(r"posisi" + _SELA + r"keuangan", re.IGNORECASE)),
    ("laba_rugi", re.compile(r"laba" + _SELA + r"rugi", re.IGNORECASE)),
    ("arus_kas", re.compile(r"arus" + _SELA + r"kas", re.IGNORECASE)),
)
_HALAMAN_CATATAN = re.compile(r"catatan\s+atas" + _SELA + r"laporan\s+keuangan", re.IGNORECASE)


@dataclass
class Baris:
    halaman: int
    urutan: int
    label: str
    catatan: str
    jenis: str  # nilai | judul
    induk: str = ""
    konteks: str = ""
    laporan: str = ""
    tahun: int | None = None
    nilai: str = ""
    teks_nilai: str = ""
    skala: int = 0
    mata_uang: str = ""
    y: float = 0.0
    indentasi: float = 0.0


@dataclass
class _Kolom:
    jenis: str  # catatan | tahun
    tengah: float
    tahun: int | None = None


@dataclass
class _Header:
    indeks: int  # indeks baris terakhir header
    kolom: list[_Kolom]
    batas_label: float
    batas_kanan: float


@dataclass
class _Tunda:
    label: list[str] = field(default_factory=list)
    catatan: list[str] = field(default_factory=list)
    x0: float | None = None
    y: float = 0.0
    y_terakhir: float = 0.0


def parse_angka_id(teks: str, ribuan: str = ".") -> Decimal | None:
    """Ubah teks angka laporan keuangan ke Decimal.

    ``ribuan`` adalah pemisah ribuan: ``"."`` untuk ``1.234.567,89`` atau
    ``","`` untuk ``1,234,567.89``. Kurung berarti negatif, strip berarti nol.
    Mengembalikan ``None`` bila teks bukan angka.
    """
    teks = teks.strip()
    if _STRIP.match(teks):
        return Decimal(0)
    if not _ANGKA[ribuan].match(teks):
        return None
    # Kurung buka atau tutup saja sudah cukup: di PDF keduanya kadang terpisah dari angkanya.
    negatif = teks.startswith("(") or teks.endswith(")") or teks.startswith("-")
    bersih = teks.strip("()").lstrip("-")
    desimal = "," if ribuan == "." else "."
    bersih = bersih.replace(ribuan, "").replace(desimal, ".")
    nilai = Decimal(bersih)
    return -nilai if negatif else nilai


def deteksi_format(teks: list[str]) -> str:
    """Tebak pemisah ribuan dari kumpulan teks angka: ``"."`` atau ``","``."""
    hitung = Counter()
    for t in teks:
        for pemisah, pola in _GRUP_RIBUAN.items():
            hitung[pemisah] += len(pola.findall(t))
    return "," if hitung[","] > hitung["."] else "."


_KETERANGAN_SATUAN = re.compile(r"\(([^()]*\b(?:dalam|expressed in|presented in)\b[^()]*)\)",
                                re.IGNORECASE)


def _keterangan_satuan(teks_halaman: str) -> str:
    """Isi kurung keterangan satuan, misalnya ``Angka dalam tabel disajikan dalam miliaran ...``."""
    m = _KETERANGAN_SATUAN.search(" ".join(teks_halaman.split()))
    return m.group(1).lower() if m else ""


def deteksi_skala(teks_halaman: str) -> int:
    """Baca satuan dari keterangan seperti ``(Dalam jutaan Rupiah ...)``."""
    keterangan = _keterangan_satuan(teks_halaman)
    for pola, skala in _SATUAN:
        if re.search(pola, keterangan):
            return skala
    return 0


def deteksi_mata_uang(teks_halaman: str) -> str:
    return "USD" if re.search(r"dolar|dollar", _keterangan_satuan(teks_halaman)) else "IDR"


def jenis_laporan(teks_judul: str) -> str:
    for nama, pola in JENIS_LAPORAN:
        if pola.search(teks_judul):
            return nama
    return ""


def _tengah(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2


def _sambung_angka(baris: list[dict]) -> list[dict]:
    """Sambung kata bersentuhan yang sebenarnya satu angka.

    MYOR menulis angka satuan penuh yang terpotong di sembarang posisi:
    ``4`` + ``.601.449.023.397``, ``4`` + ``02.141.744.744``, atau kurung buka
    yang terpisah, ``(`` + ``34.270.278.782)``. Tanpa penyambungan, tanda
    negatifnya hilang.
    """
    hasil: list[dict] = []
    for w in baris:
        if hasil:
            a = hasil[-1]
            bersentuhan = w["x0"] - a["x1"] <= 0.6
            if bersentuhan and re.fullmatch(r"\(|\(?[\d.,]+", a["text"]) and re.fullmatch(
                    r"[\d.,]+\)?|\)", w["text"]) and not (a["text"] == "(" and w["text"] == ")"):
                hasil[-1] = {**a, "text": a["text"] + w["text"], "x1": w["x1"]}
                continue
        hasil.append(dict(w))
    return hasil


def _kelompokkan_baris(kata: list[dict], toleransi: float = 2.0) -> list[list[dict]]:
    """Kelompokkan kata menjadi baris berdasarkan posisi vertikal."""
    hasil: list[list[dict]] = []
    for w in sorted(kata, key=lambda w: (w["top"], w["x0"])):
        if hasil and abs(hasil[-1][0]["top"] - w["top"]) <= toleransi:
            hasil[-1].append(w)
        else:
            hasil.append([w])
    return [_sambung_angka(sorted(b, key=lambda w: w["x0"])) for b in hasil]


def _header_tanpa_catatan(baris: list[list[dict]], maks_baris: int = 30) -> _Header | None:
    """Header cadangan untuk tabel tanpa kolom catatan (arus kas ASII): baris berisi
    hanya tahun, minimal dua."""
    for i, b in enumerate(baris[:maks_baris]):
        tahun = [w for w in b if _TAHUN.match(w["text"])]
        if len(tahun) >= 2 and len(tahun) == len(b):
            kolom = [_Kolom("tahun", _tengah(w), int(_TAHUN.match(w["text"]).group(1)))
                     for w in tahun]
            return _Header(i, kolom, min(w["x0"] for w in tahun) - 25,
                           max(w["x1"] for w in tahun) + 30)
    return None


def _cari_header(baris: list[list[dict]]) -> _Header | None:
    """Temukan header tabel: kata ``Catatan`` dan tahun-tahun kolom di sekitarnya."""
    for i, b in enumerate(baris):
        catatan = next((w for w in b if w["text"].lower().rstrip("/").startswith("catatan")), None)
        if catatan is None:
            continue
        jendela = [j for j in range(max(0, i - 3), min(len(baris), i + 3))
                   if -22 <= baris[j][0]["top"] - catatan["top"] <= 15]
        tahun: list[tuple[int, dict]] = []
        for j in jendela:
            for w in baris[j]:
                m = _TAHUN.match(w["text"])
                if m and w is not catatan:
                    tahun.append((j, w))
        if not tahun:
            continue
        # gabungkan tahun yang bertumpuk (``2024/`` di atas ``2024``) menjadi satu kolom
        kelompok: list[list[dict]] = []
        for _, w in sorted(tahun, key=lambda t: _tengah(t[1])):
            if kelompok and abs(_tengah(kelompok[-1][-1]) - _tengah(w)) <= 25:
                kelompok[-1].append(w)
            else:
                kelompok.append([w])
        kolom = [_Kolom("tahun", sum(_tengah(w) for w in g) / len(g),
                        int(_TAHUN.match(g[0]["text"]).group(1))) for g in kelompok]
        kolom.append(_Kolom("catatan", _tengah(catatan)))
        kolom.sort(key=lambda k: k.tengah)
        if kolom[0].jenis == "catatan":
            batas_label = catatan["x0"] - 10
        else:
            batas_label = min(w["x0"] for w in kelompok[0]) - 25
        batas_kanan = max(w["x1"] for g in kelompok for w in g) + 30
        indeks = max(j for j, _ in tahun)
        indeks = max(indeks, max(j for j in jendela if any(
            w["text"].lower().startswith("notes") for w in baris[j])) if any(
            w["text"].lower().startswith("notes") for j in jendela for w in baris[j]) else indeks)
        return _Header(max(indeks, i), kolom, batas_label, batas_kanan)
    return _header_tanpa_catatan(baris)


def _kapital_semua(teks: str) -> bool:
    huruf = [c for c in re.sub(r"\([^)]*\)", "", teks) if c.isalpha()]
    return bool(huruf) and all(c.isupper() for c in huruf)


_PER_SAHAM = re.compile(r"\bper\s+(saham|share|ads)\b", re.IGNORECASE)


def _skala_baris(label: str, induk: str, skala_halaman: int) -> int:
    """Pos per saham (laba per saham) disajikan dalam satuan penuh, bukan satuan halaman.

    Keterangan ``(nilai penuh)`` bisa ada di label (BBCA, UNVR) atau di judul
    induknya (TLKM, BBRI). Pos yang menyebut nilai nominal per saham (modal
    saham) tetap memakai satuan halaman.
    """
    teks = f"{label} {induk}"
    if _PER_SAHAM.search(teks) and "nominal" not in teks.lower():
        return 0
    return skala_halaman


_KATA_SAMBUNG = re.compile(
    r"\b(dari|untuk|yang|dan|atau|atas|pada|kepada|dengan|di|ke|oleh|serta|sebagai|"
    r"aktivitas|kegiatan|setelah|sebelum|dikurangi)\)?[,/]?$", re.IGNORECASE)


def _lanjutan(teks: str, sebelumnya: str, rapat: bool) -> bool:
    """Apakah baris label ``teks`` melanjutkan label ``sebelumnya``."""
    if not teks or not rapat:
        return False
    if sebelumnya.count("(") > sebelumnya.count(")"):
        return True  # kurung belum ditutup: "(dalam" / "Rupiah penuh)"
    if _KATA_SAMBUNG.search(sebelumnya):
        return True  # "Kas Neto yang Diperoleh dari Aktivitas" / "Operasi" (ICBP)
    if teks[0].islower() or teks[0].isdigit() or teks[0] == "(" or teks.startswith("Rp "):
        return True
    return _kapital_semua(teks) and _kapital_semua(sebelumnya)


def _lanjutan_sesudah_nilai(teks: str, sebelumnya: str, rapat: bool) -> bool:
    """Versi ketat untuk baris sesudah baris bernilai (pola ASII).

    Baris yang diawali kurung atau huruf kapital di sini biasanya pos baru
    ("(Penurunan)/kenaikan bersih kas" di UNVR), jadi hanya huruf kecil, kurung
    yang belum ditutup, atau kata sambung di akhir label sebelumnya yang dihitung.
    """
    if not teks or not rapat:
        return False
    if sebelumnya.count("(") > sebelumnya.count(")") or _KATA_SAMBUNG.search(sebelumnya):
        return True
    return teks[0].islower()


def ekstrak_halaman(kata: list[dict], halaman: int, teks_halaman: str = "") -> list[Baris]:
    """Ekstrak baris tabel dari daftar kata satu halaman (format ``pdfplumber``)."""
    if not kata:
        return []
    semua = _kelompokkan_baris(kata)
    header = _cari_header(semua)
    if header is None:
        return []
    skala = deteksi_skala(teks_halaman)
    mata_uang = deteksi_mata_uang(teks_halaman)
    judul_atas = " ".join(w["text"] for b in semua[:header.indeks] for w in b)
    laporan = jenis_laporan(judul_atas)
    tinggi = sorted(w["bottom"] - w["top"] for w in kata)[len(kata) // 2]
    jarak_rapat = tinggi * 1.5  # lebih jauh dari ini berarti ada baris kosong
    kolom_tahun = [k for k in header.kolom if k.jenis == "tahun"]

    def dalam_tabel(w: dict) -> bool:
        return header.batas_label <= _tengah(w) < header.batas_kanan

    def kolom_dari(w: dict) -> _Kolom:
        return min(header.kolom, key=lambda k: abs(k.tengah - _tengah(w)))

    badan = semua[header.indeks + 1:]
    ribuan = deteksi_format([w["text"] for b in badan for w in b
                             if dalam_tabel(w) and kolom_dari(w).jenis == "tahun"])

    hasil: list[Baris] = []
    tunda = _Tunda()
    catatan_lepas: list[str] = []  # nomor catatan di baris tanpa label
    judul_aktif: list[tuple[float, str]] = []  # (indentasi, label) judul yang membawahi
    terakhir: list[Baris] = []  # baris bernilai terakhir, untuk label yang berlanjut di bawahnya
    y_terakhir = 0.0
    judul_terakhir: list[Baris] = []  # judul terakhir di halaman, untuk kolom konteks

    def induk_untuk(indentasi: float) -> str:
        judul_aktif[:] = [(x, t) for x, t in judul_aktif if x < indentasi - 1]
        return " > ".join(t for _, t in judul_aktif)

    def simpan_judul() -> None:
        if tunda.label:
            x0, label = tunda.x0 or 0.0, " ".join(tunda.label)
            konteks = judul_terakhir[0].label if judul_terakhir else ""
            judul = Baris(halaman, len(hasil), label, "".join(tunda.catatan), "judul",
                          induk=induk_untuk(x0), konteks=konteks, laporan=laporan, skala=skala,
                          mata_uang=mata_uang, y=tunda.y, indentasi=x0)
            hasil.append(judul)
            judul_aktif.append((x0, label))
            judul_terakhir[:] = [judul]

    for b in badan:
        if _CATATAN_KAKI.search(" ".join(w["text"] for w in b)):
            break
        label = [w for w in b if _tengah(w) < header.batas_label]
        catatan, nilai = [], []
        for w in b:
            if not dalam_tabel(w):
                continue
            kol = kolom_dari(w)
            if kol.jenis == "catatan" and _CATATAN.match(w["text"]):
                catatan.append(w["text"])
            elif kol.jenis == "tahun" and parse_angka_id(w["text"], ribuan) is not None:
                nilai.append((kol, w))

        butir = bool(label) and label[0]["text"] in ("-", "–", "•") and len(label) > 1
        if butir:
            label = label[1:]
        teks_label = " ".join(w["text"] for w in label)

        if not label and not nilai:
            catatan_lepas.extend(catatan)
            continue
        if label and not nilai and not tunda.label and terakhir and terakhir[0].label and \
                _lanjutan_sesudah_nilai(teks_label, terakhir[0].label,
                                        label[0]["top"] - y_terakhir <= jarak_rapat):
            # ASII: angka sejajar baris pertama label, sisa label di bawahnya
            for b_ in terakhir:
                b_.label = f"{b_.label} {teks_label}"
                b_.catatan += "".join(catatan)
            y_terakhir = label[0]["top"]
            continue
        if label:
            terakhir = []
            rapat = label[0]["top"] - tunda.y_terakhir <= jarak_rapat
            if tunda.label and (butir or not _lanjutan(teks_label, tunda.label[-1], rapat)):
                simpan_judul()
                tunda = _Tunda()
            if not tunda.label:
                tunda.x0, tunda.y = label[0]["x0"], label[0]["top"]
            tunda.label.append(teks_label)
            tunda.y_terakhir = label[0]["top"]
        tunda.catatan.extend(catatan_lepas + catatan)
        catatan_lepas = []

        if nilai:
            urut = len(hasil)
            indentasi = tunda.x0 if tunda.x0 is not None else (judul_aktif[-1][0] + 1
                                                               if judul_aktif else 0.0)
            induk = induk_untuk(indentasi)
            label_lengkap = " ".join(tunda.label)
            skala_pos = _skala_baris(label_lengkap, induk, skala)
            terakhir = []
            y_terakhir = label[0]["top"] if label else nilai[0][1]["top"]
            for kol, w in nilai:
                angka = parse_angka_id(w["text"], ribuan)
                terakhir.append(Baris(
                    halaman=halaman, urutan=urut, label=label_lengkap,
                    catatan="".join(tunda.catatan), jenis="nilai", induk=induk,
                    konteks=judul_terakhir[0].label if judul_terakhir else "", laporan=laporan,
                    tahun=kol.tahun, nilai=format(angka.scaleb(skala_pos).normalize(), "f"),
                    teks_nilai=w["text"], skala=skala_pos, mata_uang=mata_uang, y=w["top"],
                    indentasi=indentasi,
                ))
            hasil.extend(terakhir)
            tunda = _Tunda()
    simpan_judul()
    if not kolom_tahun:
        return []
    return hasil


def _baca_halaman(page) -> tuple[list[dict], str]:
    return page.extract_words(x_tolerance=1.5), page.extract_text() or ""


def cari_halaman(path: Path, maks: int = 60) -> list[tuple[int, str]]:
    """Cari halaman neraca, laba rugi, dan arus kas di ``maks`` halaman pertama.

    Halaman dipilih bila punya header tabel (``Catatan`` + tahun) dan judulnya
    menyebut salah satu laporan utama. Laporan perubahan ekuitas dan halaman
    catatan atas laporan keuangan dilewati.
    """
    import pdfplumber

    hasil: list[tuple[int, str]] = []
    with pdfplumber.open(path) as pdf:
        for nomor, page in enumerate(pdf.pages[:maks], start=1):
            kata, _ = _baca_halaman(page)
            if not kata:
                continue
            semua = _kelompokkan_baris(kata)
            header = _cari_header(semua)
            if header is None:
                continue
            judul = " ".join(w["text"] for b in semua[:header.indeks] for w in b)
            jenis = jenis_laporan(judul)
            if jenis and jenis != "perubahan_ekuitas" and not _HALAMAN_CATATAN.search(judul):
                hasil.append((nomor, jenis))
    return hasil


def ekstrak_pdf(path: Path, halaman: list[int] | None = None) -> list[Baris]:
    import pdfplumber  # impor di sini agar modul tetap bisa diuji tanpa PDF

    if halaman is None:
        halaman = [n for n, _ in cari_halaman(path)]
    hasil: list[Baris] = []
    with pdfplumber.open(path) as pdf:
        for nomor in halaman:
            kata, teks = _baca_halaman(pdf.pages[nomor - 1])
            hasil.extend(ekstrak_halaman(kata, nomor, teks))
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
    parser.add_argument("pdf", type=Path, nargs="+", help="file PDF laporan keuangan")
    parser.add_argument("--halaman", help="halaman yang dibaca, misalnya 8-11,14-15 "
                                          "(bawaan: dicari otomatis)")
    parser.add_argument("--out", type=Path, help="file CSV keluaran, hanya untuk satu PDF "
                                                 "(bawaan: data/processed/pdf_<nama>.csv)")
    args = parser.parse_args(argv)
    if args.out and len(args.pdf) > 1:
        parser.error("--out hanya bisa dipakai untuk satu PDF")

    for path in args.pdf:
        halaman = parse_rentang(args.halaman) if args.halaman else None
        if halaman is None:
            temuan = cari_halaman(path)
            halaman = [n for n, _ in temuan]
            ringkas = ", ".join(f"{n}:{j}" for n, j in temuan)
            print(f"{path.name}: halaman ditemukan {ringkas or '-'}")
        baris = ekstrak_pdf(path, halaman)
        out = args.out or Path("data/processed") / f"pdf_{path.stem}.csv"
        tulis_csv(out, baris)
        nilai = [b for b in baris if b.jenis == "nilai"]
        print(f"  {len({(b.halaman, b.urutan) for b in nilai})} baris bernilai, {len(nilai)} angka, "
              f"{sum(b.jenis == 'judul' for b in baris)} judul -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
