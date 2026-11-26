"""Adaptor Gemini API (Google) untuk agen.

Memakai SDK ``google-genai`` dengan *function calling* manual: definisi alat
dikirim sebagai JSON Schema, panggilan fungsi dijalankan oleh agen, dan hasilnya
dikirim balik sebagai ``function_response``. Konten balasan model ditambahkan ke
riwayat apa adanya, supaya *thought signature* Gemini 3 ikut terkirim kembali.

API key dibaca dari ``GEMINI_API_KEY`` (atau ``GOOGLE_API_KEY``). Di tier
gratis, Google dapat memakai isi permintaan untuk mengembangkan produknya; jangan
kirim data pribadi lewat tier gratis.
"""

from __future__ import annotations

from typing import Any

from neraca.agen.inti import Balasan, PanggilanAlat

MODEL_BAWAAN = "gemini-3.8-flash"


def deklarasi_fungsi(definisi_alat: list[dict[str, Any]]):
    """Ubah definisi alat (format ``input_schema``) menjadi ``types.Tool`` Gemini."""
    from google.genai import types

    return types.Tool(function_declarations=[
        types.FunctionDeclaration(name=d["name"], description=d["description"],
                                  parameters_json_schema=d["input_schema"])
        for d in definisi_alat
    ])


def _balasan(respons) -> Balasan:
    teks, panggilan = [], []
    kandidat = (respons.candidates or [None])[0]
    konten = getattr(kandidat, "content", None)
    for bagian in (getattr(konten, "parts", None) or []):
        if getattr(bagian, "function_call", None):
            fc = bagian.function_call
            panggilan.append(PanggilanAlat(fc.id, fc.name, dict(fc.args or {})))
        elif getattr(bagian, "text", None) and not getattr(bagian, "thought", False):
            teks.append(bagian.text)
    pakai = getattr(respons, "usage_metadata", None)
    return Balasan("".join(teks), panggilan,
                   getattr(pakai, "prompt_token_count", 0) or 0,
                   (getattr(pakai, "candidates_token_count", 0) or 0)
                   + (getattr(pakai, "thoughts_token_count", 0) or 0))


class SesiGemini:
    def __init__(self, klien, model: str, config):
        self.klien, self.model, self.config = klien, model, config
        self.riwayat: list = []

    def _hasilkan(self) -> Balasan:
        respons = self.klien.models.generate_content(model=self.model, contents=self.riwayat,
                                                     config=self.config)
        kandidat = (respons.candidates or [None])[0]
        if getattr(kandidat, "content", None) is not None:
            self.riwayat.append(kandidat.content)  # apa adanya, termasuk thought signature
        return _balasan(respons)

    def kirim_pengguna(self, teks: str) -> Balasan:
        from google.genai import types

        self.riwayat.append(types.Content(role="user", parts=[types.Part.from_text(text=teks)]))
        return self._hasilkan()

    def kirim_hasil(self, hasil: list[tuple[PanggilanAlat, dict[str, Any]]]) -> Balasan:
        from google.genai import types

        bagian = [types.Part(function_response=types.FunctionResponse(id=p.id, name=p.nama, response=r))
                  for p, r in hasil]
        # Gemini API hanya menerima peran "user" dan "model"; hasil fungsi dikirim sebagai "user".
        self.riwayat.append(types.Content(role="user", parts=bagian))
        return self._hasilkan()


class PenyediaGemini:
    """Penyedia Gemini. ``percobaan`` mengatur coba ulang otomatis saat 429/5xx;
    ``batas_waktu`` adalah batas detik per permintaan."""

    def __init__(self, model: str = MODEL_BAWAAN, api_key: str | None = None, percobaan: int = 6,
                 batas_waktu: float = 120.0):
        from google import genai
        from google.genai import types

        self.model = model
        self.nama = model
        # Batas waktu per permintaan (detik); tanpa ini, model yang sedang sibuk bisa membuat
        # panggilan menggantung lama tanpa pesan.
        opsi = types.HttpOptions(timeout=int(batas_waktu * 1000), retry_options=types.HttpRetryOptions(
            attempts=percobaan, initial_delay=4.0, max_delay=60.0,
            http_status_codes=[408, 429, 500, 502, 503, 504]))
        self.klien = genai.Client(api_key=api_key, http_options=opsi) if api_key else \
            genai.Client(http_options=opsi)

    def mulai(self, system: str, definisi_alat: list[dict[str, Any]]) -> SesiGemini:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[deklarasi_fungsi(definisi_alat)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return SesiGemini(self.klien, self.model, config)
