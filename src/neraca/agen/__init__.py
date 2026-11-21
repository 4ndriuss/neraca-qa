"""Agen LLM yang menjawab pertanyaan laporan keuangan dengan memanggil alat.

Cara pakai::

    neraca-tanya "Laba bersih BCA 2024 naik berapa persen?"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from neraca.agen.inti import Agen, HasilAgen, muat_env


def buat_agen(model: str | None = None, db: Path = Path("data/processed/neraca.db"),
              maks_langkah: int = 8) -> Agen:
    """Agen Gemini siap pakai. API key dibaca dari .env atau variabel lingkungan."""
    from neraca.agen.gemini import MODEL_BAWAAN, PenyediaGemini
    from neraca.alat import Alat

    muat_env()
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise RuntimeError("GEMINI_API_KEY belum diatur. Buat file .env berisi "
                           "GEMINI_API_KEY=... (lihat .env.example).")
    return Agen(Alat(db), PenyediaGemini(model or MODEL_BAWAAN), maks_langkah)


def cetak(hasil: HasilAgen) -> None:
    print(hasil.teks or f"(tanpa teks; jawaban: {hasil.jawaban})")
    print("\nJejak alat:")
    for j in hasil.jejak:
        if j["alat"] != "jawab_akhir":
            print(f"  - {j['alat']}({', '.join(f'{k}={v}' for k, v in j['argumen'].items())})"
                  f" -> {j.get('ringkasan', '')}")
    print(f"\n{hasil.langkah} langkah, {hasil.token_masuk:,} token masuk, {hasil.token_keluar:,} token keluar")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tanya laporan keuangan emiten BEI ke agen LLM.")
    parser.add_argument("pertanyaan", help="pertanyaan dalam Bahasa Indonesia")
    parser.add_argument("--model", help="model Gemini (bawaan: gemini-3.8-flash)")
    args = parser.parse_args(argv)
    try:
        agen = buat_agen(args.model)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    cetak(agen.tanya(args.pertanyaan))
    return 0
