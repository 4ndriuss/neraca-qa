"""Tes agen dengan LLM tiruan (tanpa jaringan) dan konversi adaptor Gemini."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from neraca.agen.inti import (  # noqa: E402
    ALAT_JAWAB,
    Agen,
    Balasan,
    PanggilanAlat,
    bersihkan_nilai,
    jawaban_terstruktur,
    muat_env,
)
from neraca.alat import DEFINISI_ALAT, Alat  # noqa: E402
from neraca.evaluasi.pertanyaan import keterangan_sistem, penjawab_agen  # noqa: E402
from test_alat import buat_db  # noqa: E402


class SesiTiruan:
    def __init__(self, naskah, catatan):
        self.naskah, self.catatan = naskah, catatan

    def _berikut(self):
        return self.naskah.pop(0) if self.naskah else Balasan()

    def kirim_pengguna(self, teks):
        self.catatan.append(("pengguna", teks))
        return self._berikut()

    def kirim_hasil(self, hasil):
        self.catatan.append(("hasil", hasil))
        return self._berikut()


class PenyediaTiruan:
    """LLM tiruan yang membalas sesuai naskah dan mencatat semua yang diterimanya."""

    nama = "tiruan"

    def __init__(self, naskah):
        self.naskah, self.catatan, self.alat = list(naskah), [], None

    def mulai(self, system, definisi_alat):
        self.alat = [d["name"] for d in definisi_alat]
        return SesiTiruan(self.naskah, self.catatan)


def panggil(nama, **argumen):
    return Balasan(panggilan=[PanggilanAlat(f"id-{nama}", nama, argumen)], token_masuk=100, token_keluar=10)


class TesLoopAgen(unittest.TestCase):
    def setUp(self):
        self.alat = Alat(buat_db())

    def test_alat_lalu_jawab_akhir(self):
        llm = PenyediaTiruan([
            panggil("pertumbuhan", emiten="BBCA", pos="laba_bersih_induk", tahun_awal=2023, tahun_akhir=2024),
            panggil("jawab_akhir", teks="Naik 12,74% (hal. 11).", tipe="angka", nilai="12.74"),
        ])
        hasil = Agen(self.alat, llm).tanya("Laba BCA naik berapa persen?")
        self.assertEqual(hasil.jawaban, {"tipe": "angka", "nilai": "12.74"})
        self.assertEqual(hasil.teks, "Naik 12,74% (hal. 11).")
        self.assertEqual([j["alat"] for j in hasil.jejak], ["pertumbuhan", "jawab_akhir"])
        self.assertEqual(hasil.jejak[0]["ringkasan"], "12,74%")
        self.assertEqual((hasil.langkah, hasil.token_masuk, hasil.token_keluar), (2, 200, 20))
        # hasil alat dikirim balik ke LLM, terkait dengan panggilannya
        jenis, kiriman = llm.catatan[1]
        self.assertEqual(jenis, "hasil")
        self.assertEqual(kiriman[0][0].id, "id-pertumbuhan")
        self.assertEqual(kiriman[0][1]["persen"], "12.74")
        self.assertIn("jawab_akhir", llm.alat)

    def test_diingatkan_bila_lupa_jawab_akhir(self):
        llm = PenyediaTiruan([Balasan(teks="Jawabannya UNVR."),
                              panggil("jawab_akhir", teks="UNVR", tipe="pilihan", nilai="unvr")])
        hasil = Agen(self.alat, llm).tanya("Margin siapa lebih tinggi?")
        self.assertEqual(hasil.jawaban, {"tipe": "pilihan", "nilai": "UNVR"})
        self.assertIn("jawab_akhir", llm.catatan[1][1])

    def test_galat_bila_tidak_pernah_menjawab(self):
        llm = PenyediaTiruan([Balasan(teks="Hmm."), Balasan(teks="Masih berpikir.")])
        hasil = Agen(self.alat, llm, maks_langkah=4).tanya("?")
        self.assertEqual(hasil.jawaban["tipe"], "galat")
        self.assertIn("jawab_akhir", hasil.jawaban["pesan"])

    def test_batas_langkah(self):
        llm = PenyediaTiruan([panggil("daftar_pos")] * 10)
        hasil = Agen(self.alat, llm, maks_langkah=3).tanya("?")
        self.assertEqual(hasil.jawaban["tipe"], "galat")
        self.assertEqual(len(hasil.jejak), 3)

    def test_galat_alat_dikirim_ke_llm(self):
        llm = PenyediaTiruan([panggil("ambil_angka", emiten="XXXX", pos="total_aset", tahun=2024),
                              panggil("jawab_akhir", teks="Tidak ada.", tipe="tidak_tersedia")])
        hasil = Agen(self.alat, llm).tanya("?")
        self.assertIn("galat", llm.catatan[1][1][0][1])
        self.assertTrue(hasil.jejak[0]["ringkasan"].startswith("galat"))
        self.assertEqual(hasil.jawaban, {"tipe": "tidak_tersedia"})

    def test_penjawab_agen_hanya_memberi_pertanyaan(self):
        llm = PenyediaTiruan([panggil("jawab_akhir", teks="x", tipe="tidak_tersedia")])
        soal = {"id": "A", "pertanyaan": "Total aset BBCA 2030?", "rencana": [{"alat": "rahasia"}],
                "kunci": {"tipe": "tidak_tersedia"}}
        jawaban, jejak = penjawab_agen(Agen(self.alat, llm))(soal)
        self.assertEqual(llm.catatan, [("pengguna", "Total aset BBCA 2030?")])
        self.assertEqual((jawaban["tipe"], jawaban["teks"], jawaban["langkah"]), ("tidak_tersedia", "x", 1))


class TesNilai(unittest.TestCase):
    def test_bersihkan_nilai(self):
        for masuk, keluar in [("12.74", "12.74"), ("12,74%", "12.74"), ("-94,30", "-94.30"),
                              ("54836305000000", "54836305000000"), ("54.836.305.000.000", "54836305000000"),
                              ("Rp 1,380,013,000", "1380013000"), ("1.234.567,89", "1234567.89"),
                              ("0.74x", "0.74"), ("−16.41%", "-16.41")]:
            self.assertEqual(bersihkan_nilai(masuk), keluar, masuk)
        self.assertIsNone(bersihkan_nilai("tidak ada"))

    def test_jawaban_terstruktur(self):
        self.assertEqual(jawaban_terstruktur({"tipe": "angka", "nilai": "x"})["tipe"], "galat")
        self.assertEqual(jawaban_terstruktur({"tipe": "bingung"})["tipe"], "galat")


class TesLainnya(unittest.TestCase):
    def test_muat_env_tidak_menimpa(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("# komentar\nNERACA_UJI_A=\"satu\"\nNERACA_UJI_B=dua\n", encoding="utf-8")
            os.environ["NERACA_UJI_B"] = "sudah ada"
            try:
                muat_env(env)
                self.assertEqual(os.environ["NERACA_UJI_A"], "satu")
                self.assertEqual(os.environ["NERACA_UJI_B"], "sudah ada")
            finally:
                os.environ.pop("NERACA_UJI_A", None)
                os.environ.pop("NERACA_UJI_B", None)

    def test_keterangan_sistem(self):
        self.assertIn("Gemini", keterangan_sistem("gemini-3.8-flash"))
        self.assertIn("tanpa LLM", keterangan_sistem("rencana"))


class TesAdaptorGemini(unittest.TestCase):
    def setUp(self):
        try:
            import google.genai  # noqa: F401
        except ImportError:
            self.skipTest("google-genai belum terpasang")

    def test_deklarasi_fungsi(self):
        from neraca.agen.gemini import deklarasi_fungsi

        tool = deklarasi_fungsi(DEFINISI_ALAT + [ALAT_JAWAB])
        nama = [f.name for f in tool.function_declarations]
        self.assertEqual(nama[-1], "jawab_akhir")
        self.assertEqual(len(nama), len(DEFINISI_ALAT) + 1)
        ambil = next(f for f in tool.function_declarations if f.name == "ambil_angka")
        self.assertEqual(ambil.parameters_json_schema["required"], ["emiten", "pos", "tahun"])

    def test_balasan_dari_respons(self):
        from neraca.agen.gemini import _balasan

        bagian = [SimpleNamespace(function_call=None, text="pikiran", thought=True),
                  SimpleNamespace(function_call=None, text="Sebentar.", thought=False),
                  SimpleNamespace(function_call=SimpleNamespace(id="c1", name="ambil_angka",
                                                                args={"emiten": "BBCA"}), text=None)]
        respons = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=bagian))],
            usage_metadata=SimpleNamespace(prompt_token_count=50, candidates_token_count=5,
                                           thoughts_token_count=7))
        b = _balasan(respons)
        self.assertEqual(b.teks, "Sebentar.")
        self.assertEqual((b.panggilan[0].id, b.panggilan[0].nama, b.panggilan[0].argumen),
                         ("c1", "ambil_angka", {"emiten": "BBCA"}))
        self.assertEqual((b.token_masuk, b.token_keluar), (50, 12))

    def test_respons_kosong(self):
        from neraca.agen.gemini import _balasan

        b = _balasan(SimpleNamespace(candidates=None, usage_metadata=None))
        self.assertEqual((b.teks, b.panggilan), ("", []))


if __name__ == "__main__":
    unittest.main()
