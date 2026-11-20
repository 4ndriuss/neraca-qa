"""Fungsi alat untuk menjawab pertanyaan laporan keuangan.

Fungsi-fungsi ini dipanggil LLM lewat *tool calling*, tetapi bisa dipakai dan
diuji langsung. Semua perhitungan dilakukan di sini dengan ``Decimal``; LLM
tidak pernah berhitung sendiri. Setiap hasil membawa sumbernya (PDF dan
halamannya, atau XBRL) dan status verifikasinya, supaya jawaban bisa
menyertakan sitasi.

Konvensi:

- Nilai dalam satuan penuh (Rupiah atau Dolar AS) dan dikembalikan sebagai teks
  desimal, ditambah ``nilai_teks`` yang mudah dibaca ("Rp 54,84 triliun").
- Pos beban (beban pokok pendapatan, beban bunga, beban pajak, dividen dibayar)
  selalu positif, karena PDF menulisnya dalam kurung sedangkan XBRL positif.
- Laporan sumber: bila ``tahun_laporan`` tidak diberikan, dipakai laporan tahun
  itu sendiri; untuk perbandingan antartahun dipakai laporan tahun terakhir,
  supaya kedua angka berbasis sama (sudah disajikan kembali bila ada).
- Di dalam satu laporan, angka PDF (dengan halaman) diutamakan; XBRL dipakai
  bila PDF laporan itu tidak ada.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from neraca.db import koneksi
from neraca.normalisasi.akun import POS, POS_STANDAR

POS_BEBAN = frozenset({"beban_pokok_pendapatan", "beban_bunga", "beban_pajak", "dividen_dibayar"})
_POS_PER_SAHAM = frozenset({"laba_per_saham_dasar", "laba_per_saham_dilusian"})


class GalatAlat(ValueError):
    """Argumen alat tidak valid (emiten atau pos tidak dikenal, dan sebagainya)."""


# --------------------------------------------------------------------------
# Format angka
# --------------------------------------------------------------------------

def _koma(nilai: Decimal, desimal: int = 2) -> str:
    """Format Indonesia: titik ribuan, koma desimal."""
    teks = f"{nilai:,.{desimal}f}"
    return teks.replace(",", "\0").replace(".", ",").replace("\0", ".")


def format_nilai(nilai: Decimal, mata_uang: str, per_saham: bool = False) -> str:
    """``Rp 54,84 triliun``, ``-Rp 5,15 triliun``, ``USD 1,38 miliar``, ``Rp 88 per saham``."""
    simbol = "Rp" if mata_uang == "IDR" else "USD"
    tanda, besar = ("-" if nilai < 0 else ""), abs(nilai)
    if per_saham:
        return f"{tanda}{simbol} {_koma(besar, 2 if besar % 1 else 0)} per saham"
    for batas, nama in ((Decimal(10) ** 12, "triliun"), (Decimal(10) ** 9, "miliar"),
                        (Decimal(10) ** 6, "juta")):
        if besar >= batas:
            return f"{tanda}{simbol} {_koma(besar / batas)} {nama}"
    return f"{tanda}{simbol} {_koma(besar, 0)}"


def format_persen(nilai: Decimal) -> str:
    return f"{_koma(nilai)}%"


# --------------------------------------------------------------------------
# Akses data
# --------------------------------------------------------------------------

@dataclass
class _Angka:
    nilai: Decimal
    mata_uang: str
    tahun_laporan: int
    jenis_sumber: str  # pdf | xbrl
    halaman: int | None
    label: str
    status_verifikasi: str
    file: str | None

    def sumber(self) -> dict[str, Any]:
        if self.jenis_sumber == "pdf":
            teks = f"Laporan keuangan {self.tahun_laporan}, hal. {self.halaman}"
        else:
            teks = f"XBRL laporan keuangan {self.tahun_laporan} ({self.file})"
        return {"jenis": self.jenis_sumber, "tahun_laporan": self.tahun_laporan,
                "halaman": self.halaman, "file": self.file, "label": self.label, "teks": teks}


class Alat:
    """Kumpulan alat di atas database ``neraca.db``."""

    def __init__(self, db: Path | str | sqlite3.Connection = Path("data/processed/neraca.db")):
        self.con = db if isinstance(db, sqlite3.Connection) else koneksi(db)
        self.emiten = {r["kode"]: dict(r) for r in self.con.execute("SELECT * FROM emiten")}
        self.laporan = {(r["emiten"], r["tahun_laporan"]): dict(r)
                        for r in self.con.execute("SELECT * FROM laporan")}

    # ---- validasi

    def _kode(self, emiten: str) -> str:
        kode = (emiten or "").strip().upper()
        if kode not in self.emiten:
            raise GalatAlat(f"emiten {emiten!r} tidak dikenal; tersedia: {', '.join(sorted(self.emiten))}")
        return kode

    @staticmethod
    def _pos(pos: str) -> str:
        if pos not in POS:
            raise GalatAlat(f"pos {pos!r} tidak dikenal; lihat daftar_pos")
        return pos

    def _tahun_laporan_ada(self, emiten: str) -> list[int]:
        return sorted(tl for (e, tl) in self.laporan if e == emiten)

    # ---- pengambilan mentah

    def _dari_pdf(self, emiten: str, pos: str, tahun: int, tl: int) -> _Angka | None:
        r = self.con.execute(
            "SELECT nilai, mata_uang, halaman, label, status_verifikasi FROM angka "
            "WHERE emiten=? AND pos=? AND tahun=? AND tahun_laporan=?", (emiten, pos, tahun, tl)).fetchone()
        if r is None:
            return None
        lap = self.laporan.get((emiten, tl), {})
        return _Angka(Decimal(r["nilai"]), r["mata_uang"], tl, "pdf", r["halaman"], r["label"] or "",
                      r["status_verifikasi"], lap.get("file_pdf"))

    def _dari_xbrl(self, emiten: str, pos: str, tahun: int, tl: int) -> _Angka | None:
        p = POS[pos]
        tahun_periode = tahun - 1 if pos == "kas_awal_tahun" else tahun
        for konsep in p.kunci:
            r = self.con.execute(
                "SELECT nilai, mata_uang, file_html FROM xbrl_fakta WHERE emiten=? AND tahun_laporan=? "
                "AND konsep=? AND tahun_periode=? ORDER BY file_html LIMIT 1",
                (emiten, tl, konsep, tahun_periode)).fetchone()
            if r is not None:
                lap = self.laporan.get((emiten, tl), {})
                return _Angka(Decimal(r["nilai"]), r["mata_uang"] or "", tl, "xbrl", None, konsep,
                              "xbrl", lap.get("file_xbrl"))
        return None

    def _ambil(self, emiten: str, pos: str, tahun: int, tl: int) -> _Angka | None:
        a = self._dari_pdf(emiten, pos, tahun, tl) or self._dari_xbrl(emiten, pos, tahun, tl)
        if a and pos in POS_BEBAN:
            a.nilai = abs(a.nilai)
        return a

    def _pilih_laporan(self, emiten: str, tahun: int, tahun_laporan: int | None) -> int | None:
        tersedia = self._tahun_laporan_ada(emiten)
        if tahun_laporan is not None:
            return tahun_laporan if tahun_laporan in tersedia else None
        for tl in (tahun, tahun + 1):
            if tl in tersedia:
                return tl
        return None

    def _alasan_kosong(self, emiten: str, pos: str, tahun: int) -> str:
        e = self.emiten[emiten]
        nama = POS[pos].nama.lower()
        tersedia = self._tahun_laporan_ada(emiten)
        rentang = (min(tersedia) - 1, max(tersedia)) if tersedia else None
        if rentang and not rentang[0] <= tahun <= rentang[1]:
            return (f"Data {emiten} hanya tersedia untuk tahun {rentang[0]}-{rentang[1]} "
                    f"(laporan tahunan {min(tersedia)}-{max(tersedia)}).")
        bank = "bank" in (e.get("subsektor") or "").lower()
        syariah = "syariah" in (e.get("nama") or "").lower()
        if syariah and pos in ("pendapatan_bunga", "beban_bunga"):
            return (f"{emiten} adalah bank syariah dan tidak memakai bunga; pendapatannya berupa bagi "
                    "hasil dan pendapatan pengelolaan dana sebagai mudharib.")
        if bank and pos in ("pendapatan", "beban_pokok_pendapatan", "laba_kotor"):
            return (f"{emiten} adalah bank; laporan laba rugi bank tidak menyajikan {nama}. "
                    "Ukuran yang setara misalnya pendapatan bunga (pendapatan_bunga).")
        if pos in ("laba_kotor", "beban_pokok_pendapatan"):
            ada = {r[0] for r in self.con.execute(
                "SELECT DISTINCT pos FROM angka WHERE emiten=?", (emiten,))}
            if "pendapatan" in ada and not ada & {"laba_kotor", "beban_pokok_pendapatan"}:
                return (f"Laporan laba rugi {emiten} menyajikan beban per jenis (misalnya beban "
                        f"operasi, karyawan, penyusutan), sehingga tidak ada {nama}.")
        return f"{POS[pos].nama} {emiten} tahun {tahun} tidak ditemukan di laporan yang tersedia."

    # ---- alat publik

    def daftar_emiten(self) -> dict[str, Any]:
        hasil = []
        for kode, e in sorted(self.emiten.items()):
            tl = self._tahun_laporan_ada(kode)
            mata_uang = self.laporan[(kode, tl[-1])]["mata_uang"] if tl else ""
            hasil.append({"kode": kode, "nama": e["nama"], "sektor": e["sektor"],
                          "subsektor": e["subsektor"], "mata_uang": mata_uang,
                          "tahun_laporan": tl,
                          "pdf": [t for t in tl if self.laporan[(kode, t)].get("file_pdf")]})
        return {"emiten": hasil}

    def daftar_pos(self) -> dict[str, Any]:
        return {"pos": [{"kode": p.kode, "nama": p.nama, "laporan": p.laporan} for p in POS_STANDAR],
                "catatan": "Pos beban (" + ", ".join(sorted(POS_BEBAN)) + ") dilaporkan positif."}

    def ambil_angka(self, emiten: str, pos: str, tahun: int,
                    tahun_laporan: int | None = None) -> dict[str, Any]:
        emiten, pos, tahun = self._kode(emiten), self._pos(pos), int(tahun)
        tl = self._pilih_laporan(emiten, tahun, tahun_laporan)
        a = self._ambil(emiten, pos, tahun, tl) if tl else None
        if a is None:
            return {"tersedia": False, "emiten": emiten, "pos": pos, "tahun": tahun,
                    "alasan": self._alasan_kosong(emiten, pos, tahun)}
        hasil = {"tersedia": True, "emiten": emiten, "pos": pos, "nama_pos": POS[pos].nama,
                 "tahun": tahun, "nilai": str(a.nilai), "mata_uang": a.mata_uang,
                 "nilai_teks": format_nilai(a.nilai, a.mata_uang, pos in _POS_PER_SAHAM),
                 "sumber": a.sumber(), "status_verifikasi": a.status_verifikasi}
        if a.status_verifikasi == "tidak_cocok":
            hasil["peringatan"] = "Angka ini belum terverifikasi dengan XBRL."
        # Apakah laporan yang lebih baru (PDF atau XBRL) menyajikan kembali angka ini?
        baru = self._ambil(emiten, pos, tahun, tl + 1) if tahun_laporan is None else None
        if baru and baru.nilai != a.nilai:
            hasil["disajikan_kembali"] = {
                "tahun_laporan": tl + 1, "nilai": str(baru.nilai), "sumber": baru.sumber(),
                "nilai_teks": format_nilai(baru.nilai, a.mata_uang, pos in _POS_PER_SAHAM)}
        return hasil

    def pertumbuhan(self, emiten: str, pos: str, tahun_awal: int, tahun_akhir: int,
                    tahun_laporan: int | None = None) -> dict[str, Any]:
        """Perubahan dari ``tahun_awal`` ke ``tahun_akhir``.

        Bawaan: kedua angka diambil dari laporan ``tahun_akhir`` (dasar sebanding).
        Bila laporan itu tidak memuat ``tahun_awal``, masing-masing tahun memakai
        laporannya sendiri dan hasilnya ditandai tidak sebanding.
        """
        emiten, pos = self._kode(emiten), self._pos(pos)
        tahun_awal, tahun_akhir = int(tahun_awal), int(tahun_akhir)
        tl = tahun_laporan or tahun_akhir
        akhir = self.ambil_angka(emiten, pos, tahun_akhir, tl)
        awal = self.ambil_angka(emiten, pos, tahun_awal, tl)
        sebanding = True
        if not awal["tersedia"]:
            awal, sebanding = self.ambil_angka(emiten, pos, tahun_awal), False
        for x in (awal, akhir):
            if not x["tersedia"]:
                return {"tersedia": False, "emiten": emiten, "pos": pos, "alasan": x["alasan"]}
        a, b = Decimal(awal["nilai"]), Decimal(akhir["nilai"])
        hasil = {"tersedia": True, "emiten": emiten, "pos": pos, "nama_pos": POS[pos].nama,
                 "awal": awal, "akhir": akhir, "selisih": str(b - a),
                 "selisih_teks": format_nilai(b - a, akhir["mata_uang"], pos in _POS_PER_SAHAM),
                 "dasar_sebanding": sebanding}
        if a > 0:
            persen = (b / a - 1) * 100
            hasil.update(persen=str(persen.quantize(Decimal("0.01"))), persen_teks=format_persen(persen))
        elif a < 0 and b < 0:
            persen = (abs(b) / abs(a) - 1) * 100
            hasil.update(persen=str(persen.quantize(Decimal("0.01"))), persen_teks=format_persen(persen),
                         catatan="Kedua angka negatif (rugi); persen adalah perubahan besarnya rugi. "
                                 "Negatif berarti rugi menyusut.")
        else:
            hasil["catatan"] = ("Persentase tidak bermakna karena angka awal nol atau berganti tanda "
                                "(dari rugi ke laba atau sebaliknya); gunakan selisih.")
        if not sebanding:
            hasil["catatan_dasar"] = (f"Laporan {tl} tidak memuat tahun {tahun_awal}; tiap tahun memakai "
                                      "laporannya sendiri, jadi dasarnya mungkin tidak sebanding.")
        return hasil

    def rasio(self, emiten: str, nama: str, tahun: int, tahun_laporan: int | None = None) -> dict[str, Any]:
        emiten, tahun = self._kode(emiten), int(tahun)
        if nama not in RASIO:
            raise GalatAlat(f"rasio {nama!r} tidak dikenal; tersedia: {', '.join(RASIO)}")
        r = RASIO[nama]
        pembilang = self.ambil_angka(emiten, r.pembilang, tahun, tahun_laporan)
        penyebut = self.ambil_angka(emiten, r.penyebut, tahun, tahun_laporan)
        for x in (pembilang, penyebut):
            if not x["tersedia"]:
                return {"tersedia": False, "emiten": emiten, "rasio": nama, "alasan": x["alasan"]}
        p, q = Decimal(pembilang["nilai"]), Decimal(penyebut["nilai"])
        if q == 0:
            return {"tersedia": False, "emiten": emiten, "rasio": nama, "alasan": "Penyebut bernilai nol."}
        nilai = p / q * (100 if r.persen else 1)
        nilai = nilai.quantize(Decimal("0.01"))
        return {"tersedia": True, "emiten": emiten, "rasio": nama, "nama_rasio": r.nama,
                "tahun": tahun, "nilai": str(nilai),
                "nilai_teks": format_persen(nilai) if r.persen else f"{_koma(nilai)}x",
                "definisi": r.definisi, "pembilang": pembilang, "penyebut": penyebut}

    def bandingkan(self, emiten: list[str], ukuran: str, tahun: int,
                   tahun_laporan: int | None = None) -> dict[str, Any]:
        """Urutkan beberapa emiten menurut satu pos standar atau rasio, dari terbesar."""
        kode = [self._kode(e) for e in emiten]
        if len(kode) < 2:
            raise GalatAlat("bandingkan butuh minimal dua emiten")
        if ukuran in RASIO:
            hasil = [self.rasio(e, ukuran, tahun, tahun_laporan) for e in kode]
        else:
            self._pos(ukuran)
            hasil = [self.ambil_angka(e, ukuran, tahun, tahun_laporan) for e in kode]
        ada = [h for h in hasil if h["tersedia"]]
        tidak = [{"emiten": h["emiten"], "alasan": h["alasan"]} for h in hasil if not h["tersedia"]]
        mata_uang = {h["mata_uang"] for h in ada if "mata_uang" in h}
        if ukuran not in RASIO and len(mata_uang) > 1:
            return {"tersedia": False, "ukuran": ukuran, "tahun": tahun,
                    "alasan": "Mata uang pelaporan berbeda (" + ", ".join(
                        f"{h['emiten']}: {h['mata_uang']}" for h in ada) +
                    "); nilai absolut tidak bisa dibandingkan tanpa kurs. Bandingkan rasio, atau "
                    "konversi dengan kurs dari sumber resmi.", "data": ada}
        peringkat = sorted(ada, key=lambda h: Decimal(h["nilai"]), reverse=True)
        return {"tersedia": bool(peringkat), "ukuran": ukuran, "tahun": tahun,
                "peringkat": [{"emiten": h["emiten"], "nilai": h["nilai"], "nilai_teks": h["nilai_teks"],
                               "sumber": h.get("sumber") or h.get("pembilang", {}).get("sumber")}
                              for h in peringkat],
                "tidak_tersedia": tidak}

    def cari_baris(self, emiten: str, kata: str, tahun: int | None = None,
                   tahun_laporan: int | None = None, batas: int = 10) -> dict[str, Any]:
        """Cari baris PDF yang labelnya memuat ``kata`` (untuk pos di luar 36 pos standar)."""
        emiten = self._kode(emiten)
        sql = ("SELECT halaman, laporan, induk, label, tahun, nilai, mata_uang, status_verifikasi, "
               "tahun_laporan FROM baris_pdf WHERE emiten=? AND LOWER(label) LIKE ?")
        param: list[Any] = [emiten, f"%{kata.lower()}%"]
        if tahun is not None:
            sql += " AND tahun=?"
            param.append(int(tahun))
        if tahun_laporan is not None:
            sql += " AND tahun_laporan=?"
            param.append(int(tahun_laporan))
        sql += " ORDER BY tahun_laporan DESC, halaman, urutan LIMIT ?"
        param.append(int(batas))
        baris = [{"label": r["label"], "induk": r["induk"], "laporan": r["laporan"], "tahun": r["tahun"],
                  "nilai": r["nilai"], "nilai_teks": format_nilai(Decimal(r["nilai"]), r["mata_uang"]),
                  "sumber": f"Laporan keuangan {r['tahun_laporan']}, hal. {r['halaman']}",
                  "status_verifikasi": r["status_verifikasi"]}
                 for r in self.con.execute(sql, param)]
        return {"emiten": emiten, "kata": kata, "jumlah": len(baris), "baris": baris}

    # ---- pemanggilan dari LLM

    def jalankan(self, nama: str, argumen: dict[str, Any]) -> dict[str, Any]:
        """Jalankan alat menurut nama; galat dikembalikan sebagai ``{"galat": ...}``."""
        fungsi: Callable[..., dict] | None = getattr(self, nama, None) if nama in NAMA_ALAT else None
        if fungsi is None:
            return {"galat": f"alat {nama!r} tidak dikenal"}
        try:
            return fungsi(**argumen)
        except (GalatAlat, TypeError, InvalidOperation) as exc:
            return {"galat": str(exc)}


# --------------------------------------------------------------------------
# Rasio
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Rasio:
    nama: str
    pembilang: str
    penyebut: str
    definisi: str
    persen: bool = True


RASIO: dict[str, Rasio] = {
    "margin_kotor": Rasio("Margin laba kotor", "laba_kotor", "pendapatan", "laba kotor / pendapatan"),
    "margin_usaha": Rasio("Margin laba usaha", "laba_usaha", "pendapatan", "laba usaha / pendapatan"),
    "margin_bersih": Rasio("Margin laba bersih", "laba_bersih_induk", "pendapatan",
                           "laba bersih milik entitas induk / pendapatan"),
    "roe": Rasio("Return on equity", "laba_bersih_induk", "ekuitas_induk",
                 "laba bersih milik entitas induk / ekuitas milik entitas induk akhir tahun"),
    "roa": Rasio("Return on assets", "laba_bersih", "total_aset", "laba bersih / total aset akhir tahun"),
    "der": Rasio("Rasio liabilitas terhadap ekuitas", "total_liabilitas", "total_ekuitas",
                 "total liabilitas / total ekuitas", persen=False),
}

NAMA_ALAT = ("daftar_emiten", "daftar_pos", "ambil_angka", "pertumbuhan", "rasio", "bandingkan",
             "cari_baris")


def _properti(**kv: Any) -> dict[str, Any]:
    return kv


_EMITEN = {"type": "string", "description": "Kode saham empat huruf, misalnya BBCA"}
_POS = {"type": "string", "description": "Kode pos standar dari daftar_pos, misalnya laba_bersih_induk"}
_TAHUN = {"type": "integer", "description": "Tahun buku, misalnya 2024"}
_TL = {"type": "integer", "description": "Opsional. Tahun laporan sumber; bawaan laporan tahun itu "
                                          "sendiri. Pakai tahun berikutnya untuk angka yang disajikan kembali."}

# Definisi alat dalam format JSON Schema, siap dipakai untuk tool calling LLM.
DEFINISI_ALAT: list[dict[str, Any]] = [
    {"name": "daftar_emiten", "description": "Daftar emiten yang tersedia beserta sektor, mata uang "
     "pelaporan, dan tahun laporannya.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "daftar_pos", "description": "Daftar 36 pos standar (kode dan nama) yang bisa diambil.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ambil_angka", "description": "Ambil satu angka laporan keuangan beserta sumber (halaman "
     "PDF atau XBRL) dan status verifikasinya. Bila tidak tersedia, mengembalikan alasannya.",
     "input_schema": {"type": "object", "properties": _properti(
         emiten=_EMITEN, pos=_POS, tahun=_TAHUN, tahun_laporan=_TL), "required": ["emiten", "pos", "tahun"]}},
    {"name": "pertumbuhan", "description": "Hitung perubahan sebuah pos dari tahun_awal ke tahun_akhir "
     "(selisih dan persen). Bawaan memakai laporan tahun_akhir untuk kedua tahun agar dasarnya sebanding.",
     "input_schema": {"type": "object", "properties": _properti(
         emiten=_EMITEN, pos=_POS, tahun_awal=_TAHUN, tahun_akhir=_TAHUN, tahun_laporan=_TL),
         "required": ["emiten", "pos", "tahun_awal", "tahun_akhir"]}},
    {"name": "rasio", "description": "Hitung rasio keuangan: " + ", ".join(
        f"{k} ({v.definisi})" for k, v in RASIO.items()) + ".",
     "input_schema": {"type": "object", "properties": _properti(
         emiten=_EMITEN, nama={"type": "string", "enum": list(RASIO)}, tahun=_TAHUN, tahun_laporan=_TL),
         "required": ["emiten", "nama", "tahun"]}},
    {"name": "bandingkan", "description": "Urutkan beberapa emiten menurut satu pos standar atau rasio, "
     "dari terbesar. Menolak membandingkan nilai absolut bila mata uangnya berbeda.",
     "input_schema": {"type": "object", "properties": _properti(
         emiten={"type": "array", "items": _EMITEN, "minItems": 2},
         ukuran={"type": "string", "description": "Kode pos standar atau nama rasio"},
         tahun=_TAHUN, tahun_laporan=_TL), "required": ["emiten", "ukuran", "tahun"]}},
    {"name": "cari_baris", "description": "Cari baris di laporan keuangan PDF yang labelnya memuat kata "
     "tertentu, untuk pos yang tidak ada di daftar pos standar.",
     "input_schema": {"type": "object", "properties": _properti(
         emiten=_EMITEN, kata={"type": "string"}, tahun=_TAHUN, tahun_laporan=_TL),
         "required": ["emiten", "kata"]}},
]
