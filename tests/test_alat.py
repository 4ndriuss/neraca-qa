"""Tes fungsi alat dan penilaian set pertanyaan, dengan database kecil di memori."""

import sqlite3
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neraca.alat import Alat, format_nilai  # noqa: E402
from neraca.db import SKEMA  # noqa: E402
from neraca.evaluasi.pertanyaan import (  # noqa: E402
    SPESIFIKASI,
    baca_jsonl,
    jalankan_uji,
    jawab_dengan_rencana,
    nilai_jawaban,
    ringkasan_markdown,
    tulis_jsonl,
    tulis_ringkasan,
)

EMITEN = [("BBCA", "PT Bank Central Asia Tbk.", "G. Financials", "G1. Banks"),
          ("BRIS", "PT Bank Syariah Indonesia Tbk", "G. Financials", "G1. Banks"),
          ("TLKM", "PT Telkom Indonesia (Persero) Tbk", "J. Infrastructures", "J3. Telecommunication"),
          ("UNVR", "Unilever Indonesia Tbk", "D. Consumer Non-Cyclicals", "D4. Household"),
          ("ADRO", "Alamtri Resources Indonesia Tbk", "A. Energy", "A1. Oil, Gas & Coal"),
          ("GOTO", "PT GoTo Gojek Tokopedia Tbk", "I. Technology", "I1. Software")]

# (emiten, tahun_laporan, tahun, pos, nilai, mata_uang, halaman, status)
ANGKA = [
    ("BBCA", 2024, 2024, "laba_bersih_induk", "54836305000000", "IDR", 11, "persis"),
    ("BBCA", 2024, 2023, "laba_bersih_induk", "48639122000000", "IDR", 11, "persis"),
    ("BBCA", 2024, 2024, "total_aset", "1449301328000000", "IDR", 8, "tidak_cocok"),
    ("BBCA", 2024, 2024, "beban_bunga", "-12532290000000", "IDR", 10, "tanda_terbalik"),
    ("TLKM", 2024, 2024, "pendapatan", "149967000000000", "IDR", 10, "persis"),
    ("TLKM", 2024, 2024, "laba_bersih_induk", "23649000000000", "IDR", 10, "persis"),
    ("UNVR", 2024, 2024, "pendapatan", "35138643000000", "IDR", 7, "persis"),
    ("UNVR", 2024, 2024, "laba_kotor", "16719681000000", "IDR", 7, "persis"),
    ("UNVR", 2024, 2024, "laba_per_saham_dasar", "88", "IDR", 7, "persis"),
    ("ADRO", 2024, 2024, "laba_bersih_induk", "1380013000", "USD", 17, "persis"),
    ("ADRO", 2024, 2023, "pendapatan", "2135412000", "USD", 15, "persis"),
    ("GOTO", 2024, 2024, "laba_bersih_induk", "-5154924000000", "IDR", 16, "persis"),
    ("GOTO", 2024, 2023, "laba_bersih_induk", "-90395629000000", "IDR", 16, "persis"),
]

# (emiten, tahun_laporan, konsep, tahun_periode, nilai, mata_uang)
XBRL = [
    ("ADRO", 2023, "SalesAndRevenue", 2023, "6517556000", "USD"),
    ("TLKM", 2025, "ProfitLossAttributableToParentEntity", 2024, "22403000000000", "IDR"),
    ("BBCA", 2023, "Assets", 2023, "1408107010000000", "IDR"),
]


def buat_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SKEMA)
    con.executemany("INSERT INTO emiten VALUES (?, ?, ?, ?)", EMITEN)
    laporan = {(e, tl) for e, tl, *_ in ANGKA} | {(e, tl) for e, tl, *_ in XBRL}
    for e, tl in sorted(laporan):
        uang = "Dollar Amerika / USD" if e == "ADRO" else "Rupiah / IDR"
        con.execute("INSERT INTO laporan (emiten, tahun_laporan, mata_uang, file_xbrl, file_pdf) "
                    "VALUES (?, ?, ?, ?, ?)", (e, tl, uang, f"{e}_{tl}_xbrl.zip",
                                               f"{e}_{tl}_lk.pdf" if tl == 2024 else None))
    con.executemany("INSERT INTO angka (emiten, tahun_laporan, tahun, pos, nilai, mata_uang, halaman, "
                    "laporan, label, status_verifikasi) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'label', ?)", ANGKA)
    con.executemany("INSERT INTO xbrl_fakta (emiten, tahun_laporan, konsep, jenis_periode, tahun_periode, "
                    "nilai, mata_uang) VALUES (?, ?, ?, 'duration', ?, ?, ?)",
                    [(e, tl, k, tp, v, u) for e, tl, k, tp, v, u in XBRL])
    con.execute("INSERT INTO baris_pdf (emiten, tahun_laporan, halaman, urutan, label, tahun, nilai, "
                "mata_uang) VALUES ('UNVR', 2024, 7, 1, 'Beban pemasaran', 2024, '-4000000000000', 'IDR')")
    return con


