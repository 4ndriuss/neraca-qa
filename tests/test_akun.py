"""Tes normalisasi nama akun: label PDF -> pos standar.

Setiap kasus diambil dari label asli di laporan audit 2024.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neraca.normalisasi.akun import (  # noqa: E402
    AK,
    LR,
    PK,
    AngkaStandar,
    isi_bagian_induk,
    nilai_pemetaan,
    normalisasi_teks,
    petakan,
    petakan_semua,
    pilih_satu,
)


class TesNormalisasiTeks(unittest.TestCase):
    def test_huruf_kecil_dan_spasi(self):
        self.assertEqual(normalisasi_teks("  JUMLAH   ASET "), "jumlah aset")

    def test_ejaan_nonpengendali(self):
        for teks in ("Kepentingan non-pengendali", "Kepentingan non pengendali",
                     "KEPENTINGAN NONPENGENDALI"):
            self.assertEqual(normalisasi_teks(teks), "kepentingan nonpengendali")

    def test_keterangan_pindahan_dibuang(self):
        self.assertEqual(normalisasi_teks("JUMLAH LABA KOMPREHENSIF (Dipindahkan)"),
                         "jumlah laba komprehensif")
        self.assertEqual(normalisasi_teks("Laba tahun berjalan (saldo dipindahkan ke halaman berikut)"),
                         "laba tahun berjalan")


class TesPetakan(unittest.TestCase):
    def test_laba_bersih_berbagai_label(self):
        for label in ("LABA BERSIH", "Laba tahun berjalan", "Rugi tahun berjalan", "LABA TAHUN BERJALAN"):
            self.assertEqual(petakan(LR, label), "laba_bersih", label)

    def test_pemilik_entitas_induk_tergantung_induk(self):
        self.assertEqual(petakan(LR, "Pemilik entitas induk",
                                 induk="LABA BERSIH YANG DAPAT DIATRIBUSIKAN KEPADA:"),
                         "laba_bersih_induk")
        self.assertEqual(petakan(LR, "Pemilik entitas induk",
                                 induk="LABA KOMPREHENSIF YANG DAPAT DIATRIBUSIKAN KEPADA:"),
                         "laba_komprehensif_induk")

    def test_judul_sejajar_memakai_konteks(self):
        # ADRO: judul dan butir sejajar, sehingga induk kosong.
        self.assertEqual(petakan(LR, "Pemilik entitas induk", induk="",
                                 konteks="Laba tahun berjalan yang dapat diatribusikan kepada:"),
                         "laba_bersih_induk")

    def test_nonpengendali_neraca_vs_laba_rugi(self):
        self.assertEqual(petakan(PK, "Kepentingan nonpengendali"), "kepentingan_nonpengendali")
        self.assertEqual(petakan(LR, "Kepentingan non-pengendali",
                                 induk="Laba yang diatribusikan kepada:"),
                         "laba_bersih_nonpengendali")

    def test_penjualan_di_bawah_beban_bukan_pendapatan(self):
        # MYOR: "Penjualan" di bawah judul "Beban Usaha" adalah beban penjualan.
        self.assertEqual(petakan(LR, "Penjualan bersih"), "pendapatan")
        self.assertIsNone(petakan(LR, "Penjualan", konteks="Beban Usaha"))

    def test_penghasilan_bunga_non_bank_tidak_dipetakan(self):
        self.assertIsNone(petakan(LR, "Penghasilan bunga"))
        self.assertEqual(petakan(LR, "Jumlah pendapatan bunga dan syariah"), "pendapatan_bunga")

    def test_label_umum_mewarisi_judul(self):
        # TLKM: subtotal tanpa label di bawah "(BEBAN) MANFAAT PAJAK PENGHASILAN".
        self.assertEqual(petakan(LR, "", konteks="(BEBAN) MANFAAT PAJAK PENGHASILAN"), "beban_pajak")
        self.assertEqual(petakan(PK, "", konteks="Ekuitas yang dapat diatribusikan kepada pemilik "
                                                 "entitas induk"), "ekuitas_induk")

    def test_subtotal_atribusi_laba_rugi_tidak_dipetakan(self):
        # Isinya berbeda antaremiten (BMRI: laba total, ADRO: jumlah milik induk).
        self.assertIsNone(petakan(LR, "", konteks="Laba yang dapat diatribusikan kepada:"))

    def test_ekuitas_induk_gaya_tlkm(self):
        self.assertEqual(petakan(PK, "Pemilik entitas induk - bersih",
                                 induk="Ekuitas yang dapat diatribusikan kepada:"), "ekuitas_induk")

    def test_arus_kas(self):
        self.assertEqual(petakan(AK, "Kas neto yang diperoleh dari (digunakan untuk) kegiatan operasi"),
                         "arus_kas_operasi")
        self.assertEqual(petakan(AK, "Arus kas bersih yang digunakan untuk aktivitas investasi"),
                         "arus_kas_investasi")
        self.assertEqual(petakan(AK, "(Penurunan)/ kenaikan bersih kas dan setara kas"),
                         "kenaikan_bersih_kas")
        self.assertEqual(petakan(AK, "KAS DAN SETARA KAS, AWAL TAHUN"), "kas_awal_tahun")
        self.assertEqual(petakan(AK, "Dividen kas yang dibayarkan kepada pemegang saham Perseroan"),
                         "dividen_dibayar")
        self.assertIsNone(petakan(AK, "Dividen kas yang dibayarkan kepada kepentingan nonpengendali"))

    def test_jenis_laporan_harus_sesuai(self):
        self.assertIsNone(petakan(AK, "JUMLAH ASET"))

    def test_laba_per_saham(self):
        self.assertEqual(petakan_semua(LR, "Laba per saham - dasar dan dilusian (nilai penuh)"),
                         ["laba_per_saham_dasar", "laba_per_saham_dilusian"])
        self.assertEqual(petakan(LR, "Dilusian", induk="LABA PER SAHAM (dalam Rupiah penuh)"),
                         "laba_per_saham_dilusian")
        self.assertIsNone(petakan(LR, "Laba bersih per ADS (100 saham Seri B per ADS)",
                                  induk="LABA BERSIH PER SAHAM"))


def angka(pos, nilai, halaman=9, urutan=0, tahun=2024, status="persis"):
    return AngkaStandar("XXXX", 2024, tahun, pos, str(nilai), "IDR", halaman, "", "", "", status,
                        "belum_dinilai", urutan)


class TesTabel(unittest.TestCase):
    def test_arus_kas_pilih_kemunculan_terakhir(self):
        # BBCA: arus kas operasi sebelum pajak (pertama) dan sesudah pajak (terakhir).
        terpilih, konflik = pilih_satu([angka("arus_kas_operasi", 66102503, 14, 10),
                                        angka("arus_kas_operasi", 53820229, 14, 12)])
        self.assertEqual(terpilih[0].nilai, "53820229")
        self.assertEqual(len(konflik), 1)

    def test_pos_lain_pilih_kemunculan_pertama(self):
        terpilih, _ = pilih_satu([angka("kas_dan_setara_kas", 1, 9, 1), angka("kas_dan_setara_kas", 2, 15, 1)])
        self.assertEqual(terpilih[0].nilai, "1")

    def test_bagian_induk_tanpa_nonpengendali(self):
        hasil = isi_bagian_induk([angka("total_ekuitas", 45042), angka("laba_bersih", 7006)])
        pos = {a.pos: (a.nilai, a.status_verifikasi) for a in hasil}
        self.assertEqual(pos["ekuitas_induk"], ("45042", "turunan_tanpa_nonpengendali"))
        self.assertEqual(pos["laba_bersih_induk"][0], "7006")

    def test_bagian_induk_tidak_diturunkan_bila_ada_nonpengendali(self):
        hasil = isi_bagian_induk([angka("total_ekuitas", 67044), angka("kepentingan_nonpengendali", 21827)])
        self.assertNotIn("ekuitas_induk", {a.pos for a in hasil})


class TesPenilaian(unittest.TestCase):
    def test_benar_salah_terlewat(self):
        self.assertEqual(nilai_pemetaan("total_aset", "persis", "Assets", "100"), "benar")
        self.assertEqual(nilai_pemetaan("laba_komprehensif", "persis",
                                        "OtherComprehensiveIncome", "100"), "salah")
        self.assertEqual(nilai_pemetaan(None, "persis", "ProfitLoss", "100"), "terlewat")

    def test_belum_dinilai(self):
        self.assertEqual(nilai_pemetaan("total_aset", "gabungan", "Assets", "100"), "belum_dinilai")
        self.assertEqual(nilai_pemetaan("total_aset", "persis", "Assets", "0"), "belum_dinilai")


if __name__ == "__main__":
    unittest.main()
