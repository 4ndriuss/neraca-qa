"""Database SQLite berisi angka laporan keuangan yang sudah diekstrak dan dinormalisasi.

Database dibangun ulang dari CSV di ``data/processed/`` (hasil ``neraca-xbrl``,
``neraca-pdf``, ``neraca-cocok``, dan ``neraca-akun``), jadi tidak pernah diedit
manual. Nilai disimpan sebagai teks desimal supaya tidak ada pembulatan
floating point; ubah dengan ``Decimal`` saat membaca.

Tabel:

- ``emiten``: kode, nama terbaru, sektor, subsektor
- ``laporan``: satu baris per laporan XBRL (emiten, tahun) beserta mata uang dan
  pembulatan, serta file PDF bila ada
- ``pos``: 36 pos standar dan konsep XBRL padanannya
- ``angka``: satu angka per emiten, tahun laporan, tahun, dan pos standar, dengan
  halaman sumber dan status verifikasi; inilah tabel utama untuk menjawab
- ``baris_pdf``: semua baris bernilai hasil ekstraksi PDF, untuk pertanyaan rinci
- ``xbrl_fakta``: fakta XBRL tanpa dimensi, kunci jawaban untuk set pertanyaan uji

Semua SQL di sini SQL standar, sehingga pindah ke Postgres cukup mengganti
koneksi dan menjalankan ulang pemuat.

Cara pakai::

    neraca-db                      # bangun data/processed/neraca.db
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

from neraca.normalisasi.akun import POS_STANDAR

SKEMA = """
CREATE TABLE emiten (
    kode        TEXT PRIMARY KEY,
    nama        TEXT NOT NULL,
    sektor      TEXT,
    subsektor   TEXT
);

CREATE TABLE laporan (
    emiten          TEXT NOT NULL REFERENCES emiten(kode),
    tahun_laporan   INTEGER NOT NULL,
    nama_entitas    TEXT,
    tanggal_akhir   TEXT,
    periode         TEXT,
    mata_uang       TEXT,
    pembulatan      TEXT,
    jenis_laporan   TEXT,
    file_xbrl       TEXT,
    file_pdf        TEXT,
    PRIMARY KEY (emiten, tahun_laporan)
);

CREATE TABLE pos (
    kode        TEXT PRIMARY KEY,
    nama        TEXT NOT NULL,
    laporan     TEXT NOT NULL,
    konsep_xbrl TEXT NOT NULL
);

CREATE TABLE angka (
    emiten              TEXT NOT NULL REFERENCES emiten(kode),
    tahun_laporan       INTEGER NOT NULL,
    tahun               INTEGER NOT NULL,
    pos                 TEXT NOT NULL REFERENCES pos(kode),
    nilai               TEXT NOT NULL,
    mata_uang           TEXT NOT NULL,
    halaman             INTEGER NOT NULL,
    laporan             TEXT NOT NULL,
    label               TEXT,
    induk               TEXT,
    status_verifikasi   TEXT NOT NULL,
    PRIMARY KEY (emiten, tahun_laporan, tahun, pos)
);

CREATE TABLE baris_pdf (
    emiten              TEXT NOT NULL REFERENCES emiten(kode),
    tahun_laporan       INTEGER NOT NULL,
    halaman             INTEGER NOT NULL,
    urutan              INTEGER NOT NULL,
    laporan             TEXT,
    induk               TEXT,
    konteks             TEXT,
    label               TEXT,
    tahun               INTEGER NOT NULL,
    nilai               TEXT NOT NULL,
    teks_nilai          TEXT,
    mata_uang           TEXT,
    status_verifikasi   TEXT,
    konsep_xbrl         TEXT
);

CREATE TABLE xbrl_fakta (
    emiten          TEXT NOT NULL,
    tahun_laporan   INTEGER NOT NULL,
    konsep          TEXT NOT NULL,
    jenis_periode   TEXT NOT NULL,
    tahun_periode   INTEGER NOT NULL,
    nilai           TEXT NOT NULL,
    mata_uang       TEXT,
    unit            TEXT,
    file_html       TEXT
);