class TesFormat(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_nilai(Decimal("54836305000000"), "IDR"), "Rp 54,84 triliun")
        self.assertEqual(format_nilai(Decimal("-5154924000000"), "IDR"), "-Rp 5,15 triliun")
        self.assertEqual(format_nilai(Decimal("1380013000"), "USD"), "USD 1,38 miliar")
        self.assertEqual(format_nilai(Decimal("88"), "IDR", per_saham=True), "Rp 88 per saham")
        self.assertEqual(format_nilai(Decimal("597.67"), "IDR", per_saham=True), "Rp 597,67 per saham")


class TesAmbilAngka(unittest.TestCase):
    def setUp(self):
        self.alat = Alat(buat_db())

    def test_dari_pdf_dengan_halaman(self):
        h = self.alat.ambil_angka("bbca", "laba_bersih_induk", 2024)
        self.assertTrue(h["tersedia"])
        self.assertEqual((h["nilai"], h["sumber"]["halaman"], h["sumber"]["jenis"]),
                         ("54836305000000", 11, "pdf"))
        self.assertEqual(h["sumber"]["teks"], "Laporan keuangan 2024, hal. 11")

    def test_xbrl_bila_pdf_tidak_ada(self):
        h = self.alat.ambil_angka("ADRO", "pendapatan", 2023, tahun_laporan=2023)
        self.assertEqual((h["nilai"], h["sumber"]["jenis"]), ("6517556000", "xbrl"))

    def test_catatan_disajikan_kembali(self):
        h = self.alat.ambil_angka("TLKM", "laba_bersih_induk", 2024)
        self.assertEqual(h["disajikan_kembali"]["nilai"], "22403000000000")
        h = self.alat.ambil_angka("ADRO", "pendapatan", 2023)  # laporan 2023 vs 2024
        self.assertEqual(h["disajikan_kembali"]["nilai"], "2135412000")

    def test_beban_selalu_positif(self):
        self.assertEqual(self.alat.ambil_angka("BBCA", "beban_bunga", 2024)["nilai"], "12532290000000")

    def test_peringatan_belum_terverifikasi(self):
        self.assertIn("peringatan", self.alat.ambil_angka("BBCA", "total_aset", 2024))

    def test_alasan_tidak_tersedia(self):
        alasan = lambda e, p, t=2024: self.alat.ambil_angka(e, p, t)["alasan"]  # noqa: E731
        self.assertIn("bank syariah", alasan("BRIS", "pendapatan_bunga"))
        self.assertIn("adalah bank", alasan("BBCA", "pendapatan"))
        self.assertIn("beban per jenis", alasan("TLKM", "laba_kotor"))
        self.assertIn("hanya tersedia", alasan("BBCA", "total_aset", 2030))

    def test_galat_argumen(self):
        self.assertIn("galat", self.alat.jalankan("ambil_angka", {"emiten": "XXXX", "pos": "total_aset",
                                                                  "tahun": 2024}))
        self.assertIn("galat", self.alat.jalankan("ambil_angka", {"emiten": "BBCA", "pos": "laba",
                                                                  "tahun": 2024}))
        self.assertIn("galat", self.alat.jalankan("hapus_semua", {}))


class TesHitung(unittest.TestCase):
    def setUp(self):
        self.alat = Alat(buat_db())

    def test_pertumbuhan_dasar_sebanding(self):
        h = self.alat.pertumbuhan("BBCA", "laba_bersih_induk", 2023, 2024)
        self.assertEqual(h["persen"], "12.74")
        self.assertTrue(h["dasar_sebanding"])
        self.assertEqual(h["awal"]["sumber"]["tahun_laporan"], 2024)

    def test_pertumbuhan_rugi(self):
        h = self.alat.pertumbuhan("GOTO", "laba_bersih_induk", 2023, 2024)
        self.assertEqual(h["persen"], "-94.30")
        self.assertIn("rugi menyusut", h["catatan"])

    def test_rasio(self):
        h = self.alat.rasio("UNVR", "margin_kotor", 2024)
        self.assertEqual((h["nilai"], h["nilai_teks"]), ("47.58", "47,58%"))
        self.assertIn("rasio", self.alat.jalankan("rasio", {"emiten": "UNVR", "nama": "xyz", "tahun": 2024})
                      ["galat"])

    def test_bandingkan_menolak_mata_uang_berbeda(self):
        h = self.alat.bandingkan(["ADRO", "BBCA"], "laba_bersih_induk", 2024)
        self.assertFalse(h["tersedia"])
        self.assertIn("kurs", h["alasan"])

    def test_bandingkan_peringkat(self):
        h = self.alat.bandingkan(["TLKM", "BBCA"], "laba_bersih_induk", 2024)
        self.assertEqual([p["emiten"] for p in h["peringkat"]], ["BBCA", "TLKM"])

    def test_cari_baris(self):
        h = self.alat.cari_baris("UNVR", "pemasaran")
        self.assertEqual((h["jumlah"], h["baris"][0]["sumber"]), (1, "Laporan keuangan 2024, hal. 7"))


