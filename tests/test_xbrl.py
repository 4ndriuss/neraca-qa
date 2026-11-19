"""Tes parser XBRL IDX.

Setiap tes mewakili jebakan yang ditemukan di data asli. Jalankan dengan::

    python -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neraca.ingest.xbrl import (  # noqa: E402
    Fakta,
    baca_html,
    baca_zip,
    parse_angka,
    parse_konteks,
    periksa,
)

INFO_UMUM = """
<html><body><table>
<tr><td>Kode entitas</td><td><ix:nonNumeric name="idx-dei:EntityCode" contextRef="CurrentYearInstant">BBCA</ix:nonNumeric></td></tr>
<tr><td>Nama</td><td><ix:nonNumeric name="idx-dei:EntityName" contextRef="CurrentYearInstant"><span>PT Bank Contoh Tbk.</span></ix:nonNumeric></td></tr>
<tr><td>Kosong</td><td><ix:nonNumeric name="idx-dei:ExplanationOfChangeInName" contextRef="CurrentYearInstant" xsi:nil="true"/></td></tr>
<tr><td>Akhir</td><td><ix:nonNumeric name="idx-dei:CurrentPeriodEndDate" contextRef="CurrentYearInstant">December 31, 2024</ix:nonNumeric></td></tr>
<tr><td>Periode</td><td><ix:nonNumeric name="idx-dei:PeriodOfFinancialStatementsSubmissions" contextRef="CurrentYearInstant">Tahunan / Annual</ix:nonNumeric></td></tr>
</table></body></html>
"""


def angka(nama, konteks, nilai, skala="6", tanda=None, unit="IDR", nil=False):
    if nil:
        return (f'<ix:nonFraction name="idx-cor:{nama}" contextRef="{konteks}" '
                f'unitRef="{unit}" xsi:nil="true"/>')
    sign = f' sign="{tanda}"' if tanda else ""
    return (f'<ix:nonFraction name="idx-cor:{nama}" contextRef="{konteks}" unitRef="{unit}" '
            f'format="ixt:numcommadot" decimals="-{skala}" scale="{skala}"{sign}>{nilai}'
            f'</ix:nonFraction>')


def buat_zip(folder: Path, halaman: dict[str, str]) -> Path:
    path = folder / "BBCA_2024_xbrl.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("1000000.html", INFO_UMUM)
        for nama, isi in halaman.items():
            zf.writestr(nama, f"<html><body><table>{isi}</table></body></html>")
    return path


def fakta(konsep, nilai, jenis="instant", tahun_periode=2024, dimensi="", skala=6):
    return Fakta(
        emiten="TEST", tahun_laporan=2024, konsep=konsep, nilai=str(nilai), mata_uang="IDR",
        unit="IDR", jenis_periode=jenis, tahun_periode=tahun_periode, dimensi=dimensi,
        kode_laporan="", konteks="", skala=skala, desimal="", prefix="idx-cor",
        file_html="x.html", file_zip="x.zip", fact_id="",
    )


class TesKonteks(unittest.TestCase):
    def test_konteks_dasar(self):
        k = parse_konteks("PriorEndYearInstant")
        self.assertEqual((k.selisih_tahun, k.jenis_periode, k.kode_laporan, k.dimensi),
                         (-1, "instant", "", ""))

    def test_konteks_dengan_kode_laporan_dan_dimensi(self):
        k = parse_konteks("PriorYearDuration_4410000PY_CommonStocksMember")
        self.assertEqual(k.dasar, "PriorYearDuration")
        self.assertEqual(k.kode_laporan, "4410000PY")
        self.assertEqual(k.dimensi, "CommonStocksMember")

    def test_kode_laporan_berakhiran_huruf(self):
        self.assertEqual(parse_konteks("CurrentYearInstant_1691000a_XMember").kode_laporan,
                         "1691000a")

    def test_dimensi_tanpa_kode_laporan(self):
        k = parse_konteks("CurrentYearInstant_CarryingAmountGrossMember")
        self.assertEqual((k.kode_laporan, k.dimensi), ("", "CarryingAmountGrossMember"))

    def test_dua_tahun_lalu(self):
        self.assertEqual(parse_konteks("Prior2YearsInstant").selisih_tahun, -2)

    def test_konteks_tak_dikenal_ditolak(self):
        with self.assertRaises(ValueError):
            parse_konteks("CurrentQuarterDuration")


class TesAngka(unittest.TestCase):
    def test_skala_jutaan(self):
        self.assertEqual(parse_angka("1,408,107,010", "6", None, "ixt:numcommadot"),
                         Decimal("1408107010000000"))

    def test_skala_miliaran(self):
        self.assertEqual(parse_angka("149,967", "9", None, "ixt:numcommadot"),
                         Decimal("149967000000000"))

    def test_satuan_penuh(self):
        self.assertEqual(parse_angka("29,728,781,933,757", "0", None, "ixt:numcommadot"),
                         Decimal("29728781933757"))

    def test_rugi_dari_atribut_sign(self):
        # GOTO: angka di dalam tag positif, tanda rugi ada di atribut sign="-".
        self.assertEqual(parse_angka("90,518,726", "6", "-", "ixt:numcommadot"),
                         Decimal("-90518726000000"))

    def test_format_titik_koma(self):
        self.assertEqual(parse_angka("1.234,5", "0", None, "ixt:numdotcomma"), Decimal("1234.5"))

    def test_format_tak_dikenal_ditolak(self):
        with self.assertRaises(ValueError):
            parse_angka("1", "0", None, "ixt:numwordsen")


class TesPembacaHtml(unittest.TestCase):
    def test_tag_kosong_tidak_menelan_angka_berikutnya(self):
        # BMRI: <ix:nonFraction ... xsi:nil="true"/> tanpa tag penutup.
        html = (angka("InterbankMudharabahInvestments", "CurrentYearInstant", "", nil=True)
                + "<td>Jumlah dana syirkah temporer</td>"
                + angka("TemporarySyirkahFunds", "CurrentYearInstant", "226,281,672"))
        hasil = baca_html(html).angka
        self.assertEqual(len(hasil), 2)
        self.assertEqual(hasil[0][1], "")
        self.assertEqual(hasil[1][0]["name"], "idx-cor:TemporarySyirkahFunds")
        self.assertEqual(hasil[1][1], "226,281,672")

    def test_tag_lain_di_dalam_nilai(self):
        html = ('<ix:nonFraction name="idx-cor:Assets" contextRef="CurrentYearInstant" '
                'unitRef="IDR" scale="6"><span>1,000</span><br></ix:nonFraction>'
                + angka("Equity", "CurrentYearInstant", "400"))
        hasil = baca_html(html).angka
        self.assertEqual([v for _, v in hasil], ["1,000", "400"])

    def test_tag_huruf_kecil(self):
        # BBCA 2022 menulis tag dengan huruf kecil.
        html = ('<ix:nonfraction name="idx-cor:Assets" contextref="CurrentYearInstant" '
                'unitref="IDR" scale="6">5</ix:nonfraction>')
        attrs, nilai = baca_html(html).angka[0]
        self.assertEqual((attrs["contextref"], nilai), ("CurrentYearInstant", "5"))


class TesBacaZip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_metadata_dan_tahun_periode(self):
        path = buat_zip(self.folder, {"4220000.html":
            angka("Assets", "CurrentYearInstant", "1,449,301")
            + angka("Assets", "PriorEndYearInstant", "1,408,107")
            + angka("Assets", "Prior2YearsInstant", "1,314,732")
            + angka("Loans", "CurrentYearInstant", "", nil=True)})
        laporan, daftar, konflik = baca_zip(path)
        self.assertEqual((laporan.emiten, laporan.tahun_laporan), ("BBCA", 2024))
        self.assertEqual(laporan.nama_entitas, "PT Bank Contoh Tbk.")
        self.assertEqual(laporan.tanggal_akhir_periode, "2024-12-31")
        self.assertEqual((laporan.jumlah_fakta, laporan.jumlah_kosong), (3, 1))
        self.assertEqual(sorted(f.tahun_periode for f in daftar), [2022, 2023, 2024])
        self.assertEqual(konflik, [])

    def test_saldo_awal_ekuitas_mundur_satu_tahun(self):
        # Laporan perubahan ekuitas memakai konteks yang sama untuk saldo awal
        # dan akhir; saldo awal jatuh pada akhir tahun sebelumnya.
        ctx = "PriorYearInstant_4410000PY_UnappropriatedRetainedEarningsMember"
        path = buat_zip(self.folder, {"4410000PY.html":
            angka("EquityPositionBeginningOfThePeriod", ctx, "198,132,066")
            + angka("EquityPositionEndOfThePeriod", ctx, "219,723,216")})
        _, daftar, _ = baca_zip(path)
        tahun = {f.konsep: f.tahun_periode for f in daftar}
        self.assertEqual(tahun["EquityPositionBeginningOfThePeriod"], 2022)
        self.assertEqual(tahun["EquityPositionEndOfThePeriod"], 2023)

    def test_dobel_sama_dilewati_dobel_beda_jadi_konflik(self):
        path = buat_zip(self.folder, {
            "4220000.html": angka("Assets", "CurrentYearInstant", "100")
                            + angka("Equity", "CurrentYearInstant", "40"),
            "4611000.html": angka("Assets", "CurrentYearInstant", "100")
                            + angka("Equity", "CurrentYearInstant", "41"),
        })
        _, daftar, konflik = baca_zip(path)
        self.assertEqual(len(daftar), 2)
        self.assertEqual(len(konflik), 1)
        self.assertEqual((konflik[0].konsep, konflik[0].nilai_pertama, konflik[0].nilai_kedua),
                         ("Equity", "40000000", "41000000"))

    def test_mata_uang_dari_unit(self):
        path = buat_zip(self.folder, {"1311000.html":
            angka("SalesAndRevenue", "CurrentYearDuration", "2,079,000", skala="3", unit="USD")
            + angka("BasicEarningsLossPerShare", "CurrentYearDuration", "0.05", skala="0",
                    unit="USDPerShares")})
        _, daftar, _ = baca_zip(path)
        hasil = {f.konsep: (f.mata_uang, f.unit, f.nilai) for f in daftar}
        self.assertEqual(hasil["SalesAndRevenue"], ("USD", "USD", "2079000000"))
        self.assertEqual(hasil["BasicEarningsLossPerShare"], ("USD", "USDPerShares", "0.05"))


class TesPeriksa(unittest.TestCase):
    def status(self, hasil, nama, tahun=2024):
        return next(h.status for h in hasil if h.pemeriksaan == nama and h.tahun_periode == tahun)

    def test_neraca_dengan_dana_syirkah(self):
        hasil = periksa([
            fakta("Assets", 1_408_107_010_000_000),
            fakta("Liabilities", 1_157_675_683_000_000),
            fakta("TemporarySyirkahFunds", 7_893_872_000_000),
            fakta("Equity", 242_537_455_000_000),
        ])
        self.assertEqual(self.status(hasil, "neraca"), "ok")

    def test_neraca_tidak_seimbang(self):
        hasil = periksa([fakta("Assets", 100_000_000), fakta("Liabilities", 50_000_000),
                         fakta("Equity", 40_000_000)])
        self.assertEqual(self.status(hasil, "neraca"), "tidak_cocok")

    def test_tanpa_laba_kotor_dilewati(self):
        # TLKM menyajikan beban per jenis: tidak ada laba kotor.
        hasil = periksa([fakta("Assets", 1), fakta("ProfitLoss", 1, "duration")])
        self.assertEqual(self.status(hasil, "laba_kotor"), "lewati")

    def test_pembagian_laba(self):
        hasil = periksa([
            fakta("ProfitLoss", 10_756, "duration"),
            fakta("ProfitLossAttributableToParentEntity", 9_225, "duration"),
            fakta("ProfitLossAttributableToNonControllingInterests", 1_531, "duration"),
        ])
        self.assertEqual(self.status(hasil, "pembagian_laba"), "ok")

    def test_saldo_awal_cocok_sebelum_atau_sesudah_penyesuaian(self):
        dim = "UnappropriatedRetainedEarningsMember"
        hasil = periksa([
            fakta("EquityPositionEndOfThePeriod", 12_327_400_000_000, tahun_periode=2023, dimensi=dim),
            fakta("BalanceBeforeRestatementAtBeginningOfPeriod", 12_327_400_000_000,
                  tahun_periode=2023, dimensi=dim),
            fakta("EquityPositionBeginningOfThePeriod", 12_249_000_000_000,
                  tahun_periode=2023, dimensi=dim),
        ])
        self.assertEqual(self.status(hasil, "saldo_awal_ekuitas", 2023), "ok")


if __name__ == "__main__":
    unittest.main()