CREATE INDEX idx_angka_pos ON angka(pos, tahun);
CREATE INDEX idx_baris_emiten ON baris_pdf(emiten, tahun_laporan, tahun);
CREATE INDEX idx_xbrl ON xbrl_fakta(emiten, konsep, tahun_periode);
"""

_NAMA_PDF_CSV = re.compile(r"^pdf_([A-Z]{4})_(\d{4})_lk_cocok\.csv$")


def koneksi(path: Path | str = Path("data/processed/neraca.db")) -> sqlite3.Connection:
    """Buka database dengan baris sebagai ``sqlite3.Row`` (bisa diakses per nama kolom)."""
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _baca(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _isi(con: sqlite3.Connection, tabel: str, baris: list[dict], kolom: list[str]) -> int:
    tanda = ", ".join("?" for _ in kolom)
    con.executemany(f"INSERT INTO {tabel} ({', '.join(kolom)}) VALUES ({tanda})",
                    ([b.get(k) for k in kolom] for b in baris))
    return len(baris)


def bangun(processed: Path, out: Path, raw: Path | None = None) -> dict[str, int]:
    """Bangun ulang database dari CSV hasil olahan. Mengembalikan jumlah baris per tabel."""
    laporan_csv = processed / "xbrl_laporan.csv"
    if not laporan_csv.exists():
        raise FileNotFoundError(f"{laporan_csv} tidak ada; jalankan neraca-xbrl dulu")

    sementara = out.with_suffix(".db.baru")
    sementara.unlink(missing_ok=True)
    con = koneksi(sementara)
    con.executescript(SKEMA)
    jumlah: dict[str, int] = {}

    laporan = _baca(laporan_csv)
    terbaru: dict[str, dict] = {}
    for lap in sorted(laporan, key=lambda r: int(r["tahun_laporan"])):
        terbaru[lap["emiten"]] = lap
    jumlah["emiten"] = _isi(con, "emiten", [
        {"kode": k, "nama": v["nama_entitas"], "sektor": v.get("sektor", ""),
         "subsektor": v.get("subsektor", "")} for k, v in sorted(terbaru.items())],
        ["kode", "nama", "sektor", "subsektor"])

    raw = raw or processed.parent / "raw"
    for lap in laporan:
        pdf = raw / f"{lap['emiten']}_{lap['tahun_laporan']}_lk.pdf"
        lap.update(tanggal_akhir=lap["tanggal_akhir_periode"], periode=lap["periode_penyampaian"],
                   mata_uang=lap["mata_uang_penyajian"], file_xbrl=lap["file_zip"],
                   file_pdf=pdf.name if pdf.exists() else None)
    jumlah["laporan"] = _isi(con, "laporan", laporan, [
        "emiten", "tahun_laporan", "nama_entitas", "tanggal_akhir", "periode", "mata_uang",
        "pembulatan", "jenis_laporan", "file_xbrl", "file_pdf"])

    jumlah["pos"] = _isi(con, "pos", [
        {"kode": p.kode, "nama": p.nama, "laporan": p.laporan, "konsep_xbrl": " | ".join(p.konsep_xbrl)}
        for p in POS_STANDAR], ["kode", "nama", "laporan", "konsep_xbrl"])

    akun = processed / "akun_standar.csv"
    jumlah["angka"] = _isi(con, "angka", _baca(akun), [
        "emiten", "tahun_laporan", "tahun", "pos", "nilai", "mata_uang", "halaman", "laporan",
        "label", "induk", "status_verifikasi"]) if akun.exists() else 0

    baris_pdf: list[dict] = []
    for path in sorted(processed.glob("pdf_*_lk_cocok.csv")):
        m = _NAMA_PDF_CSV.match(path.name)
        if not m:
            continue
        for r in _baca(path):
            r.update(emiten=m.group(1), tahun_laporan=int(m.group(2)), status_verifikasi=r["status"],
                     konsep_xbrl=r["konsep"])
            baris_pdf.append(r)
    jumlah["baris_pdf"] = _isi(con, "baris_pdf", baris_pdf, [
        "emiten", "tahun_laporan", "halaman", "urutan", "laporan", "induk", "konteks", "label",
        "tahun", "nilai", "teks_nilai", "mata_uang", "status_verifikasi", "konsep_xbrl"])

    fakta = processed / "xbrl_fakta.csv"
    jumlah["xbrl_fakta"] = _isi(con, "xbrl_fakta", [r for r in _baca(fakta) if not r["dimensi"]], [
        "emiten", "tahun_laporan", "konsep", "jenis_periode", "tahun_periode", "nilai", "mata_uang",
        "unit", "file_html"]) if fakta.exists() else 0

    con.commit()
    con.close()
    sementara.replace(out)
    return jumlah


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--processed", type=Path, default=Path("data/processed"))
    parser.add_argument("--out", type=Path, default=Path("data/processed/neraca.db"))
    args = parser.parse_args(argv)
    try:
        jumlah = bangun(args.processed, args.out)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Database ditulis ke {args.out}")
    for tabel, n in jumlah.items():
        print(f"  {tabel:12} {n:7,} baris")
    return 0


if __name__ == "__main__":
    sys.exit(main())
