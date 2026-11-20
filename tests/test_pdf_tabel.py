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
    deteksi_format,
    deteksi_mata_uang,
    deteksi_skala,
    ekstrak_halaman,
    jenis_laporan,
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

    def test_skala_kalimat_lain(self):
        # TLKM, ICBP, ADRO, MYOR
        self.assertEqual(deteksi_skala("(Angka dalam tabel disajikan dalam miliaran Rupiah, kecuali "
                                       "dinyatakan lain)"), 9)
        self.assertEqual(deteksi_skala("(Disajikan dalam Jutaan Rupiah,\nKecuali Dinyatakan Lain)"), 6)
        self.assertEqual(deteksi_skala("(Expressed in thousands of US Dollars, except par value)"), 3)
        self.assertEqual(deteksi_skala("(Angka-angka Disajikan dalam Rupiah, kecuali Dinyatakan Lain)"), 0)

    def test_mata_uang(self):
        self.assertEqual(deteksi_mata_uang("(Dinyatakan dalam ribuan Dolar AS, kecuali ...)"), "USD")
        self.assertEqual(deteksi_mata_uang("(Dalam jutaan Rupiah)"), "IDR")

    def test_ribuan_koma(self):
        # ASII, GOTO, ADRO, UNVR, BRIS
        self.assertEqual(parse_angka_id("19,177,524", ","), Decimal(19177524))
        self.assertEqual(parse_angka_id("(165,684)", ","), Decimal(-165684))
        self.assertEqual(parse_angka_id("238.73", ","), Decimal("238.73"))

    def test_kurung_terpisah_tetap_negatif(self):
        self.assertEqual(parse_angka_id("34.270.278.782)"), Decimal(-34270278782))
        self.assertEqual(parse_angka_id("(78.900"), Decimal(-78900))

    def test_deteksi_format(self):
        self.assertEqual(deteksi_format(["19,177,524", "1,747,595", "4"]), ",")
        self.assertEqual(deteksi_format(["29.315.878", "(12.137.180)", "445"]), ".")

    def test_jenis_laporan_dwibahasa(self):
        self.assertEqual(jenis_laporan("LAPORAN POSISI CONSOLIDATED STATEMENT OF KEUANGAN "
                                       "KONSOLIDASIAN"), "posisi_keuangan")
        self.assertEqual(jenis_laporan("LAPORAN ARUS KAS KONSOLIDASIAN"), "arus_kas")
        self.assertEqual(jenis_laporan("LAPORAN PERUBAHAN EKUITAS KONSOLIDASIAN"), "perubahan_ekuitas")

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

    def test_butir_strip_memulai_pos_baru(self):
        # ASII, GOTO, UNVR: "- Pihak berelasi" di bawah "Utang usaha:"
        hasil = (Halaman().baris("Utang usaha:")
                 .lanjut("- Pihak berelasi", "18", "5.158", "5.754", x=81)
                 .lanjut("- Pihak ketiga", "18", "34.889", "34.775", x=81).ekstrak())
        pos = per_pos(hasil)
        self.assertEqual(set(pos), {"Pihak berelasi", "Pihak ketiga"})
        self.assertEqual(next(b.induk for b in hasil if b.label == "Pihak ketiga"), "Utang usaha:")

    def test_kurung_belum_ditutup_berarti_lanjutan(self):
        # BMRI: "Dasar dan dilusian (dalam" / "Rupiah penuh)"
        hasil = (Halaman().baris("LABA PER SAHAM")
                 .baris("Dasar dan dilusian (dalam", x=88)
                 .lanjut("Rupiah penuh)", "", "597,67", "589,93", x=88).ekstrak())
        eps = [b for b in hasil if b.jenis == "nilai"]
        self.assertEqual(eps[0].label, "Dasar dan dilusian (dalam Rupiah penuh)")
        self.assertEqual({b.tahun: b.nilai for b in eps}, {2024: "597.67", 2023: "589.93"})

    def test_satuan_penuh_dari_induk_per_saham(self):
        # TLKM: "(dalam jumlah penuh)" ada di judul induk, bukan di label.
        hasil = (Halaman().baris("LABA BERSIH PER SAHAM (dalam jumlah penuh)")
                 .baris("Laba bersih per saham", "", "238,73", "247,92", x=88)
                 .ekstrak("(Angka dalam tabel disajikan dalam miliaran Rupiah)"))
        eps = next(b for b in hasil if b.jenis == "nilai" and b.tahun == 2024)
        self.assertEqual((eps.nilai, eps.skala), ("238.73", 0))

    def test_nilai_nominal_per_saham_tetap_satuan_halaman(self):
        # UNVR: modal saham menyebut "nilai nominal Rp 2 (nilai penuh) per saham".
        hasil = (Halaman()
                 .baris("Modal saham (seluruhnya disetor penuh dengan nilai nominal Rp 2 (nilai penuh)")
                 .lanjut("per saham)", "", "76.300", "76.300", x=88).ekstrak())
        modal = next(b for b in hasil if b.jenis == "nilai" and b.tahun == 2024)
        self.assertEqual((modal.nilai, modal.skala), ("76300000000", 6))

    def test_label_berlanjut_sesudah_baris_bernilai(self):
        # ASII: angka sejajar baris pertama label, sisa label di bawahnya.
        hasil = (Halaman().baris("Arus kas bersih yang diperoleh dari", "", "69.456", "58.234")
                 .lanjut("aktivitas operasi").ekstrak())
        self.assertEqual(set(per_pos(hasil)), {"Arus kas bersih yang diperoleh dari aktivitas operasi"})
        self.assertFalse([b for b in hasil if b.jenis == "judul"])

    def test_baris_berkurung_sesudah_nilai_adalah_pos_baru(self):
        # UNVR: "(Penurunan)/ kenaikan bersih kas" bukan lanjutan pos di atasnya.
        hasil = (Halaman().baris("Arus kas neto yang digunakan untuk aktivitas pendanaan", "",
                                 "(3.294.681)", "(2.000.000)")
                 .lanjut("(Penurunan)/ kenaikan bersih kas")
                 .lanjut("dan setara kas", "", "(349.573)", "100.000").ekstrak())
        self.assertEqual(set(per_pos(hasil)), {
            "Arus kas neto yang digunakan untuk aktivitas pendanaan",
            "(Penurunan)/ kenaikan bersih kas dan setara kas"})

    def test_kata_sambung_berarti_lanjutan(self):
        # ICBP: gaya Huruf Kapital Tiap Kata, "... dari Aktivitas" / "Operasi".
        hasil = (Halaman().baris("Kas Neto yang Diperoleh dari Aktivitas")
                 .lanjut("Operasi", "", "10.000.000", "9.000.000").ekstrak())
        self.assertEqual(set(per_pos(hasil)), {"Kas Neto yang Diperoleh dari Aktivitas Operasi"})

    def test_konteks_judul_terakhir(self):
        hasil = (Halaman().baris("Laba tahun berjalan yang dapat diatribusikan kepada:")
                 .lanjut("Pemilik entitas induk", "", "1.380", "1.641").ekstrak())
        induk = next(b for b in hasil if b.jenis == "nilai")
        self.assertEqual(induk.konteks, "Laba tahun berjalan yang dapat diatribusikan kepada:")

    def test_subtotal_tanpa_label(self):
        hasil = (Halaman().baris("Pihak berelasi", "", "194.411", "461.097")
                 .lanjut("Pihak ketiga", "", "46.474.028", "36.144.993")
                 .baris("", "", "46.668.439", "36.606.090").ekstrak())
        subtotal = [b for b in hasil if b.jenis == "nilai" and b.label == ""]
        self.assertEqual({b.tahun: b.teks_nilai for b in subtotal},
                         {2024: "46.668.439", 2023: "36.606.090"})


