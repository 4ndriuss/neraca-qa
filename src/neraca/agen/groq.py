"""Adaptor Groq API (OpenAI-compatible Chat Completions) untuk agen.

Hanya memakai pustaka standar Python (``urllib.request``), sehingga tidak perlu
memasang SDK tambahan. Definisi alat dikirim sebagai ``tools`` standar OpenAI,
panggilan fungsi dijalankan oleh agen, dan hasilnya dikirim balik dengan peran
``"tool"``.

API key dibaca dari ``GROQ_API_KEY`` (atau diberikan lewat argumen ``api_key``).
Key gratis bisa dibuat di https://console.groq.com/keys.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from neraca.agen.inti import Balasan, PanggilanAlat

URL_CHAT = "https://api.groq.com/openai/v1/chat/completions"
MODEL_BAWAAN = "qwen/qwen3.8-27b"
_KODE_ULANG = frozenset({408, 429, 500, 502, 503, 504})


class GalatGroq(RuntimeError):
    """Galat saat memanggil Groq API."""


def deklarasi_fungsi(definisi_alat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ubah definisi alat (format ``input_schema``) menjadi format ``tools`` OpenAI/Groq."""
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["input_schema"],
            },
        }
        for d in definisi_alat
    ]


def _urai_argumen(mentah: Any) -> dict[str, Any]:
    """Ubah argumen tool call dari JSON string/dict menjadi dict Python."""
    if isinstance(mentah, dict):
        return mentah
    if not mentah:
        return {}
    try:
        hasil = json.loads(str(mentah))
    except json.JSONDecodeError:
        return {}
    return hasil if isinstance(hasil, dict) else {}


def _balasan(respons: dict[str, Any]) -> tuple[Balasan, dict[str, Any]]:
    """Ekstrak ``Balasan`` agen dan pesan asisten yang bersih untuk riwayat."""
    pilihan = (respons.get("choices") or [{}])[0]
    pesan = pilihan.get("message") or {}
    teks = pesan.get("content") or ""
    panggilan: list[PanggilanAlat] = []
    tool_calls_bersih: list[dict[str, Any]] = []
    for tc in pesan.get("tool_calls") or []:
        fc = tc.get("function") or {}
        nama = fc.get("name") or ""
        if not nama:
            continue
        panggilan.append(PanggilanAlat(tc.get("id"), nama, _urai_argumen(fc.get("arguments"))))
        tool_calls_bersih.append(
            {
                "id": tc.get("id") or f"call_{len(tool_calls_bersih)}",
                "type": "function",
                "function": {
                    "name": nama,
                    "arguments": fc.get("arguments") if isinstance(fc.get("arguments"), str)
                    else json.dumps(fc.get("arguments") or {}, ensure_ascii=False),
                },
            }
        )
    pakai = respons.get("usage") or {}
    balasan = Balasan(
        teks=teks,
        panggilan=panggilan,
        token_masuk=int(pakai.get("prompt_tokens") or 0),
        token_keluar=int(pakai.get("completion_tokens") or 0),
    )
    asisten: dict[str, Any] = {"role": "assistant", "content": teks}
    if tool_calls_bersih:
        asisten["tool_calls"] = tool_calls_bersih
    return balasan, asisten


def _pesan_galat_http(isi: bytes) -> str:
    teks = isi.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(teks)
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            return str(data["error"].get("message") or teks)
    except json.JSONDecodeError:
        pass
    return teks[:300]


_COBA_LAGI = re.compile(r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE)


def _jeda_ulang(pesan: str, retry_after: str | None, bawaan: float) -> float:
    if retry_after:
        try:
            return min(30.0, max(0.5, float(retry_after)))
        except ValueError:
            pass
    m = _COBA_LAGI.search(pesan)
    if m:
        nilai = float(m.group(1))
        detik = nilai / 1000.0 if m.group(2).lower() == "ms" else nilai
        return min(30.0, max(0.5, detik + 0.5))
    return bawaan


def _kirim_http(
    url: str,
    api_key: str,
    muatan: dict[str, Any],
    batas_waktu: float,
    percobaan: int,
) -> dict[str, Any]:
    badan = json.dumps(muatan, ensure_ascii=False).encode("utf-8")
    header = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # User-Agent eksplisit menghindari blokir Cloudflare terhadap default Python-urllib.
        "User-Agent": "neraca-qa/0.1",
    }
    jeda = 2.0
    galat_terakhir: Exception | None = None
    for ke in range(1, max(1, percobaan) + 1):
        req = urllib.request.Request(url, data=badan, headers=header, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=batas_waktu) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            pesan = _pesan_galat_http(exc.read())
            galat_terakhir = GalatGroq(f"HTTP {exc.code}: {pesan}")
            if exc.code not in _KODE_ULANG or ke == percobaan:
                raise galat_terakhir from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            time.sleep(_jeda_ulang(pesan, retry_after, jeda))
            jeda = min(30.0, jeda * 2)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            galat_terakhir = GalatGroq(f"Gagal terhubung ke Groq API: {exc}")
            if ke == percobaan:
                raise galat_terakhir from exc
            time.sleep(jeda)
            jeda = min(30.0, jeda * 2)
    raise galat_terakhir or GalatGroq("Gagal memanggil Groq API.")


class SesiGroq:
    def __init__(
        self,
        kirim: Callable[[dict[str, Any]], dict[str, Any]],
        model: str,
        system: str,
        tools: list[dict[str, Any]],
    ):
        self.kirim = kirim
        self.model = model
        self.tools = tools
        self.riwayat: list[dict[str, Any]] = [{"role": "system", "content": system}]

    def _hasilkan(self) -> Balasan:
        muatan: dict[str, Any] = {
            "model": self.model,
            "messages": self.riwayat,
            "tools": self.tools,
            "tool_choice": "auto",
            "temperature": 0,
        }
        # Model reasoning gpt-oss di tier gratis Groq berbatas 8.000 TPM;
        # reasoning_effort="low" menjaga konsumsi token tetap hemat dan respons cepat.
        if self.model.startswith("openai/gpt-oss-"):
            muatan["reasoning_effort"] = "low"
        respons = self.kirim(muatan)
        balasan, asisten = _balasan(respons)
        self.riwayat.append(asisten)
        return balasan

    def kirim_pengguna(self, teks: str) -> Balasan:
        self.riwayat.append({"role": "user", "content": teks})
        return self._hasilkan()

    def kirim_hasil(self, hasil: list[tuple[PanggilanAlat, dict[str, Any]]]) -> Balasan:
        for p, r in hasil:
            self.riwayat.append(
                {
                    "role": "tool",
                    "tool_call_id": p.id or p.nama,
                    "name": p.nama,
                    "content": json.dumps(r, ensure_ascii=False),
                }
            )
        return self._hasilkan()


class PenyediaGroq:
    """Penyedia Groq Chat Completions dengan coba ulang otomatis saat 429/5xx."""

    def __init__(
        self,
        model: str = MODEL_BAWAAN,
        api_key: str | None = None,
        percobaan: int = 4,
        batas_waktu: float = 30.0,
        url: str = URL_CHAT,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.model = model
        self.nama = f"groq-{model.replace('/', '_')}"
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.percobaan = percobaan
        self.batas_waktu = batas_waktu
        self.url = url
        self._transport = transport

    def mulai(self, system: str, definisi_alat: list[dict[str, Any]]) -> SesiGroq:
        kirim = self._transport or (
            lambda muatan: _kirim_http(self.url, self.api_key, muatan, self.batas_waktu, self.percobaan)
        )
        return SesiGroq(kirim, self.model, system, deklarasi_fungsi(definisi_alat))
