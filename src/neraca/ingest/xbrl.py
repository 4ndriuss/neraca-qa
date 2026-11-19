"""Parser inline XBRL laporan keuangan emiten dari IDX.

Zip XBRL yang diunduh dari IDX berisi halaman HTML, satu per laporan (misalnya
``4220000.html`` untuk neraca bank). Setiap angka ditandai ``<ix:nonFraction>``
dan setiap isian teks ditandai ``<ix:nonNumeric>``.

File ini tidak membawa header XBRL (definisi konteks dan unit), sehingga periode
setiap angka dibaca dari nama konteksnya, misalnya ``CurrentYearInstant`` atau
``PriorYearDuration_4410000PY_CommonStocksMember``.

Cara pakai::

    python -m neraca.ingest.xbrl --raw data/raw --out data/processed
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

# Konteks dasar -> (selisih tahun terhadap tahun laporan N, jenis periode).
# Diturunkan dari 48 laporan tahunan: kas akhir tahun di Prior2YearsInstant
# laporan N sama dengan kas akhir tahun di laporan N-2, dan seterusnya.
KONTEKS_DASAR: dict[str, tuple[int, str]] = {
    "CurrentYearInstant": (0, "instant"),
    "CurrentYearDuration": (0, "duration"),
    "PriorEndYearInstant": (-1, "instant"),
    "PriorYearInstant": (-1, "instant"),
    "PriorYearDuration": (-1, "duration"),
    "PriorEndYearDuration": (-1, "duration"),
    "Prior2YearsInstant": (-2, "instant"),
}

# Di laporan perubahan ekuitas, saldo awal dan saldo akhir memakai konteks yang
# sama. Tanggal saldo awal adalah akhir tahun sebelum tanggal konteksnya.
KONSEP_SALDO_AWAL = frozenset(
    {"EquityPositionBeginningOfThePeriod", "BalanceBeforeRestatementAtBeginningOfPeriod"}
)

_KODE_LAPORAN = re.compile(r"\d{7}[A-Za-z]*")
_FORMAT_TANGGAL = ("%B %d, %Y", "%Y-%m-%d", "%d %B %Y")


@dataclass(frozen=True)
class Konteks:
    dasar: str
    selisih_tahun: int
    jenis_periode: str
    kode_laporan: str
    dimensi: str


@dataclass
class Fakta:
    emiten: str
    tahun_laporan: int
    konsep: str
    nilai: str
    mata_uang: str
    unit: str
    jenis_periode: str
    tahun_periode: int
    dimensi: str
    kode_laporan: str
    konteks: str
    skala: int
    desimal: str
    prefix: str
    file_html: str
    file_zip: str
    fact_id: str


@dataclass
class Laporan:
    emiten: str
    nama_entitas: str
    tahun_laporan: int
    tanggal_akhir_periode: str
    periode_penyampaian: str
    mata_uang_penyajian: str
    pembulatan: str
    jenis_laporan: str
    file_zip: str
    jumlah_fakta: int
    jumlah_kosong: int
    jumlah_konflik: int


@dataclass
class Konflik:
    """Pos yang sama muncul dua kali di satu laporan dengan nilai berbeda."""

    emiten: str
    tahun_laporan: int
    konsep: str
    konteks: str
    nilai_pertama: str
    file_pertama: str
    nilai_kedua: str
    file_kedua: str


def parse_konteks(nama: str) -> Konteks:
    """Uraikan nama konteks IDX menjadi periode, kode laporan, dan dimensi."""
    dasar, _, sisa = nama.partition("_")
    if dasar not in KONTEKS_DASAR:
        raise ValueError(f"konteks tidak dikenal: {nama!r}")
    selisih, jenis = KONTEKS_DASAR[dasar]
    bagian = sisa.split("_") if sisa else []
    kode = bagian.pop(0) if bagian and _KODE_LAPORAN.fullmatch(bagian[0]) else ""
    return Konteks(dasar, selisih, jenis, kode, "|".join(bagian))


def parse_angka(teks: str, skala: str | None, tanda: str | None, fmt: str | None) -> Decimal:
    """Ubah teks angka IDX menjadi nilai penuh (satuan terkecil mata uang)."""
    bersih = teks.strip()
    if fmt is None or fmt.endswith("numcommadot"):
        bersih = bersih.replace(",", "")
    elif fmt.endswith("numdotcomma"):
        bersih = bersih.replace(".", "").replace(",", ".")
    else:
        raise ValueError(f"format angka tidak didukung: {fmt!r}")
    try:
        nilai = Decimal(bersih)
    except InvalidOperation as exc:
        raise ValueError(f"angka tidak valid: {teks!r}") from exc
    nilai = nilai.scaleb(int(skala or 0))
    return -nilai if tanda == "-" else nilai


def format_angka(nilai: Decimal) -> str:
    teks = format(nilai.normalize(), "f")
    return "0" if teks in ("-0", "") else teks


class _PembacaIx(HTMLParser):
    """Kumpulkan isi tag ``ix:nonfraction`` dan ``ix:nonnumeric``.

    Tag kosong (``<ix:nonFraction ... xsi:nil="true"/>``) diproses lewat
    ``handle_startendtag`` bawaan, sehingga tidak menelan angka berikutnya.
    Tag lain di dalam nilai (misalnya ``<span>``) diabaikan; teksnya ikut.
    """

    TAG = ("ix:nonfraction", "ix:nonnumeric")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.angka: list[tuple[dict[str, str], str]] = []
        self.teks: list[tuple[dict[str, str], str]] = []
        self._tag: str | None = None
        self._attrs: dict[str, str] = {}
        self._buf: list[str] = []
        self._kedalaman = 0

    def handle_starttag(self, tag, attrs):
        if self._tag is not None:
            if tag == self._tag:
                self._kedalaman += 1
            return
        if tag in self.TAG:
            self._tag = tag
            self._attrs = {k: (v or "") for k, v in attrs}
            self._buf = []
            self._kedalaman = 0

    def handle_endtag(self, tag):
        if self._tag is None or tag != self._tag:
            return
        if self._kedalaman:
            self._kedalaman -= 1
            return
        isi = " ".join("".join(self._buf).split())
        tujuan = self.angka if self._tag == "ix:nonfraction" else self.teks
        tujuan.append((self._attrs, isi))
        self._tag = None

    def handle_data(self, data):
        if self._tag is not None:
            self._buf.append(data)


def baca_html(html: str) -> _PembacaIx:
    pembaca = _PembacaIx()
    pembaca.feed(html)
    pembaca.close()
    return pembaca


def _nama_lokal(nama: str) -> tuple[str, str]:
    prefix, _, lokal = nama.rpartition(":")
    return prefix, lokal


def _tahun_dari_tanggal(teks: str) -> tuple[int, str]:
    for fmt in _FORMAT_TANGGAL:
        try:
            tanggal = datetime.strptime(teks.strip(), fmt).date()
            return tanggal.year, tanggal.isoformat()
        except ValueError:
            continue
    raise ValueError(f"tanggal tidak dikenali: {teks!r}")


def _info_umum(html: str) -> dict[str, str]:
    """Ambil isian teks dari halaman informasi umum (1000000.html)."""
    info: dict[str, str] = {}
    for attrs, isi in baca_html(html).teks:
        if attrs.get("xsi:nil") == "true" or not isi:
            continue
        info.setdefault(_nama_lokal(attrs.get("name", ""))[1], isi)
    return info


def baca_zip(path: Path) -> tuple[Laporan, list[Fakta], list[Konflik]]:
    """Baca satu zip XBRL IDX menjadi metadata laporan dan daftar fakta angka."""
    with zipfile.ZipFile(path) as zf:
        halaman = {
            nama: zf.read(nama).decode("utf-8", errors="replace")
            for nama in sorted(zf.namelist())
            if nama.lower().endswith((".html", ".htm"))
        }

    info_umum = next((h for n, h in halaman.items() if n.startswith("1000000")), None)
    if info_umum is None:
        raise ValueError(f"{path.name}: halaman informasi umum (1000000.html) tidak ada")
    info = _info_umum(info_umum)
    try:
        emiten = info["EntityCode"].strip().upper()
        tahun, tanggal = _tahun_dari_tanggal(info["CurrentPeriodEndDate"])
    except KeyError as exc:
        raise ValueError(f"{path.name}: isian {exc.args[0]} tidak ditemukan") from exc

    fakta: dict[tuple[str, str], Fakta] = {}
    konflik: list[Konflik] = []
    kosong = 0
    for nama_file, html in halaman.items():
        for attrs, isi in baca_html(html).angka:
            if attrs.get("xsi:nil") == "true" or not isi:
                kosong += 1
                continue
            prefix, konsep = _nama_lokal(attrs["name"])
            ctx = parse_konteks(attrs["contextref"])
            nilai = format_angka(
                parse_angka(isi, attrs.get("scale"), attrs.get("sign"), attrs.get("format"))
            )
            tahun_periode = tahun + ctx.selisih_tahun
            if ctx.jenis_periode == "instant" and konsep in KONSEP_SALDO_AWAL:
                tahun_periode -= 1
            unit = attrs.get("unitref", "")
            baru = Fakta(
                emiten=emiten,
                tahun_laporan=tahun,
                konsep=konsep,
                nilai=nilai,
                mata_uang=unit[:3],
                unit=unit,
                jenis_periode=ctx.jenis_periode,
                tahun_periode=tahun_periode,
                dimensi=ctx.dimensi,
                kode_laporan=ctx.kode_laporan,
                konteks=attrs["contextref"],
                skala=int(attrs.get("scale") or 0),
                desimal=attrs.get("decimals", ""),
                prefix=prefix,
                file_html=nama_file,
                file_zip=path.name,
                fact_id=attrs.get("id", ""),
            )
            kunci = (konsep, attrs["contextref"])
            lama = fakta.get(kunci)
            if lama is None:
                fakta[kunci] = baru
            elif Decimal(lama.nilai) != Decimal(baru.nilai):
                konflik.append(
                    Konflik(emiten, tahun, konsep, baru.konteks,
                            lama.nilai, lama.file_html, baru.nilai, nama_file)
                )

    laporan = Laporan(
        emiten=emiten,
        nama_entitas=info.get("EntityName", ""),
        tahun_laporan=tahun,
        tanggal_akhir_periode=tanggal,
        periode_penyampaian=info.get("PeriodOfFinancialStatementsSubmissions", ""),
        mata_uang_penyajian=info.get("DescriptionOfPresentationCurrency", ""),
        pembulatan=info.get("LevelOfRoundingUsedInFinancialStatements", ""),
        jenis_laporan=info.get("TypeOfReportOnFinancialStatements", ""),
        file_zip=path.name,
        jumlah_fakta=len(fakta),
        jumlah_kosong=kosong,
        jumlah_konflik=len(konflik),
    )
    return laporan, list(fakta.values()), konflik


# --------------------------------------------------------------------------
# Pemeriksaan konsistensi
# --------------------------------------------------------------------------

@dataclass
class HasilPeriksa:
    emiten: str
    tahun_laporan: int
    pemeriksaan: str
    tahun_periode: int
    status: str  # ok | tidak_cocok | lewati
    detail: str


def _toleransi(*fakta: Fakta) -> Decimal:
    """Toleransi pembulatan: beberapa unit dari skala terbesar yang dipakai."""
    skala = max((f.skala for f in fakta if f is not None), default=0)
    return Decimal(3).scaleb(skala)


def periksa(daftar: list[Fakta]) -> list[HasilPeriksa]:
    """Periksa konsistensi angka di dalam setiap laporan.

    - neraca: aset = liabilitas + dana syirkah temporer + ekuitas
    - laba_kotor: pendapatan - beban pokok = laba kotor
    - pembagian_laba: laba = bagian entitas induk + bagian nonpengendali
    - saldo_awal_ekuitas: saldo awal tahun N = saldo akhir tahun N-1 di laporan
      perubahan ekuitas yang sama (menguji aturan tanggal saldo awal). Di antara
      saldo awal sebelum penyesuaian (``BalanceBeforeRestatementAtBeginningOfPeriod``)
      dan sesudahnya (``EquityPositionBeginningOfThePeriod``) bisa ada penerapan
      standar baru atau penyajian kembali, jadi cukup salah satunya yang cocok.
    """
    per_laporan: dict[tuple[str, int], list[Fakta]] = defaultdict(list)
    for f in daftar:
        per_laporan[(f.emiten, f.tahun_laporan)].append(f)

    hasil: list[HasilPeriksa] = []
    for (emiten, tahun), fakta in sorted(per_laporan.items()):
        tanpa_dimensi = {
            (f.konsep, f.jenis_periode, f.tahun_periode): f
            for f in fakta
            if not f.dimensi and f.mata_uang and "PerShares" not in f.unit
        }

        def ambil(konsep: str, jenis: str, th: int) -> Fakta | None:
            return tanpa_dimensi.get((konsep, jenis, th))

        def catat(nama: str, th: int, kiri, kanan, uraian: str, *dipakai: Fakta | None) -> None:
            if kiri is None or kanan is None:
                hasil.append(HasilPeriksa(emiten, tahun, nama, th, "lewati", "data tidak lengkap"))
                return
            selisih = kiri - kanan
            status = "ok" if abs(selisih) <= _toleransi(*dipakai) else "tidak_cocok"
            hasil.append(HasilPeriksa(emiten, tahun, nama, th, status,
                                      f"{uraian}; selisih {format_angka(selisih)}"))

        for th in (tahun, tahun - 1):
            a = ambil("Assets", "instant", th)
            l = ambil("Liabilities", "instant", th)
            s = ambil("TemporarySyirkahFunds", "instant", th)
            e = ambil("Equity", "instant", th)
            kanan = None
            if l is not None and e is not None:
                kanan = Decimal(l.nilai) + Decimal(e.nilai) + (Decimal(s.nilai) if s else 0)
            catat("neraca", th, Decimal(a.nilai) if a else None, kanan,
                  "aset vs liabilitas + syirkah + ekuitas", a, l, s, e)

            r = ambil("SalesAndRevenue", "duration", th)
            c = ambil("CostOfSalesAndRevenue", "duration", th)
            g = ambil("GrossProfit", "duration", th)
            if r is None and g is None:
                hasil.append(HasilPeriksa(emiten, tahun, "laba_kotor", th, "lewati",
                                          "laporan tanpa laba kotor"))
            else:
                kiri = Decimal(r.nilai) - Decimal(c.nilai) if r and c else None
                catat("laba_kotor", th, kiri, Decimal(g.nilai) if g else None,
                      "pendapatan - beban pokok vs laba kotor", r, c, g)

            p = ambil("ProfitLoss", "duration", th)
            pi = ambil("ProfitLossAttributableToParentEntity", "duration", th)
            pn = ambil("ProfitLossAttributableToNonControllingInterests", "duration", th)
            kanan = Decimal(pi.nilai) + (Decimal(pn.nilai) if pn else 0) if pi else None
            catat("pembagian_laba", th, Decimal(p.nilai) if p else None, kanan,
                  "laba vs induk + nonpengendali", p, pi, pn)

        awal: dict[tuple[str, int], list[Fakta]] = defaultdict(list)
        for f in fakta:
            if f.konsep in KONSEP_SALDO_AWAL:
                awal[(f.dimensi, f.tahun_periode)].append(f)
        akhir = {(f.dimensi, f.tahun_periode): f for f in fakta
                 if f.konsep == "EquityPositionEndOfThePeriod"}
        cocok = [k for k in awal if k in akhir]
        beda = [k for k in cocok if not any(
            abs(Decimal(a.nilai) - Decimal(akhir[k].nilai)) <= _toleransi(a, akhir[k])
            for a in awal[k])]
        if not cocok:
            hasil.append(HasilPeriksa(emiten, tahun, "saldo_awal_ekuitas", tahun - 1,
                                      "lewati", "tidak ada pasangan saldo awal/akhir"))
        else:
            contoh = "" if not beda else "; beda di " + ", ".join(
                f"{d or 'total'} {th}" for d, th in beda[:3])
            hasil.append(HasilPeriksa(
                emiten, tahun, "saldo_awal_ekuitas", tahun - 1,
                "tidak_cocok" if beda else "ok",
                f"{len(cocok) - len(beda)} dari {len(cocok)} kolom cocok{contoh}"))
    return hasil


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _tulis_csv(path: Path, baris: list, kelas) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        penulis = csv.DictWriter(fh, fieldnames=[f.name for f in fields(kelas)])
        penulis.writeheader()
        penulis.writerows(asdict(b) for b in baris)


def jalankan(raw: Path, out: Path, pola: str = "*_xbrl.zip", ketat: bool = False) -> int:
    """Proses semua zip di ``raw`` dan tulis CSV ke ``out``.

    Ketidakcocokan hasil pemeriksaan adalah temuan tentang data IDX, bukan
    kegagalan parser, sehingga hanya memengaruhi kode keluar bila ``ketat``.
    """
    zips = sorted(raw.glob(pola))
    if not zips:
        print(f"Tidak ada file {pola} di {raw}", file=sys.stderr)
        return 1

    semua_laporan: list[Laporan] = []
    semua_fakta: list[Fakta] = []
    semua_konflik: list[Konflik] = []
    for zp in zips:
        laporan, fakta, konflik = baca_zip(zp)
        semua_laporan.append(laporan)
        semua_fakta.extend(fakta)
        semua_konflik.extend(konflik)

    hasil = periksa(semua_fakta)
    _tulis_csv(out / "xbrl_laporan.csv", semua_laporan, Laporan)
    _tulis_csv(out / "xbrl_fakta.csv", semua_fakta, Fakta)
    _tulis_csv(out / "xbrl_konflik.csv", semua_konflik, Konflik)
    _tulis_csv(out / "xbrl_pemeriksaan.csv", hasil, HasilPeriksa)

    emiten = sorted({l.emiten for l in semua_laporan})
    print(f"{len(semua_laporan)} laporan, {len(emiten)} emiten, {len(semua_fakta):,} fakta "
          f"({sum(l.jumlah_kosong for l in semua_laporan):,} tag kosong dilewati), "
          f"{len(semua_konflik)} konflik")
    ringkas: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for h in hasil:
        ringkas[h.pemeriksaan][h.status] += 1
    for nama, hitung in ringkas.items():
        print(f"  {nama:20} ok={hitung['ok']:3}  tidak_cocok={hitung['tidak_cocok']:3}  "
              f"lewati={hitung['lewati']:3}")
    for h in hasil:
        if h.status == "tidak_cocok":
            print(f"  TIDAK COCOK {h.emiten} {h.tahun_laporan} {h.pemeriksaan} "
                  f"({h.tahun_periode}): {h.detail}")
    print(f"Hasil ditulis ke {out}")
    return 1 if ketat and any(h.status == "tidak_cocok" for h in hasil) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raw", type=Path, default=Path("data/raw"),
                        help="folder berisi KODE_TAHUN_xbrl.zip (bawaan: data/raw)")
    parser.add_argument("--out", type=Path, default=Path("data/processed"),
                        help="folder keluaran CSV (bawaan: data/processed)")
    parser.add_argument("--ketat", action="store_true",
                        help="keluar dengan kode 1 bila ada hasil pemeriksaan yang tidak cocok")
    args = parser.parse_args(argv)
    return jalankan(args.raw, args.out, ketat=args.ketat)


if __name__ == "__main__":
    sys.exit(main())