def kata(teks: str, x0: float, top: float, x1: float | None = None) -> dict:
    x1 = x1 if x1 is not None else x0 + len(teks) * LEBAR_HURUF
    return {"text": teks, "x0": x0, "x1": x1, "top": top, "bottom": top + 8}


class TesTataLetakLain(unittest.TestCase):
    def test_dwibahasa_header_dua_baris_dan_label_inggris(self):
        # ICBP/GOTO/ASII: "Catatan/" lalu "Notes 2024 2023"; label Inggris di kanan.
        w = [
            kata("LAPORAN", 94, 80), kata("POSISI", 139, 80), kata("KEUANGAN", 169, 80),
            kata("Catatan/", 248, 146.8), kata("Notes", 252, 155.3),
            kata("2024", 305, 155.3), kata("2023", 364, 155.3),
            kata("Kas", 94, 198.5), kata("dan", 109, 198.5), kata("setara", 124, 198.5),
            kata("kas", 147, 198.5), kata("4", 262, 198.5),
            kata("19,177,524", 302, 198.5, 339), kata("25,143,935", 362, 198.5, 400),
            kata("Cash", 450, 198.5), kata("and", 469, 198.5), kata("cash", 484, 198.5),
            kata("2", 494, 207), kata("Deposito", 94, 207), kata("berjangka", 126, 207),
            kata("5", 262, 207), kata("1,747,595", 306, 207, 339), kata("-", 397, 207, 400),
        ]
        hasil = ekstrak_halaman(w, 13, "(Dinyatakan dalam jutaan Rupiah, kecuali dinyatakan lain)")
        pos = per_pos(hasil)
        self.assertEqual(pos["Kas dan setara kas"], {2024: "19,177,524", 2023: "25,143,935"})
        kas = next(b for b in hasil if b.label == "Kas dan setara kas" and b.tahun == 2024)
        self.assertEqual((kas.nilai, kas.catatan, kas.laporan), ("19177524000000", "4",
                                                                 "posisi_keuangan"))
        deposito = {b.tahun: b.nilai for b in hasil if b.label == "Deposito berjangka"}
        self.assertEqual(deposito, {2024: "1747595000000", 2023: "0"})

    def test_catatan_di_antara_kolom_dan_angka_terpotong(self):
        # MYOR: kolom 2024 | Catatan | 2023, angka satuan penuh terpotong jadi dua kata.
        w = [
            kata("Catatan/", 281, 119.6), kata("2024", 224, 128.2), kata("Notes", 284, 128.2),
            kata("2023", 347, 128.2),
            kata("Pihak", 66, 236.5), kata("ketiga", 84, 236.5),
            kata("4", 217, 236.5, 220), kata("02.141.744.744", 220, 236.5, 264),
            kata("34", 290, 236.5, 297),
            kata("(", 333, 236.5, 336), kata("50.189.161.535)", 336, 236.5, 385),
            kata("Third", 407, 236.5), kata("parties", 423, 236.5),
        ]
        hasil = ekstrak_halaman(w, 10, "(Angka-angka Disajikan dalam Rupiah, kecuali Dinyatakan Lain)")
        nilai = {b.tahun: b.nilai for b in hasil if b.jenis == "nilai"}
        self.assertEqual(nilai, {2024: "402141744744", 2023: "-50189161535"})
        self.assertEqual(hasil[0].catatan, "34")

    def test_header_tanpa_catatan(self):
        # ASII arus kas: header hanya "2024 2023".
        w = [
            kata("LAPORAN", 72, 80), kata("ARUS", 115, 80), kata("KAS", 140, 80),
            kata("2024", 281, 150.8), kata("2023", 334, 150.8),
            kata("Pelunasan", 72, 179.9), kata("pinjaman", 112, 179.9),
            kata("(165,684)", 279, 179.9, 313), kata("(78,715)", 337, 179.9, 367),
            kata("Repayments", 379, 179.9),
        ]
        hasil = ekstrak_halaman(w, 18, "(Dinyatakan dalam miliaran Rupiah)")
        nilai = {b.tahun: b.nilai for b in hasil if b.jenis == "nilai"}
        self.assertEqual(nilai, {2024: "-165684000000000", 2023: "-78715000000000"})
        self.assertEqual(hasil[0].laporan, "arus_kas")

    def test_tiga_kolom_tahun_bertanda_bintang(self):
        # BBRI: "2024  2023*)  2022*)"
        w = [
            kata("Catatan/", 187, 189.6), kata("Notes", 191, 197.6),
            kata("2024", 253, 197.6), kata("2023*)", 320, 197.6), kata("2022*)", 387, 197.6),
            kata("Pinjaman", 64, 229.7), kata("Syariah", 95, 229.7), kata("12", 197, 229.7),
            kata("49.889.082", 256, 229.7, 290), kata("45.937.696", 322, 229.7, 357),
            kata("38.099.994", 392, 229.7, 427), kata("Sharia", 497, 229.7),
        ]
        hasil = ekstrak_halaman(w, 15, "(Disajikan dalam jutaan Rupiah)")
        self.assertEqual({b.tahun for b in hasil if b.jenis == "nilai"}, {2024, 2023, 2022})


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

    def test_konsisten_internal_dan_tanpa_kunci(self):
        kolom_x = ["emiten", "tahun_laporan", "konsep", "nilai", "tahun_periode", "dimensi", "file_html"]
        fakta = [dict(emiten="BMRI", tahun_laporan=2024, konsep=f"Pos{i}", nilai=1000 + i,
                      tahun_periode=2024, dimensi="", file_html="4220000.html") for i in range(25)]
        xbrl = self.tulis("x.csv", kolom_x, fakta)
        kolom_p = ["halaman", "urutan", "induk", "label", "jenis", "tahun", "nilai", "teks_nilai"]
        baris = [
            # 2024: tidak ada di XBRL, tetapi berelasi + ketiga = total
            (19, 1, "Pihak berelasi", 2024, 4299236), (19, 2, "Pihak ketiga", 2024, 4790802),
            (19, 3, "Total", 2024, 9090038),
            # 2022: XBRL tidak punya data tahun ini
            (19, 4, "Pihak ketiga", 2022, 123456),
        ]
        pdf = self.tulis("p.csv", kolom_p, [
            dict(halaman=h, urutan=u, induk="", label=l, jenis="nilai", tahun=t, nilai=v,
                 teks_nilai=str(v)) for h, u, l, t, v in baris])
        hasil = {(h.label, h.tahun): h.status for h in cocokkan(pdf, xbrl, "BMRI", 2024)}
        self.assertEqual(hasil[("Total", 2024)], "konsisten_internal")
        self.assertEqual(hasil[("Pihak berelasi", 2024)], "konsisten_internal")
        self.assertEqual(hasil[("Pihak ketiga", 2022)], "tanpa_kunci")

    def test_kunci_dari_laporan_tahun_berikutnya(self):
        # PTBA: total aset 2024 di PDF final sama dengan angka pembanding di XBRL laporan 2025.
        kolom_x = ["emiten", "tahun_laporan", "konsep", "nilai", "tahun_periode", "dimensi", "file_html"]
        xbrl = self.tulis("x.csv", kolom_x, [
            dict(emiten="PTBA", tahun_laporan=2024, konsep="Assets", nilai=41854334,
                 tahun_periode=2024, dimensi="", file_html="1210000.html"),
            dict(emiten="PTBA", tahun_laporan=2025, konsep="Assets", nilai=41785576,
                 tahun_periode=2024, dimensi="", file_html="1210000.html"),
        ])
        kolom_p = ["halaman", "urutan", "induk", "label", "jenis", "tahun", "nilai", "teks_nilai"]
        pdf = self.tulis("p.csv", kolom_p, [dict(halaman=9, urutan=1, induk="", label="JUMLAH ASET",
                                                 jenis="nilai", tahun=2024, nilai=41785576,
                                                 teks_nilai="41,785,576")])
        h = cocokkan(pdf, xbrl, "PTBA", 2024)[0]
        self.assertEqual(h.status, "laporan_berikut")
        self.assertIn("XBRL laporan 2025", h.konsep)


if __name__ == "__main__":
    unittest.main()
