"""Normalisasi nama akun: petakan label PDF ke pos standar.

Label di laporan keuangan berbeda-beda antaremiten ("Laba tahun berjalan",
"LABA BERSIH", "Laba bersih tahun berjalan"), dan sebagian hanya bermakna
dalam konteksnya: "Pemilik entitas induk" bisa berarti laba bersih atau laba
komprehensif milik induk, tergantung judul di atasnya. Modul ini memetakan
setiap baris ke pos standar memakai aturan atas jenis laporan, label, dan
induk, lalu mengukur ketepatannya terhadap konsep XBRL yang nilainya cocok.

Setiap pos standar terhubung ke konsep XBRL padanannya (taksonomi IDX), yang
sama untuk semua emiten. Baris PDF yang nilainya cocok persis dengan XBRL
sudah "tahu" konsepnya, sehingga aturan label bisa dinilai:

- benar: pos hasil aturan sesuai dengan konsep XBRL baris itu
- salah: pos hasil aturan tidak sesuai
- terlewat: baris punya konsep XBRL pos standar, tetapi aturan tidak memetakannya

Selain itu diukur cakupan: untuk setiap emiten, tahun, dan pos standar yang
punya nilai di XBRL, apakah tabel angka standar punya nilai yang sama. Ukuran
ini yang paling relevan untuk tanya-jawab, karena subtotal tanpa label yang
terlewat tidak masalah selama posnya sendiri tersedia dari baris lain.

Cara pakai::

    neraca-akun data/processed/pdf_*_lk_cocok.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
from pathlib import Path

PK, LR, AK = "posisi_keuangan", "laba_rugi", "arus_kas"


@dataclass(frozen=True)
class PosStandar:
    """Pos standar. ``konsep_xbrl`` dipakai untuk menilai pemetaan baris;
    ``konsep_kunci`` (bawaan: sama) dipakai untuk menghitung cakupan."""

    kode: str
    nama: str
    laporan: str
    konsep_xbrl: tuple[str, ...]
    konsep_kunci: tuple[str, ...] = ()

    @property
    def kunci(self) -> tuple[str, ...]:
        return self.konsep_kunci or self.konsep_xbrl


POS_STANDAR: tuple[PosStandar, ...] = (
    # Laporan posisi keuangan
    PosStandar("total_aset", "Jumlah aset", PK, ("Assets",)),
    PosStandar("aset_lancar", "Jumlah aset lancar", PK, ("CurrentAssets",)),
    PosStandar("aset_tidak_lancar", "Jumlah aset tidak lancar", PK,
               ("NonCurrentAssets", "NoncurrentAssets")),
    PosStandar("kas", "Kas", PK, ("Cash",)),
    PosStandar("kas_dan_setara_kas", "Kas dan setara kas", PK, ("CashAndCashEquivalents",)),
    PosStandar("persediaan", "Persediaan", PK, ("Inventories", "CurrentInventories")),
    PosStandar("total_liabilitas", "Jumlah liabilitas", PK, ("Liabilities",)),
    PosStandar("liabilitas_jangka_pendek", "Jumlah liabilitas jangka pendek", PK,
               ("CurrentLiabilities",)),
    PosStandar("liabilitas_jangka_panjang", "Jumlah liabilitas jangka panjang", PK,
               ("NonCurrentLiabilities", "NoncurrentLiabilities")),
    PosStandar("dana_syirkah_temporer", "Dana syirkah temporer", PK, ("TemporarySyirkahFunds",)),
    PosStandar("ekuitas_induk", "Ekuitas yang diatribusikan kepada pemilik entitas induk", PK,
               ("EquityAttributableToEquityOwnersOfParentEntity",)),
    PosStandar("kepentingan_nonpengendali", "Kepentingan nonpengendali (ekuitas)", PK,
               ("NonControllingInterests",)),
    # Kolom 2022 di neraca tiga kolom hanya punya padanan di laporan perubahan ekuitas XBRL.
    PosStandar("total_ekuitas", "Jumlah ekuitas", PK,
               ("Equity", "EquityPositionEndOfThePeriod", "EquityPositionBeginningOfThePeriod",
                "BalanceBeforeRestatementAtBeginningOfPeriod"), ("Equity",)),
    PosStandar("total_liabilitas_ekuitas", "Jumlah liabilitas (dan dana syirkah) dan ekuitas", PK,
               ("LiabilitiesAndEquity", "LiabilitiesTemporarySyirkahFundsAndEquity")),
    # Laporan laba rugi
    PosStandar("pendapatan", "Pendapatan", LR, ("SalesAndRevenue",)),
    PosStandar("beban_pokok_pendapatan", "Beban pokok pendapatan", LR, ("CostOfSalesAndRevenue",)),
    PosStandar("laba_kotor", "Laba kotor", LR, ("GrossProfit",)),
    PosStandar("pendapatan_bunga", "Pendapatan bunga (dan syariah)", LR, ("InterestIncome",)),
    PosStandar("beban_bunga", "Beban bunga (dan syariah)", LR, ("InterestExpenses",)),
    PosStandar("laba_usaha", "Laba usaha", LR, ("ProfitFromOperation",)),
    PosStandar("laba_sebelum_pajak", "Laba sebelum pajak", LR, ("ProfitLossBeforeIncomeTax",)),
    PosStandar("beban_pajak", "Beban pajak penghasilan", LR, ("TaxBenefitExpenses",)),
    PosStandar("laba_bersih", "Laba bersih", LR, ("ProfitLoss",)),
    PosStandar("laba_bersih_induk", "Laba bersih milik entitas induk", LR,
               ("ProfitLossAttributableToParentEntity",)),
    PosStandar("laba_bersih_nonpengendali", "Laba bersih milik kepentingan nonpengendali", LR,
               ("ProfitLossAttributableToNonControllingInterests",)),
    PosStandar("laba_komprehensif", "Jumlah laba komprehensif", LR, ("ComprehensiveIncome",)),
    PosStandar("laba_komprehensif_induk", "Laba komprehensif milik entitas induk", LR,
               ("ComprehensiveIncomeAttributableToParentEntity",)),
    # Laba per saham dasar dan dilusian hampir selalu sama, dan XBRL kadang hanya
    # menandai salah satunya (BBRI) atau menandainya untuk set angka yang berbeda
    # (ADRO), jadi keduanya diterima untuk kedua pos.
    PosStandar("laba_per_saham_dasar", "Laba per saham dasar", LR,
               ("BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsLossPerShare",
                "DilutedEarningsLossPerShareFromContinuingOperations", "DilutedEarningsLossPerShare"),
               ("BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsLossPerShare")),
    PosStandar("laba_per_saham_dilusian", "Laba per saham dilusian", LR,
               ("DilutedEarningsLossPerShareFromContinuingOperations", "DilutedEarningsLossPerShare",
                "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsLossPerShare"),
               ("DilutedEarningsLossPerShareFromContinuingOperations", "DilutedEarningsLossPerShare")),
    # Laporan arus kas
    PosStandar("arus_kas_operasi", "Arus kas bersih dari aktivitas operasi", AK,
               ("NetCashFlowsReceivedFromUsedInOperatingActivities",)),
    PosStandar("arus_kas_investasi", "Arus kas bersih dari aktivitas investasi", AK,
               ("NetCashFlowsReceivedFromUsedInInvestingActivities",)),
    PosStandar("arus_kas_pendanaan", "Arus kas bersih dari aktivitas pendanaan", AK,
               ("NetCashFlowsReceivedFromUsedInFinancingActivities",)),
    PosStandar("kenaikan_bersih_kas", "Kenaikan (penurunan) bersih kas dan setara kas", AK,
               ("NetIncreaseDecreaseInCashAndCashEquivalents",)),
    PosStandar("kas_awal_tahun", "Kas dan setara kas awal tahun", AK,
               ("CashAndCashEquivalentsCashFlows",)),
    PosStandar("kas_akhir_tahun", "Kas dan setara kas akhir tahun", AK,
               ("CashAndCashEquivalentsCashFlows",)),
    PosStandar("dividen_dibayar", "Dividen kas dibayar kepada pemegang saham induk", AK,
               ("DividendsPaidFromFinancingActivities",)),
)
POS = {p.kode: p for p in POS_STANDAR}


@dataclass(frozen=True)
class Aturan:
    """Satu aturan pemetaan.

    ``induk`` dan ``bukan_induk`` diperiksa terhadap induk baris; bila induknya
    kosong (judul sejajar dengan butirnya), konteks (judul terakhir) dipakai.
    ``bukan_konteks`` selalu diperiksa terhadap konteks.
    """

    pos: str
    laporan: str
    label: re.Pattern
    induk: re.Pattern | None = None
    bukan_induk: re.Pattern | None = None
    bukan_label: re.Pattern | None = None
    bukan_konteks: re.Pattern | None = None


def _A(pos: str, label: str, induk: str | None = None, bukan_induk: str | None = None,
       bukan_label: str | None = None, bukan_konteks: str | None = None,
       laporan: str | None = None) -> Aturan:
    c = lambda s: re.compile(s) if s else None  # noqa: E731
    return Aturan(pos, laporan or POS[pos].laporan, re.compile(label), c(induk), c(bukan_induk),
                  c(bukan_label), c(bukan_konteks))


_JUMLAH = r"(jumlah|total)"
_LABA = r"(laba|rugi|laba \(rugi\)|\(rugi\) laba|laba/\(rugi\)|\(rugi\)/laba)"
_DIATRIBUSI = r"diatribusikan"

# Urutan penting: aturan pertama yang cocok dipakai.
ATURAN: tuple[Aturan, ...] = (
    # --- posisi keuangan
    _A("total_aset", rf"^{_JUMLAH} aset$"),
    _A("aset_lancar", rf"^{_JUMLAH} aset lancar$"),
    _A("aset_tidak_lancar", rf"^{_JUMLAH} aset tidak lancar$"),
    _A("total_liabilitas", rf"^{_JUMLAH} liabilitas$"),
    _A("liabilitas_jangka_pendek", rf"^{_JUMLAH} liabilitas jangka pendek$"),
    _A("liabilitas_jangka_panjang", rf"^{_JUMLAH} liabilitas jangka panjang$"),
    _A("dana_syirkah_temporer", rf"^({_JUMLAH} )?dana syirkah temporer$"),
    _A("total_liabilitas_ekuitas", rf"^{_JUMLAH} liabilitas,? (dana syirkah temporer,? )?dan ekuitas$"),
    _A("ekuitas_induk", rf"^({_JUMLAH} )?ekuitas yang (dapat )?{_DIATRIBUSI} kepada "
                        r"(para )?(pemilik|pemegang saham)?\s*(entitas induk|perusahaan|perseroan)"),
    # TLKM: "Ekuitas yang dapat diatribusikan kepada:" / "Pemilik entitas induk - bersih"
    _A("ekuitas_induk", r"^(pemilik|pemegang saham) entitas induk", induk=_DIATRIBUSI),
    _A("total_ekuitas", rf"^{_JUMLAH} ekuitas$"),
    _A("kepentingan_nonpengendali", r"^kepentingan nonpengendali"),
    _A("kas_dan_setara_kas", r"^kas dan setara kas$"),
    _A("kas", r"^kas$"),
    _A("persediaan", r"^persediaan( - (neto|bersih))?$"),
    # --- laba rugi
    # "Penjualan" di bawah judul "Beban usaha" adalah beban penjualan (MYOR).
    _A("pendapatan", r"^((pendapatan|penjualan)( usaha)?( (bersih|neto))?"
                     r"|pendapatan dari kontrak dengan pelanggan)$", bukan_induk=r"komprehensif|beban",
       bukan_konteks=r"beban"),
    _A("beban_pokok_pendapatan", r"^(beban|harga) pokok (penjualan|pendapatan)"),
    _A("laba_kotor", r"^laba (kotor|bruto)$"),
    # Hanya total bank; "penghasilan bunga" di perusahaan non-bank adalah pendapatan keuangan.
    _A("pendapatan_bunga", rf"^{_JUMLAH} pendapatan bunga( dan (pendapatan )?syariah)?$"),
    _A("beban_bunga", rf"^{_JUMLAH} beban bunga( dan (beban )?syariah)?$"),
    _A("laba_usaha", rf"^{_LABA} (usaha|operasional)$"),
    _A("laba_sebelum_pajak", rf"^{_LABA}( bersih)? sebelum (zakat dan )?(beban )?pajak"),
    _A("beban_pajak", r"^((\(beban\)/manfaat|\(beban\) manfaat|beban|manfaat \(beban\)) )?"
                      r"pajak( penghasilan)?( - (bersih|neto))?$",
       bukan_induk=r"komprehensif|reklasifikasi"),
    _A("laba_bersih", rf"^({_LABA}( bersih)?( tahun berjalan)?|laba bersih)$",
       bukan_induk=rf"{_DIATRIBUSI}|per saham"),
    _A("laba_komprehensif_induk", r"^(pemilik|pemegang saham) entitas induk",
       induk=r"komprehensif"),
    _A("laba_bersih_induk", r"^(pemilik|pemegang saham) entitas induk|^entitas induk",
       induk=rf"{_DIATRIBUSI}"),
    _A("laba_bersih_nonpengendali", r"^kepentingan nonpengendali", induk=rf"{_DIATRIBUSI}",
       bukan_induk=r"komprehensif"),
    _A("laba_komprehensif", rf"^{_JUMLAH} (laba|penghasilan|penghasilan/\(beban\)|laba/\(rugi\)) "
                            r"komprehensif( tahun berjalan)?(, setelah pajak)?$"),
    # --- arus kas
    _A("arus_kas_operasi", r"^(arus )?kas (bersih|neto) .*(aktivitas|kegiatan) operasi"),
    _A("arus_kas_investasi", r"^(arus )?kas (bersih|neto) .*(aktivitas|kegiatan) investasi"),
    _A("arus_kas_pendanaan", r"^(arus )?kas (bersih|neto) .*(aktivitas|kegiatan) pendanaan"),
    _A("kenaikan_bersih_kas", r"(kenaikan|penurunan).*(bersih|neto).* kas"),
    _A("kas_awal_tahun", r"^kas dan setara kas,? (pada )?awal (tahun|periode)"),
    _A("kas_akhir_tahun", r"^kas dan setara kas,? (pada )?akhir (tahun|periode)"),
    # Rincian kas dan setara kas di bawah arus kas (BBCA, BBRI)
    _A("kas_akhir_tahun", rf"^{_JUMLAH} kas dan setara kas$"),
    _A("kas", r"^kas$", laporan=AK),
    _A("dividen_dibayar", r"^(pembayaran dividen( kas)?|dividen( kas)? yang dibayarkan|"
                          r"pembagian laba untuk dividen)", bukan_label=r"nonpengendali"),
)


def normalisasi_teks(teks: str) -> str:
    """Huruf kecil, spasi tunggal, ejaan nonpengendali seragam, tanpa keterangan pindahan."""
    t = teks.lower().replace("–", "-").replace("*)", "")
    t = re.sub(r"non[-\s]+pengendali", "nonpengendali", t)
    t = re.sub(r"\([^)]*(pindah|halaman sebelumnya)[^)]*\)?", "", t)
    t = re.sub(r"\s+", " ", t).strip(" :-")
    return t


def _pos_per_saham(label: str, induk: str) -> str | None:
    teks = f"{label} {induk}"
    if not re.search(r"\bper saham\b", teks) or re.search(r"nominal|per ads", teks):
        return None
    if "dilusian" in label and "dasar" not in label:
        return "laba_per_saham_dilusian"
    return "laba_per_saham_dasar"


_LABEL_UMUM = re.compile(r"^((jumlah|total|neto|bersih)( - (neto|bersih))?)?$")


def petakan_semua(laporan: str, label: str, induk: str = "", konteks: str = "") -> list[str]:
    """Semua pos standar untuk satu baris (biasanya satu; "dasar dan dilusian" dua)."""
    label_n = normalisasi_teks(label)
    if laporan == LR:
        pos = _pos_per_saham(label_n, normalisasi_teks(induk or konteks))
        if pos == "laba_per_saham_dasar" and "dilusian" in label_n:
            return ["laba_per_saham_dasar", "laba_per_saham_dilusian"]
    pos = petakan(laporan, label, induk, konteks)
    return [pos] if pos else []


def petakan(laporan: str, label: str, induk: str = "", konteks: str = "") -> str | None:
    """Pos standar untuk satu baris, atau ``None`` bila tidak ada aturan yang cocok.

    Label kosong atau umum (``Jumlah``, ``Total``) mewarisi pos dari judul di
    atasnya: subtotal tanpa label di bawah ``(BEBAN) MANFAAT PAJAK PENGHASILAN``
    adalah beban pajak (TLKM).
    """
    label_n, induk_n = normalisasi_teks(label), normalisasi_teks(induk)
    konteks_n = normalisasi_teks(konteks)
    atas = induk_n or konteks_n  # judul yang membawahi, atau judul terakhir bila sejajar
    if _LABEL_UMUM.match(label_n):
        # Di laba rugi, subtotal di bawah "... yang diatribusikan kepada:" sengaja tidak
        # dipetakan: di BMRI isinya laba total, di ADRO jumlah milik induk.
        if not konteks_n or (laporan == LR and re.search(_DIATRIBUSI, konteks_n)):
            return None
        return petakan(laporan, konteks, "", "")
    if laporan == LR:
        pos = _pos_per_saham(label_n, atas)
        if pos:
            return pos
    for a in ATURAN:
        if a.laporan != laporan or not a.label.search(label_n):
            continue
        if a.induk and not a.induk.search(atas):
            continue
        if a.bukan_induk and a.bukan_induk.search(atas):
            continue
        if a.bukan_label and a.bukan_label.search(label_n):
            continue
        if a.bukan_konteks and a.bukan_konteks.search(konteks_n):
            continue
        return a.pos
    return None


# --------------------------------------------------------------------------
# Tabel angka standar dan evaluasi
# --------------------------------------------------------------------------

STATUS_XBRL_PERSIS = ("persis", "tanda_terbalik", "laporan_berikut")


@dataclass
class AngkaStandar:
    emiten: str
    tahun_laporan: int
    tahun: int
    pos: str
    nilai: str
    mata_uang: str
    halaman: int
    laporan: str
    label: str
    induk: str
    status_verifikasi: str
    penilaian: str  # benar | salah | belum_dinilai
    urutan: int = 0


def _konsep(teks: str) -> set[str]:
    return {k.strip().split(" (")[0] for k in teks.split("|") if k.strip()}


def nilai_pemetaan(pos: str | None, status: str, konsep: str, nilai: str) -> str:
    """``benar``, ``salah``, ``terlewat``, atau ``belum_dinilai``."""
    if status not in STATUS_XBRL_PERSIS or Decimal(nilai) == 0:
        return "belum_dinilai"
    ks = _konsep(konsep)
    if pos:
        return "benar" if ks & set(POS[pos].konsep_xbrl) else "salah"
    semua = {k for p in POS_STANDAR for k in p.konsep_xbrl}
    return "terlewat" if ks & semua else "belum_dinilai"


_NAMA_CSV = re.compile(r"^pdf_([A-Z]{4})_(\d{4})_")


NILAI_PDF: dict[tuple[str, int], set[Decimal]] = defaultdict(set)  # diisi oleh proses()


def proses(paths: list[Path]) -> tuple[list[AngkaStandar], Counter, dict[str, Counter], list[dict]]:
    tabel: list[AngkaStandar] = []
    total: Counter = Counter()
    per_pos: dict[str, Counter] = defaultdict(Counter)
    kesalahan: list[dict] = []
    for path in paths:
        m = _NAMA_CSV.match(path.name)
        if not m:
            continue
        emiten, tahun_laporan = m.group(1), int(m.group(2))
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                NILAI_PDF[(emiten, tahun_laporan)].add(abs(Decimal(r["nilai"])))
                daftar_pos = petakan_semua(r["laporan"], r["label"], r["induk"], r.get("konteks", ""))
                for pos in daftar_pos or [None]:
                    penilaian = nilai_pemetaan(pos, r["status"], r["konsep"], r["nilai"])
                    total[penilaian] += 1
                    if pos:
                        per_pos[pos][penilaian] += 1
                    if penilaian in ("salah", "terlewat"):
                        kesalahan.append({"emiten": emiten, "halaman": r["halaman"],
                                          "tahun": r["tahun"], "penilaian": penilaian, "pos": pos or "",
                                          "label": r["label"], "induk": r["induk"],
                                          "konsep_xbrl": r["konsep"]})
                    if pos:
                        tabel.append(AngkaStandar(
                            emiten, tahun_laporan, int(r["tahun"]), pos, r["nilai"], r["mata_uang"],
                            int(r["halaman"]), r["laporan"], r["label"], r["induk"], r["status"],
                            penilaian if penilaian != "terlewat" else "belum_dinilai",
                            int(r["urutan"])))
    return tabel, total, per_pos, kesalahan


# Total arus kas per aktivitas adalah baris terakhir di bagiannya: BBCA mencetak
# "Kas bersih ... aktivitas operasi" dua kali, sebelum dan sesudah pajak penghasilan.
_PILIH_TERAKHIR = {"arus_kas_operasi", "arus_kas_investasi", "arus_kas_pendanaan"}


def pilih_satu(tabel: list[AngkaStandar]) -> tuple[list[AngkaStandar], list[tuple]]:
    """Satu angka per (emiten, tahun laporan, tahun, pos).

    Yang dipilih adalah kemunculan pertama menurut halaman dan urutan, kecuali
    total arus kas per aktivitas yang memakai kemunculan terakhir. Pemilihan ini
    tidak memakai XBRL, supaya berlaku juga untuk laporan tanpa kunci jawaban.
    Mengembalikan juga daftar konflik, yaitu pos yang muncul lebih dari sekali
    dengan nilai berbeda.
    """
    kelompok: dict[tuple, list[AngkaStandar]] = defaultdict(list)
    for a in tabel:
        kelompok[(a.emiten, a.tahun_laporan, a.tahun, a.pos)].append(a)
    hasil, konflik = [], []
    for kunci, daftar in sorted(kelompok.items()):
        daftar.sort(key=lambda a: (a.halaman, a.urutan))
        hasil.append(daftar[-1] if kunci[3] in _PILIH_TERAKHIR else daftar[0])
        if len({a.nilai for a in daftar}) > 1:
            konflik.append((kunci, [(a.halaman, a.label, a.nilai) for a in daftar]))
    return hasil, konflik


# Bila tidak ada kepentingan nonpengendali, bagian induk sama dengan totalnya.
_TURUNAN_INDUK = (
    ("ekuitas_induk", "total_ekuitas", "kepentingan_nonpengendali"),
    ("laba_bersih_induk", "laba_bersih", "laba_bersih_nonpengendali"),
    ("laba_komprehensif_induk", "laba_komprehensif", "laba_bersih_nonpengendali"),
)


def isi_bagian_induk(terpilih: list[AngkaStandar]) -> list[AngkaStandar]:
    """Turunkan pos milik induk dari totalnya untuk emiten tanpa kepentingan nonpengendali.

    Hanya bila laporan itu sama sekali tidak memuat pos nonpengendali untuk
    tahun tersebut. Hasilnya diberi status verifikasi ``turunan_tanpa_nonpengendali``.
    """
    ada = {(a.emiten, a.tahun_laporan, a.tahun, a.pos): a for a in terpilih}
    tambahan = []
    for (emiten, tl, tahun) in sorted({(a.emiten, a.tahun_laporan, a.tahun) for a in terpilih}):
        nonpengendali = any((emiten, tl, tahun, p) in ada for _, _, p in _TURUNAN_INDUK)
        if nonpengendali:
            continue
        for pos_induk, pos_total, _ in _TURUNAN_INDUK:
            total = ada.get((emiten, tl, tahun, pos_total))
            if total and (emiten, tl, tahun, pos_induk) not in ada:
                tambahan.append(AngkaStandar(
                    emiten, tl, tahun, pos_induk, total.nilai, total.mata_uang, total.halaman,
                    total.laporan, total.label, total.induk, "turunan_tanpa_nonpengendali",
                    "belum_dinilai", total.urutan))
    return sorted(terpilih + tambahan, key=lambda a: (a.emiten, a.tahun_laporan, a.tahun, a.pos))


_BUKAN_KUNCI = {"EquityPositionEndOfThePeriod", "EquityPositionBeginningOfThePeriod",
                "BalanceBeforeRestatementAtBeginningOfPeriod"}


def cakupan(terpilih: list[AngkaStandar], xbrl_csv: Path) -> tuple[Counter, dict[str, Counter], list]:
    """Untuk setiap (emiten, tahun, pos) yang punya nilai di XBRL: ada dan sama di tabel?

    Kunci diambil dari XBRL laporan yang sama atau angka pembanding di laporan
    tahun berikutnya. Tanda diabaikan, karena PDF menulis beban dalam kurung.
    """
    xbrl: dict[tuple, set[Decimal]] = defaultdict(set)
    with xbrl_csv.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if not r["dimensi"]:
                xbrl[(r["emiten"], int(r["tahun_laporan"]), int(r["tahun_periode"]), r["konsep"])].add(
                    abs(Decimal(r["nilai"])))
    tabel = {(a.emiten, a.tahun_laporan, a.tahun, a.pos): abs(Decimal(a.nilai)) for a in terpilih}
    laporan = {(a.emiten, a.tahun_laporan) for a in terpilih}
    hasil, per_pos, meleset = Counter(), defaultdict(Counter), []
    for emiten, tl in sorted(laporan):
        for tahun in (tl, tl - 1):
            for pos in POS_STANDAR:
                tp = tahun - 1 if pos.kode == "kas_awal_tahun" else tahun
                kunci: set[Decimal] = set()
                for k in pos.kunci:
                    kunci |=xbrl.get((emiten, tl, tp, k), set()) | xbrl.get((emiten, tl + 1, tp, k), set())
                kunci.discard(Decimal(0))
                if not kunci:
                    continue
                nilai = tabel.get((emiten, tl, tahun, pos.kode))
                if nilai in kunci:
                    status = "ada"
                elif not kunci & NILAI_PDF.get((emiten, tl), set()):
                    status = "tidak_ada_di_pdf"  # XBRL berbeda dari PDF atau menggabungkan pos
                else:
                    status = "beda" if nilai is not None else "kosong"
                hasil[status] += 1
                per_pos[pos.kode][status] += 1
                if status != "ada":
                    meleset.append((emiten, tahun, pos.kode, status, nilai, sorted(kunci)))
    return hasil, per_pos, meleset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cocok_csv", type=Path, nargs="+", help="hasil neraca-cocok (pdf_*_cocok.csv)")
    parser.add_argument("--out", type=Path, default=Path("data/processed/akun_standar.csv"))
    parser.add_argument("--xbrl", type=Path, default=Path("data/processed/xbrl_fakta.csv"))
    args = parser.parse_args(argv)

    tabel, total, per_pos, kesalahan = proses(args.cocok_csv)
    terpilih, konflik = pilih_satu(tabel)
    terpilih = isi_bagian_induk(terpilih)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[f.name for f in fields(AngkaStandar)])
        w.writeheader()
        w.writerows(asdict(a) for a in terpilih)
    with args.out.with_name(args.out.stem + "_kesalahan.csv").open("w", newline="",
                                                                    encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["emiten", "halaman", "tahun", "penilaian", "pos",
                                           "label", "induk", "konsep_xbrl"])
        w.writeheader()
        w.writerows(kesalahan)

    benar, salah, terlewat = total["benar"], total["salah"], total["terlewat"]
    presisi = benar / (benar + salah) if benar + salah else 0
    recall = benar / (benar + terlewat) if benar + terlewat else 0
    print(f"Pemetaan label -> pos standar, dinilai dengan konsep XBRL:")
    print(f"  benar {benar}, salah {salah}, terlewat {terlewat}  ->  presisi {presisi:.1%}, "
          f"recall {recall:.1%}")
    print(f"  {len(tabel)} baris terpetakan, {len(terpilih)} angka standar unik "
          f"({len({a.emiten for a in terpilih})} emiten), {len(konflik)} pos dengan nilai ganda")

    if args.xbrl.exists():
        hasil, per_pos, meleset = cakupan(terpilih, args.xbrl)
        n = sum(hasil.values())
        di_pdf = n - hasil["tidak_ada_di_pdf"]
        print(f"Cakupan pos standar (emiten x tahun x pos yang punya nilai di XBRL): "
              f"{hasil['ada']}/{n} = {hasil['ada'] / n:.1%} ada dan sama")
        print(f"  {hasil['tidak_ada_di_pdf']} nilai XBRL tidak ada di PDF sama sekali; dari {di_pdf} "
              f"yang ada di PDF: {hasil['ada']} terpetakan ({hasil['ada'] / di_pdf:.1%}), "
              f"{hasil['beda']} salah pilih, {hasil['kosong']} terlewat")
        lemah = sorted(((p, c) for p, c in per_pos.items() if c["beda"] + c["kosong"]),
                       key=lambda pc: -(pc[1]["beda"] + pc[1]["kosong"]))
        for p, c in lemah:
            print(f"    {p:28} {c['ada']:3}/{sum(c.values()):3}  salah pilih {c['beda']}, "
                  f"terlewat {c['kosong']}, tidak ada di PDF {c['tidak_ada_di_pdf']}")
        with args.out.with_name(args.out.stem + "_cakupan.csv").open("w", newline="",
                                                                      encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["emiten", "tahun", "pos", "status", "nilai_tabel", "nilai_xbrl"])
            for emiten, tahun, pos, status, nilai, kunci in meleset:
                w.writerow([emiten, tahun, pos, status, nilai if nilai is not None else "",
                            " | ".join(str(k) for k in kunci)])
    print(f"Ditulis ke {args.out}, {args.out.stem}_kesalahan.csv, dan {args.out.stem}_cakupan.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
