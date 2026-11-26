"""Tes demo Streamlit secara headless (tanpa browser dan tanpa panggilan API)."""

import sys
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
APP = AKAR / "app" / "streamlit_app.py"
DB = AKAR / "data" / "processed" / "neraca.db"

sys.path.insert(0, str(AKAR / "src"))


class TesDemoStreamlit(unittest.TestCase):
    def setUp(self):
        try:
            import streamlit  # noqa: F401
        except ImportError:
            self.skipTest("streamlit belum terpasang")
        if not DB.exists():
            self.skipTest("data/processed/neraca.db belum dibuat")

    def test_muat_awal_dan_semua_mode_kalkulator(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(APP), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, f"Ada exception saat memuat app: {at.exception}")
        self.assertTrue(any("Neraca QA" in t.value for t in at.title))
        # Mode awal: Ambil Angka menghasilkan metrik
        self.assertGreaterEqual(len(at.metric), 1)

        # Uji seluruh mode kalkulator tanpa LLM
        for mode in ("Pertumbuhan YoY", "Rasio Keuangan", "Bandingkan Emiten", "Cari Baris PDF"):
            at.radio[0].set_value(mode).run()
            self.assertFalse(at.exception, f"Exception pada mode {mode}: {at.exception}")


if __name__ == "__main__":
    unittest.main()