class TesPenilaian(unittest.TestCase):
    def test_angka_dengan_toleransi(self):
        kunci = {"tipe": "angka", "nilai": "1000000", "satuan": "IDR"}
        self.assertTrue(nilai_jawaban(kunci, {"tipe": "angka", "nilai": "1004000"})[0])
        self.assertFalse(nilai_jawaban(kunci, {"tipe": "angka", "nilai": "1010000"})[0])

    def test_persen_dan_alternatif(self):
        kunci = {"tipe": "angka", "nilai": "12.7411", "satuan": "persen"}
        self.assertTrue(nilai_jawaban(kunci, {"tipe": "angka", "nilai": "12.74"})[0])
        self.assertFalse(nilai_jawaban(kunci, {"tipe": "angka", "nilai": "12.9"})[0])
        kunci = {"tipe": "angka", "nilai": "100", "satuan": "IDR", "alternatif": ["80"]}
        self.assertTrue(nilai_jawaban(kunci, {"tipe": "angka", "nilai": "80"})[0])

    def test_pilihan_dan_tidak_tersedia(self):
        self.assertTrue(nilai_jawaban({"tipe": "pilihan", "nilai": "UNVR"},
                                      {"tipe": "pilihan", "nilai": "unvr"})[0])
        self.assertTrue(nilai_jawaban({"tipe": "tidak_tersedia"}, {"tipe": "tidak_tersedia"})[0])
        self.assertFalse(nilai_jawaban({"tipe": "tidak_tersedia"}, {"tipe": "angka", "nilai": "1"})[0])

    def test_rencana_diambil_dari_jalur(self):
        alat = Alat(buat_db())
        soal = {"kunci": {"tipe": "pilihan"}, "rencana": [
            {"alat": "bandingkan", "argumen": {"emiten": ["TLKM", "BBCA"], "ukuran": "laba_bersih_induk",
                                               "tahun": 2024}, "ambil": "peringkat.0.emiten"}]}
        self.assertEqual(jawab_dengan_rencana(alat, soal), {"tipe": "pilihan", "nilai": "BBCA"})

    def test_id_pertanyaan_unik(self):
        ids = [s[0] for s in SPESIFIKASI]
        self.assertEqual(len(ids), len(set(ids)))


SOAL = [
    {"id": "A1", "kategori": "lookup", "pertanyaan": "Total aset | BBCA?", "rencana": [],
     "kunci": {"tipe": "angka", "nilai": "100", "satuan": "IDR"}},
    {"id": "A2", "kategori": "jebakan", "pertanyaan": "Laba kotor TLKM?", "rencana": [],
     "kunci": {"tipe": "tidak_tersedia"}},
]


class TesHasil(unittest.TestCase):
    def test_jalankan_uji_mencatat_galat_sebagai_salah(self):
        def penjawab(soal):
            if soal["id"] == "A2":
                raise RuntimeError("koneksi putus")
            return {"tipe": "angka", "nilai": "100"}, [{"alat": "ambil_angka", "argumen": {}}]
        hasil = jalankan_uji(SOAL, penjawab, "contoh")
        self.assertEqual([h["benar"] for h in hasil], [True, False])
        self.assertEqual(hasil[1]["jawaban"]["tipe"], "galat")
        self.assertIn("koneksi putus", hasil[1]["jawaban"]["pesan"])
        self.assertEqual(hasil[0]["jejak"][0]["alat"], "ambil_angka")

    def test_ringkasan_membandingkan_sistem(self):
        benar = jalankan_uji(SOAL, lambda s: ({"tipe": "angka", "nilai": "100"} if s["id"] == "A1"
                                              else {"tipe": "tidak_tersedia"}, []), "rencana")
        salah = jalankan_uji(SOAL, lambda s: ({"tipe": "angka", "nilai": "5"}, []), "agen")
        teks = ringkasan_markdown({"rencana": benar, "agen": salah})
        self.assertIn("| lookup | 1/1 | 0/1 |", teks)
        self.assertIn("| **Total** | **2/2 (100%)** | **0/2 (0%)** |", teks)
        self.assertIn("Total aset \\| BBCA?", teks)  # tanda | di-escape
        self.assertIn("| `agen` | A1 |", teks)  # daftar jawaban salah

    def test_tulis_baca_dan_rencana_di_kolom_pertama(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            tulis_jsonl(folder / "zeta.jsonl", jalankan_uji(SOAL, lambda s: ({"tipe": "x"}, []), "zeta"))
            tulis_jsonl(folder / "rencana.jsonl",
                        jalankan_uji(SOAL, lambda s: ({"tipe": "tidak_tersedia"}, []), "rencana"))
            self.assertEqual(len(baca_jsonl(folder / "zeta.jsonl")), 2)
            teks = tulis_ringkasan(folder).read_text(encoding="utf-8")
        self.assertIn("| Kategori | `rencana` | `zeta` |", teks)


if __name__ == "__main__":
    unittest.main()
