"""Tes pembangun database SQLite."""

import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neraca.db import bangun, koneksi  # noqa: E402


def tulis(path: Path, baris: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(baris[0]))
        w.writeheader()
        w.writerows(baris)


LAPORAN = dict(nama_entitas="PT Bank Contoh Tbk.", tanggal_akhir_periode="2024-12-31",
               periode_penyampaian="Tahunan / Annual", mata_uang_penyajian="Rupiah / IDR",
               pembulatan="Jutaan / In Million", jenis_laporan="Diaudit / Audited",
               jumlah_fakta=1, jumlah_kosong=0, jumlah_konflik=0,
               sektor="G. Financials", subsektor="G1. Banks")


class TesBangun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.processed, self.raw = root / "processed", root / "raw"
        self.processed.mkdir()
        self.raw.mkdir()
        (self.raw / "BBCA_2024_lk.pdf").write_bytes(b"%PDF-1.4")
        tulis(self.processed / "xbrl_laporan.csv", [
            {"emiten": "BBCA", "tahun_laporan": 2023, "file_zip": "BBCA_2023_xbrl.zip",
             **{**LAPORAN, "nama_entitas": "Nama Lama"}},
            {"emiten": "BBCA", "tahun_laporan": 2024, "file_zip": "BBCA_2024_xbrl.zip", **LAPORAN},
        ])
        tulis(self.processed / "akun_standar.csv", [
            dict(emiten="BBCA", tahun_laporan=2024, tahun=2024, pos="total_aset",
                 nilai="1449301328000000", mata_uang="IDR", halaman=8, laporan="posisi_keuangan",
                 label="JUMLAH ASET", induk="", status_verifikasi="persis", penilaian="benar", urutan=26),
        ])
        tulis(self.processed / "pdf_BBCA_2024_lk_cocok.csv", [
            dict(halaman=8, urutan=1, induk="", label="Kas", tahun=2024, nilai="29315878000000",
                 teks_nilai="29.315.878", status="persis", konsep="Cash", laporan="posisi_keuangan",
                 mata_uang="IDR", konteks="ASET"),
        ])
        tulis(self.processed / "xbrl_fakta.csv", [
            dict(emiten="BBCA", tahun_laporan=2024, konsep="Assets", nilai="1449301328000000",
                 mata_uang="IDR", unit="IDR", jenis_periode="instant", tahun_periode=2024,
                 dimensi="", file_html="4220000.html"),
            dict(emiten="BBCA", tahun_laporan=2024, konsep="CommonStocks", nilai="1",
                 mata_uang="IDR", unit="IDR", jenis_periode="instant", tahun_periode=2024,
                 dimensi="CommonStocksMember", file_html="4410000.html"),
        ])
        self.db = root / "neraca.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_isi_tabel(self):
        jumlah = bangun(self.processed, self.db, self.raw)
        self.assertEqual(jumlah["emiten"], 1)
        self.assertEqual(jumlah["laporan"], 2)
        self.assertEqual(jumlah["pos"], 36)
        self.assertEqual(jumlah["angka"], 1)
        self.assertEqual(jumlah["baris_pdf"], 1)
        self.assertEqual(jumlah["xbrl_fakta"], 1)  # fakta berdimensi tidak dimuat

    def test_emiten_memakai_nama_terbaru(self):
        bangun(self.processed, self.db, self.raw)
        with koneksi(self.db) as con:
            e = con.execute("SELECT * FROM emiten").fetchone()
        self.assertEqual((e["nama"], e["subsektor"]), ("PT Bank Contoh Tbk.", "G1. Banks"))

    def test_nilai_tersimpan_tepat_dan_file_pdf(self):
        bangun(self.processed, self.db, self.raw)
        with koneksi(self.db) as con:
            a = con.execute("SELECT nilai, halaman FROM angka WHERE pos='total_aset'").fetchone()
            pdf = dict(con.execute("SELECT tahun_laporan, file_pdf FROM laporan").fetchall())
        self.assertEqual(Decimal(a["nilai"]), Decimal("1449301328000000"))
        self.assertEqual(a["halaman"], 8)
        self.assertEqual(pdf, {2023: None, 2024: "BBCA_2024_lk.pdf"})

    def test_bangun_ulang_menimpa(self):
        bangun(self.processed, self.db, self.raw)
        jumlah = bangun(self.processed, self.db, self.raw)
        self.assertEqual(jumlah["angka"], 1)
        with koneksi(self.db) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM angka").fetchone()[0], 1)

    def test_tanpa_xbrl_laporan_gagal(self):
        (self.processed / "xbrl_laporan.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            bangun(self.processed, self.db, self.raw)


if __name__ == "__main__":
    unittest.main()
