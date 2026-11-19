"""Tes ekstraktor tabel PDF dan pencocokan dengan XBRL.

Kata-kata halaman dibuat manual dengan posisi yang meniru laporan BBCA 2024
(label di x=79, catatan di x=360, kolom 2024 dan 2023 rata kanan di x=475 dan
x=544, jarak baris rapat 8,5 pt dan baris kosong 17 pt).
"""

import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neraca.evaluasi.pdf_vs_xbrl import FaktaRingkas, Pencocok, cocokkan  # noqa: E402
from neraca.extract.pdf_tabel import (  # noqa: E402
    deteksi_skala,
    ekstrak_halaman,
    parse_angka_id,
    parse_rentang,
)

LEBAR_HURUF = 4.5


class Halaman:
    """Penyusun kata halaman tiruan dalam format ``pdfplumber.extract_words``."""

    def __init__(self) -> None:
        self.kata: list[dict] = []
        self.y = 100.0
        self._tambah("Catatan", 357)
        self._tambah("2024", 435)
        self._tambah("2023", 507)

    def _tambah(self, teks: str, x0: float, x1: float | None = None) -> None:
        x1 = x1 if x1 is not None else x0 + len(teks) * LEBAR_HURUF
        self.kata.append({"text": teks, "x0": x0, "x1": x1, "top": self.y, "bottom": self.y + 8})

    def baris(self, label: str = "", catatan: str = "", n2024: str = "", n2023: str = "",
              x: float = 79.2, jarak: float = 17.0) -> "Halaman":
        self.y += jarak
        posisi = x
        for kata in label.split():
            self._tambah(kata, posisi)
            posisi += len(kata) * LEBAR_HURUF + 2.2
        if catatan:
            self._tambah(catatan, 361.7)
        if n2024:
            self._tambah(n2024, 475.2 - len(n2024) * LEBAR_HURUF, 475.2)
        if n2023:
            self._tambah(n2023, 544.5 - len(n2023) * LEBAR_HURUF, 544.5)
        return self

    def lanjut(self, *args, **kwargs) -> "Halaman":
        return self.baris(*args, jarak=8.5, **kwargs)

    def ekstrak(self, teks_halaman: str = "(Dalam jutaan Rupiah, kecuali dinyatakan lain)"):
        return ekstrak_halaman(self.kata, 8, teks_halaman)


def per_pos(hasil):
    """{label: {tahun: teks_nilai}} untuk baris bernilai."""
    pos: dict[str, dict[int, str]] = {}
    for b in hasil:
        if b.jenis == "nilai":
            pos.setdefault(b.label, {})[b.tahun] = b.teks_nilai
    return pos


class TesAngka(unittest.TestCase):
    def test_format_indonesia(self):
        self.assertEqual(parse_angka_id("29.315.878"), Decimal(29315878))

    def test_kurung_berarti_negatif(self):
        self.assertEqual(parse_angka_id("(12.137.180)"), Decimal(-12137180))

    def test_strip_berarti_nol(self):
        self.assertEqual(parse_angka_id("-"), Decimal(0))

    def test_desimal_koma(self):
        self.assertEqual(parse_angka_id("12,50"), Decimal("12.50"))

    def test_bukan_angka(self):
        for teks in ("Kas", "2b,2g", "40,43,", "2023:"):
            self.assertIsNone(parse_angka_id(teks), teks)

    def test_skala_halaman(self):
        self.assertEqual(deteksi_skala("(Dalam jutaan Rupiah, kecuali dinyatakan lain)"), 6)
        self.assertEqual(deteksi_skala("(Dalam miliaran Rupiah)"), 9)
        self.assertEqual(deteksi_skala("(Dalam ribuan Dolar AS)"), 3)
        self.assertEqual(deteksi_skala("tanpa keterangan"), 0)

    def test_rentang_halaman(self):
        self.assertEqual(parse_rentang("8-11,14"), [8, 9, 10, 11, 14])


