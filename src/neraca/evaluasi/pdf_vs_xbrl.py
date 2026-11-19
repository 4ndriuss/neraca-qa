"""Ukur akurasi ekstraksi PDF dengan mencocokkannya ke kunci jawaban XBRL.

Setiap angka hasil ekstraksi PDF dicari di fakta XBRL laporan yang sama
(emiten, tahun laporan, tahun periode) dan diberi status:

- ``persis``: ada fakta XBRL dengan nilai yang sama.
- ``tanda_terbalik``: nilainya sama tetapi tandanya berbeda. PDF menulis beban
  dalam kurung (negatif), sedangkan XBRL sering menyimpannya positif.
- ``turunan``: sama dengan jumlah atau selisih dua fakta, atau ``a + b + c``
  / ``a + b - c`` dari tiga fakta di laporan utama. XBRL menyimpan komponen
  (giro, tabungan, deposito; pihak ketiga, pihak berelasi; cadangan kerugian),
  sedangkan PDF menampilkan jumlah atau nilai bersihnya.
- ``gabungan``: 2-4 baris PDF yang berurutan (di antara baris yang belum
  cocok) jumlahnya sama dengan satu fakta XBRL. Taksonomi XBRL sering
  menggabungkan beberapa pos PDF, misalnya pembelian, penjualan, dan jatuh tempo
  efek-efek menjadi ``OtherCashInflowsOutflowsFromInvestingActivities``.
- ``konsisten_internal``: tidak ada di XBRL, tetapi aritmetika PDF-nya sendiri
  cocok: subtotal (``Jumlah``, ``Total``, ``Bersih``, atau baris tanpa label)
  sama dengan jumlah dua baris atau lebih tepat di atasnya. Subtotal dan baris
  penyusunnya sama-sama diberi status ini, karena satu angka yang salah baca
  akan membuat penjumlahannya meleset. Ini memverifikasi bahwa angka terbaca
  sesuai PDF, bukan bahwa angkanya sama dengan XBRL.
- ``tanpa_kunci``: XBRL hampir tidak punya data untuk tahun kolom ini
  (misalnya kolom 2022 di neraca tiga kolom BBRI).
- ``tidak_cocok``: tidak ditemukan. Bisa salah ekstraksi, bisa juga pos yang
  tidak ditandai di XBRL atau XBRL yang berbeda dari PDF; perlu diperiksa manual.

Pos saldo awal (``KAS DAN SETARA KAS, AWAL TAHUN``) di kolom tahun N
dicocokkan ke saldo akhir tahun N-1.

Cara pakai::

    neraca-cocok data/processed/pdf_BBCA_2024_lk.csv --emiten BBCA --tahun 2024
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
from itertools import combinations
from pathlib import Path

# Halaman XBRL laporan utama: neraca (x2xxxxx), laba rugi (x3xxxxx), arus kas (x5xxxxx).
_LAPORAN_UTAMA = re.compile(r"^\d[235]\d{5}\.html$")
_SALDO_AWAL = re.compile(r"awal (tahun|periode)", re.IGNORECASE)
_SUBTOTAL = re.compile(r"^(jumlah|total|sub\s*-?\s*jumlah|bersih|neto|net)\b|[-–]\s*(bersih|neto)$",
                       re.IGNORECASE)
STATUS_XBRL = ("persis", "tanda_terbalik", "turunan", "gabungan")
STATUS = STATUS_XBRL + ("konsisten_internal", "tanpa_kunci", "tidak_cocok")
MIN_FAKTA_KUNCI = 20  # tahun dengan fakta XBRL lebih sedikit dari ini dianggap tanpa kunci


@dataclass
class FaktaRingkas:
    konsep: str
    nilai: Decimal
    file_html: str


@dataclass
class HasilCocok:
    halaman: int
    urutan: int
    induk: str
    label: str
    tahun: int
    nilai: str
    teks_nilai: str
    status: str
    konsep: str


def muat_xbrl(path: Path, emiten: str, tahun_laporan: int) -> dict[int, list[FaktaRingkas]]:
    """Fakta tanpa dimensi untuk satu laporan, dikelompokkan per tahun periode."""
    hasil: dict[int, list[FaktaRingkas]] = defaultdict(list)
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["emiten"] != emiten or int(r["tahun_laporan"]) != tahun_laporan or r["dimensi"]:
                continue
            hasil[int(r["tahun_periode"])].append(
                FaktaRingkas(r["konsep"], Decimal(r["nilai"]), r["file_html"]))
    return hasil


class Pencocok:
    """Indeks fakta XBRL satu tahun periode untuk pencarian cepat."""

    def __init__(self, fakta: list[FaktaRingkas]) -> None:
        self.per_nilai: dict[Decimal, list[str]] = defaultdict(list)
        for f in fakta:
            if f.konsep not in self.per_nilai[f.nilai]:
                self.per_nilai[f.nilai].append(f.konsep)
        utama: dict[str, Decimal] = {}
        for f in fakta:
            if _LAPORAN_UTAMA.match(f.file_html) and f.nilai != 0:
                utama.setdefault(f.konsep, f.nilai)
        self.utama = list(utama.items())
        self.utama_per_nilai: dict[Decimal, list[str]] = defaultdict(list)
        for k, v in self.utama:
            self.utama_per_nilai[v].append(k)

    def cocokkan(self, nilai: Decimal) -> tuple[str, str]:
        if nilai in self.per_nilai:
            return "persis", " | ".join(self.per_nilai[nilai])
        if nilai != 0 and -nilai in self.per_nilai:
            return "tanda_terbalik", " | ".join(self.per_nilai[-nilai])
        if nilai == 0:
            return "tidak_cocok", ""
        target = abs(nilai)
        for k, v in self.utama:
            for k2 in self.utama_per_nilai.get(target - v, []):
                if k2 != k:
                    return "turunan", f"{k} + {k2}"
            for k2 in self.utama_per_nilai.get(v - target, []):
                if k2 != k:
                    return "turunan", f"{k} - {k2}"
        for (k1, v1), (k2, v2) in combinations(self.utama, 2):
            for k3 in self.utama_per_nilai.get(target - v1 - v2, []):
                if k3 not in (k1, k2):
                    return "turunan", f"{k1} + {k2} + {k3}"
            for k3 in self.utama_per_nilai.get(v1 + v2 - target, []):
                if k3 not in (k1, k2):
                    return "turunan", f"{k1} + {k2} - {k3}"
        return "tidak_cocok", ""

    def cari_nilai(self, nilai: Decimal) -> str | None:
        """Konsep XBRL dengan nilai ini (tanda apa pun), atau None."""
        for v in (nilai, -nilai):
            if v != 0 and v in self.per_nilai:
                return " | ".join(self.per_nilai[v])
        return None


def _cocokkan_gabungan(hasil: list[HasilCocok], pencocok: dict[int, Pencocok],
                       panjang_maks: int = 4) -> None:
    """Tandai deretan baris tak cocok yang jumlahnya ada di XBRL sebagai ``gabungan``.

    Baris tanpa label (subtotal) tidak ikut, karena baris itu sendiri sudah
    berupa penjumlahan dan akan memutus deretan pos yang digabung.
    """
    kelompok: dict[tuple[int, int], list[HasilCocok]] = defaultdict(list)
    for h in hasil:
        if h.status == "tidak_cocok" and h.label:
            kelompok[(h.halaman, h.tahun)].append(h)
    for (_, tahun), deret in kelompok.items():
        deret.sort(key=lambda h: h.urutan)
        i = 0
        while i < len(deret):
            for n in range(min(panjang_maks, len(deret) - i), 1, -1):
                jendela = deret[i:i + n]
                if any(h.status != "tidak_cocok" for h in jendela):
                    continue
                konsep = pencocok[tahun].cari_nilai(sum(Decimal(h.nilai) for h in jendela))
                if konsep:
                    for h in jendela:
                        h.status, h.konsep = "gabungan", f"{konsep} (jumlah {n} baris)"
                    i += n - 1
                    break
            i += 1


def _cek_internal(hasil: list[HasilCocok], maks_baris: int = 25) -> None:
    """Tandai subtotal yang sama dengan jumlah baris tepat di atasnya beserta penyusunnya."""
    kelompok: dict[tuple[int, int], list[HasilCocok]] = defaultdict(list)
    for h in hasil:
        kelompok[(h.halaman, h.tahun)].append(h)
    belum = ("tidak_cocok", "tanpa_kunci")
    for deret in kelompok.values():
        deret.sort(key=lambda h: h.urutan)
        for i, h in enumerate(deret):
            if h.label and not _SUBTOTAL.search(h.label):
                continue
            target, jumlah = Decimal(h.nilai), Decimal(0)
            if target == 0:
                continue
            for k in range(1, min(maks_baris, i) + 1):
                jumlah += Decimal(deret[i - k].nilai)
                if k >= 2 and jumlah == target:
                    bagian = deret[i - k:i]
                    if h.status in belum:
                        h.status, h.konsep = "konsisten_internal", f"= jumlah {k} baris di atas"
                    for b in bagian:
                        if b.status in belum:
                            b.status = "konsisten_internal"
                            b.konsep = f"bagian dari {h.label or 'subtotal'}"
                    break


def cocokkan(pdf_csv: Path, xbrl_csv: Path, emiten: str, tahun_laporan: int) -> list[HasilCocok]:
    xbrl = muat_xbrl(xbrl_csv, emiten, tahun_laporan)
    pencocok: dict[int, Pencocok] = defaultdict(lambda: None)  # type: ignore[arg-type]

    def untuk(tahun: int) -> Pencocok:
        if pencocok[tahun] is None:
            pencocok[tahun] = Pencocok(xbrl.get(tahun, []))
        return pencocok[tahun]

    hasil: list[HasilCocok] = []
    with pdf_csv.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["jenis"] != "nilai":
                continue
            tahun = int(r["tahun"])
            tahun_cari = tahun - 1 if _SALDO_AWAL.search(r["label"]) else tahun
            status, konsep = untuk(tahun_cari).cocokkan(Decimal(r["nilai"]))
            if tahun_cari != tahun and konsep:
                konsep += f" (saldo akhir {tahun_cari})"
            hasil.append(HasilCocok(int(r["halaman"]), int(r["urutan"]), r["induk"], r["label"],
                                    tahun, r["nilai"], r["teks_nilai"], status, konsep))
    for tahun in {h.tahun for h in hasil}:
        untuk(tahun)
    _cocokkan_gabungan(hasil, pencocok)
    for h in hasil:
        if h.status == "tidak_cocok" and len(xbrl.get(h.tahun, [])) < MIN_FAKTA_KUNCI:
            h.status = "tanpa_kunci"
    _cek_internal(hasil)
    return hasil


def ringkasan(hasil: list[HasilCocok]) -> str:
    total = len(hasil)
    hitung = Counter(h.status for h in hasil)
    baris = [f"{total} angka dari PDF dicocokkan ke XBRL:"]
    for status in STATUS:
        n = hitung[status]
        baris.append(f"  {status:15} {n:4}  ({n / total * 100:5.1f}%)" if total else f"  {status} 0")
    xbrl = sum(hitung[s] for s in STATUS_XBRL)
    terverifikasi = xbrl + hitung["konsisten_internal"]
    if total:
        baris.append(f"  cocok XBRL      {xbrl:4}  ({xbrl / total * 100:5.1f}%)")
        baris.append(f"  terverifikasi   {terverifikasi:4}  ({terverifikasi / total * 100:5.1f}%)"
                     "  (XBRL + konsisten internal)")
    per_hal: dict[int, Counter] = defaultdict(Counter)
    for h in hasil:
        per_hal[h.halaman][h.status in STATUS_XBRL + ("konsisten_internal",)] += 1
    baris.append("  per halaman: " + ", ".join(
        f"hal {hal} {c[True]}/{c[True] + c[False]}" for hal, c in sorted(per_hal.items())))
    return "\n".join(baris)


_NAMA_CSV = re.compile(r"^pdf_([A-Z]{4})_(\d{4})_")


def tabel_ringkas(semua: dict[str, list[HasilCocok]]) -> str:
    """Satu baris per emiten: jumlah angka dan persentase per kelompok status."""
    kepala = f"{'emiten':8} {'angka':>6} {'XBRL':>7} {'internal':>9} {'terverif.':>10} " \
             f"{'tanpa kunci':>12} {'tidak cocok':>12}"
    baris = [kepala, "-" * len(kepala)]
    total = Counter()
    for nama, hasil in semua.items():
        c = Counter(h.status for h in hasil)
        n = len(hasil)
        xbrl = sum(c[s] for s in STATUS_XBRL)
        total.update(c)
        total["_n"] += n
        persen = lambda x: f"{x / n * 100:5.1f}%" if n else "  -  "  # noqa: E731
        baris.append(f"{nama:8} {n:6} {persen(xbrl):>7} {persen(c['konsisten_internal']):>9} "
                     f"{persen(xbrl + c['konsisten_internal']):>10} {persen(c['tanpa_kunci']):>12} "
                     f"{persen(c['tidak_cocok']):>12}")
    n = total["_n"]
    xbrl = sum(total[s] for s in STATUS_XBRL)
    if len(semua) > 1 and n:
        baris.append("-" * len(kepala))
        baris.append(f"{'total':8} {n:6} {xbrl / n * 100:6.1f}% {total['konsisten_internal'] / n * 100:8.1f}% "
                     f"{(xbrl + total['konsisten_internal']) / n * 100:9.1f}% "
                     f"{total['tanpa_kunci'] / n * 100:11.1f}% {total['tidak_cocok'] / n * 100:11.1f}%")
    return "\n".join(baris)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("pdf_csv", type=Path, nargs="+", help="hasil neraca-pdf (pdf_KODE_TAHUN_*.csv)")
    parser.add_argument("--emiten", help="kode emiten (bawaan: dari nama file)")
    parser.add_argument("--tahun", type=int, help="tahun laporan (bawaan: dari nama file)")
    parser.add_argument("--xbrl", type=Path, default=Path("data/processed/xbrl_fakta.csv"))
    args = parser.parse_args(argv)

    semua: dict[str, list[HasilCocok]] = {}
    for path in args.pdf_csv:
        if path.stem.endswith("_cocok"):
            continue
        m = _NAMA_CSV.match(path.name)
        emiten = (args.emiten or (m.group(1) if m else "")).upper()
        tahun = args.tahun or (int(m.group(2)) if m else None)
        if not emiten or tahun is None:
            parser.error(f"{path.name}: kode emiten/tahun tidak bisa dibaca dari nama file; "
                         "pakai --emiten dan --tahun")
        hasil = cocokkan(path, args.xbrl, emiten, tahun)
        out = path.with_name(path.stem + "_cocok.csv")
        with out.open("w", newline="", encoding="utf-8") as fh:
            penulis = csv.DictWriter(fh, fieldnames=[f.name for f in fields(HasilCocok)])
            penulis.writeheader()
            penulis.writerows(asdict(h) for h in hasil)
        semua[emiten] = hasil
        if len(args.pdf_csv) == 1:
            print(ringkasan(hasil))
            print(f"Rincian ditulis ke {out}")
    if len(semua) > 1:
        print(tabel_ringkas(semua))
        print("Rincian ditulis ke data/processed/pdf_*_cocok.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
