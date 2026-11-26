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


def buat_agen(
    model: str | None = None,
    db: Path = Path("data/processed/neraca.db"),
    maks_langkah: int = 8,
    percobaan: int = 6,
    batas_waktu: float = 120.0,
    penyedia: str = "groq",
    api_key: str | None = None,
) -> Agen:
    """Buat agen siap pakai untuk penyedia ``groq`` (bawaan) atau ``gemini``."""
    from neraca.alat import Alat

    muat_env()
    p = (penyedia or "groq").strip().lower()
    if p == "groq":
        from neraca.agen.groq import MODEL_BAWAAN, PenyediaGroq

        kunci = api_key or os.environ.get("GROQ_API_KEY")
        if not kunci:
            raise RuntimeError(
                "GROQ_API_KEY belum diatur. Buat file .env berisi GROQ_API_KEY=... "
                "(atau isi di sidebar demo). Key gratis: https://console.groq.com/keys"
            )
        return Agen(
            Alat(db),
            PenyediaGroq(model or MODEL_BAWAAN, api_key=kunci, percobaan=percobaan,
                         batas_waktu=batas_waktu),
            maks_langkah,
        )
    if p == "gemini":
        from neraca.agen.gemini import MODEL_BAWAAN, PenyediaGemini

        kunci = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not kunci:
            raise RuntimeError("GEMINI_API_KEY belum diatur. Buat file .env berisi "
                               "GEMINI_API_KEY=... (lihat .env.example).")
        return Agen(
            Alat(db),
            PenyediaGemini(model or MODEL_BAWAAN, api_key=kunci, percobaan=percobaan,
                           batas_waktu=batas_waktu),
            maks_langkah,
        )
    raise RuntimeError(f"Penyedia tidak dikenal: {penyedia!r} (pilih 'groq' atau 'gemini').")


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
    parser.add_argument("--penyedia", choices=["groq", "gemini"], default="groq",
                        help="penyedia LLM (bawaan: groq)")
    parser.add_argument("--model", help="nama model (bawaan groq: qwen/qwen3.8-27b)")
    args = parser.parse_args(argv)
    try:
        agen = buat_agen(args.model, penyedia=args.penyedia)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        hasil = agen.tanya(args.pertanyaan)
    except Exception as exc:  # galat API (key salah, kuota habis, dsb.) tampil sebagai satu pesan
        print(f"Gagal memanggil {agen.penyedia.nama}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    cetak(hasil)
    return 0