class TesEkstrakHalaman(unittest.TestCase):
    def test_nilai_dan_skala(self):
        hasil = Halaman().baris("Kas", "4", "29.315.878", "21.701.514").ekstrak()
        kas = [b for b in hasil if b.label == "Kas"]
        self.assertEqual({b.tahun: b.nilai for b in kas},
                         {2024: "29315878000000", 2023: "21701514000000"})

    def test_catatan_di_baris_atas_ikut_pos_berikutnya(self):
        # BBCA: "2b,2g,4,37," di atas baris Kas, "40,43" sebaris dengan Kas.
        hasil = (Halaman().baris("ASET").baris(catatan="2b,2g,4,37,")
                 .lanjut("Kas", "40,43", "29.315.878", "21.701.514").ekstrak())
        judul = next(b for b in hasil if b.label == "ASET")
        kas = next(b for b in hasil if b.label == "Kas")
        self.assertEqual(judul.jenis, "judul")
        self.assertEqual(judul.catatan, "")
        self.assertEqual(kas.catatan, "2b,2g,4,37,40,43")

    def test_label_terpotong_digabung(self):
        hasil = (Halaman()
                 .baris("Giro pada bank-bank lain - setelah dikurangi cadangan kerugian")
                 .lanjut("penurunan nilai sebesar Rp 638 pada tanggal 31 Desember 2024", x=88.2)
                 .lanjut("(31 Desember 2023: Rp 899)", "40,43", "4.097.199", "5.614.353", x=88.2)
                 .ekstrak())
        pos = per_pos(hasil)
        self.assertEqual(len(pos), 1)
        label = next(iter(pos))
        self.assertTrue(label.startswith("Giro pada bank-bank lain"))
        self.assertIn("Rp 638", label)
        self.assertEqual(pos[label], {2024: "4.097.199", 2023: "5.614.353"})

    def test_angka_di_label_bukan_nilai(self):
        hasil = (Halaman().baris("Aset tetap - setelah dikurangi akumulasi penyusutan sebesar")
                 .lanjut("Rp 9.899.706 pada tanggal 31 Desember 2024", "16", "28.250.624",
                         "26.824.744", x=88.2).ekstrak())
        nilai = [b.teks_nilai for b in hasil if b.jenis == "nilai"]
        self.assertEqual(sorted(nilai), ["26.824.744", "28.250.624"])

    def test_sub_pos_dengan_induk(self):
        hasil = (Halaman().baris("Kredit yang diberikan - setelah dikurangi cadangan")
                 .lanjut("Pihak berelasi", "46", "7.174.457", "8.406.659", x=88.2)
                 .lanjut("Pihak ketiga", "", "861.511.753", "750.481.180", x=88.2)
                 .baris("JUMLAH ASET", "", "1.449.301.328", "1.408.107.010").ekstrak())
        induk = {b.label: b.induk for b in hasil if b.jenis == "nilai"}
        self.assertEqual(induk["Pihak berelasi"], "Kredit yang diberikan - setelah dikurangi cadangan")
        self.assertEqual(induk["Pihak ketiga"], induk["Pihak berelasi"])
        self.assertEqual(induk["JUMLAH ASET"], "")

    def test_judul_kapital_terpotong(self):
        hasil = (Halaman().baris("LABA BERSIH YANG DAPAT DIATRIBUSIKAN")
                 .lanjut("KEPADA:")
                 .baris("Pemilik entitas induk", "", "54.836.305", "48.639.122", x=88.2).ekstrak())
        judul = [b.label for b in hasil if b.jenis == "judul"]
        self.assertEqual(judul, ["LABA BERSIH YANG DAPAT DIATRIBUSIKAN KEPADA:"])

    def test_judul_kapital_berjarak_tidak_digabung(self):
        hasil = (Halaman().baris("LIABILITAS, DANA SYIRKAH TEMPORER, DAN EKUITAS")
                 .baris("LIABILITAS").baris("Utang pajak", "20b", "626.355", "1.727.910").ekstrak())
        judul = [b.label for b in hasil if b.jenis == "judul"]
        self.assertEqual(judul, ["LIABILITAS, DANA SYIRKAH TEMPORER, DAN EKUITAS", "LIABILITAS"])

    def test_negatif_dan_nilai_penuh(self):
        hasil = (Halaman().baris("Beban bunga", "", "(12.137.180)", "(11.954.918)")
                 .baris("LABA PER SAHAM (nilai penuh)", "35", "445", "395").ekstrak())
        beban = next(b for b in hasil if b.label == "Beban bunga" and b.tahun == 2024)
        eps = next(b for b in hasil if b.label.startswith("LABA PER SAHAM") and b.tahun == 2024)
        self.assertEqual(beban.nilai, "-12137180000000")
        self.assertEqual((eps.nilai, eps.skala), ("445", 0))

    def test_berhenti_di_catatan_kaki(self):
        hasil = (Halaman().baris("Kas", "4", "1", "2")
                 .baris("Catatan atas laporan keuangan konsolidasian merupakan bagian yang tidak")
                 .lanjut("terpisahkan dari laporan keuangan secara keseluruhan.").ekstrak())
        self.assertEqual([b.label for b in hasil], ["Kas", "Kas"])

    def test_tanpa_header_tidak_ada_hasil(self):
        kata = [{"text": "Kas", "x0": 79, "x1": 92, "top": 100, "bottom": 108}]
        self.assertEqual(ekstrak_halaman(kata, 1), [])


