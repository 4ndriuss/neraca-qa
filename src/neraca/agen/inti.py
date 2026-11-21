"""Agen tanya-jawab laporan keuangan yang tidak terikat penyedia LLM.

Agen menerima pertanyaan, membiarkan LLM memanggil alat dari ``neraca.alat``,
menjalankan alat itu, lalu mengirim hasilnya kembali sampai LLM memanggil
``jawab_akhir``. Semua angka berasal dari alat; LLM hanya memilih alat dan
menyusun kalimat.

Penyedia LLM (Gemini, dan nanti yang lain) cukup menyediakan ``Penyedia`` dan
``Sesi`` di bawah ini; loop, system prompt, dan format jawaban sama untuk semua.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from neraca.alat import DEFINISI_ALAT, Alat

SYSTEM_PROMPT = """\
Kamu adalah asisten analis laporan keuangan emiten Bursa Efek Indonesia. Kamu menjawab pertanyaan \
dalam Bahasa Indonesia hanya berdasarkan hasil alat yang tersedia.

Aturan:
1. Setiap angka dalam jawaban harus berasal dari hasil alat. Jangan menghitung, menjumlahkan, \
membulatkan ulang, atau menebak angka sendiri. Untuk persentase perubahan, selisih, rasio, dan \
perbandingan antaremiten, pakai alat pertumbuhan, rasio, atau bandingkan.
2. Pakai kode saham empat huruf. Bila ragu nama emiten atau kode pos, panggil daftar_emiten atau \
daftar_pos lebih dulu.
3. Bila istilah di pertanyaan bisa berarti dua pos berbeda (misalnya "laba bersih" bisa laba total \
atau laba milik entitas induk), ambil keduanya dan sebutkan bedanya di jawaban.
4. Bila alat menyatakan data tidak tersedia, sampaikan alasannya dan jangan menggantinya dengan pos \
lain yang tidak ditanyakan.
5. Sebutkan sumber setiap angka (halaman laporan keuangan atau XBRL). Bila hasil alat memuat \
disajikan_kembali atau peringatan, sampaikan juga.
6. Bila pertanyaan menyebut laporan keuangan tahun tertentu sebagai sumber, isi tahun_laporan.
7. Selalu akhiri dengan memanggil jawab_akhir tepat satu kali, setelah semua alat lain selesai.
"""

NAMA_JAWAB = "jawab_akhir"

ALAT_JAWAB: dict[str, Any] = {
    "name": NAMA_JAWAB,
    "description": "Kirim jawaban akhir untuk pengguna. Panggil sekali, sebagai langkah terakhir.",
    "input_schema": {
        "type": "object",
        "properties": {
            "teks": {"type": "string", "description": "Jawaban lengkap untuk pengguna, dengan sumber."},
            "tipe": {"type": "string", "enum": ["angka", "pilihan", "tidak_tersedia"],
                     "description": "angka untuk nilai atau persen, pilihan untuk jawaban berupa "
                                    "emiten, tidak_tersedia bila datanya tidak ada."},
            "nilai": {"type": "string",
                      "description": "Untuk angka: salin persis field nilai, persen, atau selisih dari "
                                     "hasil alat, tanpa pemisah ribuan (contoh 54836305000000 atau "
                                     "12.74). Untuk pilihan: kode emiten. Kosongkan untuk "
                                     "tidak_tersedia."},
        },
        "required": ["teks", "tipe"],
    },
}


@dataclass
class PanggilanAlat:
    id: str | None
    nama: str
    argumen: dict[str, Any]


@dataclass
class Balasan:
    teks: str = ""
    panggilan: list[PanggilanAlat] = field(default_factory=list)
    token_masuk: int = 0
    token_keluar: int = 0


class Sesi(Protocol):
    def kirim_pengguna(self, teks: str) -> Balasan: ...

    def kirim_hasil(self, hasil: list[tuple[PanggilanAlat, dict[str, Any]]]) -> Balasan: ...


class Penyedia(Protocol):
    nama: str

    def mulai(self, system: str, definisi_alat: list[dict[str, Any]]) -> Sesi: ...


@dataclass
class HasilAgen:
    jawaban: dict[str, Any]  # format penilaian: tipe, nilai
    teks: str
    jejak: list[dict[str, Any]]
    langkah: int
    token_masuk: int
    token_keluar: int


_ANGKA = re.compile(r"-?\d[\d.,]*")


def bersihkan_nilai(nilai: Any) -> str | None:
    """Ubah nilai dari LLM menjadi teks desimal: ``"12,74%"`` -> ``"12.74"``.

    Mengembalikan ``None`` bila tidak ada angka. Bila ada koma dan titik, yang
    terakhir dianggap pemisah desimal.
    """
    teks = str(nilai or "").replace("−", "-").replace(" ", "")
    m = _ANGKA.search(teks)
    if not m:
        return None
    angka = m.group(0).rstrip(".,")
    negatif = angka.startswith("-")
    angka = angka.lstrip("-")
    if "," in angka and "." in angka:
        desimal = "," if angka.rfind(",") > angka.rfind(".") else "."
        ribuan = "." if desimal == "," else ","
        angka = angka.replace(ribuan, "").replace(desimal, ".")
    elif "," in angka:
        bagian = angka.split(",")
        angka = angka.replace(",", "") if all(len(b) == 3 for b in bagian[1:]) else angka.replace(",", ".")
    elif angka.count(".") > 1:
        angka = angka.replace(".", "")
    try:
        hasil = Decimal(angka)
    except InvalidOperation:
        return None
    return str(-hasil if negatif else hasil)


def jawaban_terstruktur(argumen: dict[str, Any]) -> dict[str, Any]:
    tipe = argumen.get("tipe")
    if tipe == "tidak_tersedia":
        return {"tipe": "tidak_tersedia"}
    if tipe == "pilihan":
        return {"tipe": "pilihan", "nilai": str(argumen.get("nilai", "")).strip().upper()}
    if tipe == "angka":
        nilai = bersihkan_nilai(argumen.get("nilai"))
        return {"tipe": "angka", "nilai": nilai} if nilai is not None else \
            {"tipe": "galat", "pesan": f"nilai angka tidak terbaca: {argumen.get('nilai')!r}"}
    return {"tipe": "galat", "pesan": f"tipe jawaban tidak dikenal: {tipe!r}"}


def _ringkas(hasil: dict[str, Any]) -> str:
    if "galat" in hasil:
        return f"galat: {hasil['galat']}"
    if hasil.get("tersedia") is False:
        return f"tidak tersedia: {str(hasil.get('alasan', ''))[:80]}"
    for kunci in ("nilai_teks", "persen_teks"):
        if kunci in hasil:
            return str(hasil[kunci])
    if "peringkat" in hasil:
        return " > ".join(p["emiten"] for p in hasil["peringkat"])
    return ", ".join(sorted(hasil))[:80]


class Agen:
    """Loop agen: pertanyaan -> panggilan alat -> ``jawab_akhir``."""

    def __init__(self, alat: Alat, penyedia: Penyedia, maks_langkah: int = 8):
        self.alat = alat
        self.penyedia = penyedia
        self.maks_langkah = maks_langkah

    def tanya(self, pertanyaan: str) -> HasilAgen:
        sesi = self.penyedia.mulai(SYSTEM_PROMPT, DEFINISI_ALAT + [ALAT_JAWAB])
        balasan = sesi.kirim_pengguna(pertanyaan)
        jejak: list[dict[str, Any]] = []
        masuk, keluar = balasan.token_masuk, balasan.token_keluar
        sudah_diingatkan = False

        for langkah in range(1, self.maks_langkah + 1):
            akhir = next((p for p in balasan.panggilan if p.nama == NAMA_JAWAB), None)
            if akhir is not None:
                jejak.append({"alat": NAMA_JAWAB, "argumen": akhir.argumen})
                return HasilAgen(jawaban_terstruktur(akhir.argumen), str(akhir.argumen.get("teks", "")),
                                 jejak, langkah, masuk, keluar)
            if not balasan.panggilan:
                if sudah_diingatkan:
                    break
                sudah_diingatkan = True
                balasan = sesi.kirim_pengguna(
                    "Panggil jawab_akhir sekarang untuk mengirim jawabanmu.")
            else:
                hasil = []
                for p in balasan.panggilan:
                    keluaran = self.alat.jalankan(p.nama, dict(p.argumen))
                    jejak.append({"alat": p.nama, "argumen": p.argumen, "ringkasan": _ringkas(keluaran)})
                    hasil.append((p, keluaran))
                balasan = sesi.kirim_hasil(hasil)
            masuk, keluar = masuk + balasan.token_masuk, keluar + balasan.token_keluar

        pesan = (f"agen tidak memanggil {NAMA_JAWAB} dalam {self.maks_langkah} langkah"
                 + (f"; teks terakhir: {balasan.teks[:120]!r}" if balasan.teks else ""))
        return HasilAgen({"tipe": "galat", "pesan": pesan}, balasan.teks, jejak, self.maks_langkah,
                         masuk, keluar)


def muat_env(path: Path | str = ".env") -> None:
    """Muat ``KUNCI=nilai`` dari file .env ke variabel lingkungan yang belum terisi."""
    path = Path(path)
    if not path.exists():
        return
    for baris in path.read_text(encoding="utf-8").splitlines():
        baris = baris.strip()
        if not baris or baris.startswith("#") or "=" not in baris:
            continue
        kunci, _, nilai = baris.partition("=")
        kunci, nilai = kunci.strip(), nilai.strip().strip('"').strip("'")
        if kunci and kunci not in os.environ:
            os.environ[kunci] = nilai


def ke_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