class TesPencocok(unittest.TestCase):
    def setUp(self):
        self.p = Pencocok([
            FaktaRingkas("Cash", Decimal(29315878), "4220000.html"),
            FaktaRingkas("InterestExpenses", Decimal(12532290), "4322000.html"),
            FaktaRingkas("CurrentAccountsWithOtherBanksThirdParties", Decimal(4097837), "4220000.html"),
            FaktaRingkas("AllowanceForCurrentAccountsWithOtherBanks", Decimal(638), "4220000.html"),
        ])

    def test_persis(self):
        self.assertEqual(self.p.cocokkan(Decimal(29315878)), ("persis", "Cash"))

    def test_tanda_terbalik(self):
        self.assertEqual(self.p.cocokkan(Decimal(-12532290))[0], "tanda_terbalik")

    def test_turunan_bruto_dikurangi_cadangan(self):
        status, konsep = self.p.cocokkan(Decimal(4097199))
        self.assertEqual(status, "turunan")
        self.assertIn("ThirdParties - Allowance", konsep)

    def test_tidak_cocok(self):
        self.assertEqual(self.p.cocokkan(Decimal(123))[0], "tidak_cocok")


class TesCocokkanCsv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tulis(self, nama, kolom, baris):
        path = self.folder / nama
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=kolom)
            w.writeheader()
            w.writerows(baris)
        return path

    def test_gabungan_dan_saldo_awal(self):
        kolom_x = ["emiten", "tahun_laporan", "konsep", "nilai", "tahun_periode", "dimensi", "file_html"]
        xbrl = self.tulis("x.csv", kolom_x, [
            dict(emiten="BBCA", tahun_laporan=2024, konsep="OtherOperatingExpenses",
                 nilai=21180096, tahun_periode=2024, dimensi="", file_html="4322000.html"),
            dict(emiten="BBCA", tahun_laporan=2024, konsep="CashAndCashEquivalentsCashFlows",
                 nilai=124395987, tahun_periode=2023, dimensi="", file_html="4510000.html"),
        ])
        kolom_p = ["halaman", "urutan", "induk", "label", "jenis", "tahun", "nilai", "teks_nilai"]
        pdf = self.tulis("p.csv", kolom_p, [
            dict(halaman=10, urutan=1, induk="", label="Beban karyawan", jenis="nilai",
                 tahun=2024, nilai=-17444242, teks_nilai="(17.444.242)"),
            dict(halaman=10, urutan=2, induk="", label="Lain-lain", jenis="nilai",
                 tahun=2024, nilai=-3735854, teks_nilai="(3.735.854)"),
            dict(halaman=15, urutan=1, induk="", label="KAS DAN SETARA KAS, AWAL TAHUN",
                 jenis="nilai", tahun=2024, nilai=124395987, teks_nilai="124.395.987"),
        ])
        hasil = {h.label: h for h in cocokkan(pdf, xbrl, "BBCA", 2024)}
        self.assertEqual(hasil["Beban karyawan"].status, "gabungan")
        self.assertEqual(hasil["Lain-lain"].status, "gabungan")
        self.assertIn("OtherOperatingExpenses", hasil["Lain-lain"].konsep)
        awal = hasil["KAS DAN SETARA KAS, AWAL TAHUN"]
        self.assertEqual(awal.status, "persis")
        self.assertIn("saldo akhir 2023", awal.konsep)


if __name__ == "__main__":
    unittest.main()
