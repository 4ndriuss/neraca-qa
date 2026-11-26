"""Demo web interaktif Neraca QA memakai Streamlit.

Jalankan dari akar repositori::

    .venv/Scripts/streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import streamlit as st

from neraca.agen import buat_agen
from neraca.agen.groq import MODEL_BAWAAN
from neraca.agen.inti import muat_env
from neraca.alat import RASIO, Alat
from neraca.normalisasi.akun import POS_STANDAR

AKAR = Path(__file__).resolve().parents[1]
DB_BAWAAN = AKAR / "data" / "processed" / "neraca.db"
RINGKASAN_EVAL = AKAR / "eval" / "hasil" / "ringkasan.md"

MODEL_PILIHAN = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

CONTOH_PERTANYAAN = [
    "Laba bersih BCA yang diatribusikan kepada pemilik entitas induk tahun 2024 naik berapa persen dibanding 2023?",
    "Berapa ROE Bank Rakyat Indonesia tahun 2024?",
    "Di antara BCA, BRI, Mandiri, dan BSI, bank mana yang laba bersih milik entitas induknya terbesar pada 2024?",
    "Berapa laba kotor Telkom Indonesia tahun 2024?",
    "Berapa pendapatan Alamtri Resources (ADRO) tahun 2023 menurut laporan keuangan 2024, yang sudah disajikan kembali?",
    "Mana yang laba bersihnya lebih besar pada 2024, Alamtri Resources (ADRO) atau Bukit Asam (PTBA)?",
]


@contextmanager
def buka_alat(db: Path = DB_BAWAAN) -> Iterator[Alat]:
    """Buka koneksi SQLite baru per aksi agar aman lintas thread di Streamlit."""
    alat = Alat(db)
    try:
        yield alat
    finally:
        alat.con.close()


def _format_sumber(sumber: dict[str, Any] | None) -> str:
    if not sumber:
        return "-"
    teks = sumber.get("teks") or "-"
    label = sumber.get("label")
    return f"{teks} — label: `{label}`" if label else teks


def _tampilkan_jejak(jejak: list[dict[str, Any]]) -> None:
    panggilan = [j for j in jejak if j.get("alat") != "jawab_akhir"]
    if not panggilan:
        return
    with st.expander(f"Jejak pemanggilan alat ({len(panggilan)} panggilan)", expanded=False):
        for i, j in enumerate(panggilan, start=1):
            arg = ", ".join(f"{k}={v!r}" for k, v in (j.get("argumen") or {}).items())
            ringkas = j.get("ringkasan", "")
            st.markdown(f"{i}. `{j['alat']}({arg})` → {ringkas}")


def _tab_agen(db: Path, model: str, api_key_override: str) -> None:
    st.subheader("Tanya Agen LLM")
    st.caption(
        "LLM hanya memilih alat dan menyusun kalimat; seluruh angka dan perhitungan berasal dari "
        "database terverifikasi dan kode Python (`Decimal`)."
    )

    if "riwayat_chat" not in st.session_state:
        st.session_state.riwayat_chat = []

    with st.expander("Contoh pertanyaan cepat", expanded=not st.session_state.riwayat_chat):
        kolom = st.columns(2)
        for idx, contoh in enumerate(CONTOH_PERTANYAAN):
            if kolom[idx % 2].button(contoh, key=f"contoh_{idx}", width="stretch"):
                st.session_state.pertanyaan_dipilih = contoh

    for pesan in st.session_state.riwayat_chat:
        with st.chat_message(pesan["peran"]):
            st.markdown(pesan["teks"])
            if pesan.get("meta"):
                st.caption(pesan["meta"])
            if pesan.get("jejak"):
                _tampilkan_jejak(pesan["jejak"])

    pertanyaan_input = st.chat_input("Tulis pertanyaan laporan keuangan emiten BEI...")
    pertanyaan = st.session_state.pop("pertanyaan_dipilih", None) or pertanyaan_input
    if not pertanyaan:
        return

    st.session_state.riwayat_chat.append({"peran": "user", "teks": pertanyaan})
    with st.chat_message("user"):
        st.markdown(pertanyaan)

    with st.chat_message("assistant"):
        try:
            with st.spinner(f"Memanggil Groq ({model}) dan alat perhitungan..."):
                agen = buat_agen(
                    model=model,
                    db=db,
                    percobaan=4,
                    batas_waktu=30.0,
                    penyedia="groq",
                    api_key=api_key_override or None,
                )
                try:
                    hasil = agen.tanya(pertanyaan)
                finally:
                    agen.alat.con.close()
        except Exception as exc:
            pesan_galat = (
                f"Gagal memanggil Groq `{model}`: **{type(exc).__name__}** — `{exc}`\n\n"
                "Pastikan `GROQ_API_KEY` sudah diisi di `.env` atau di sidebar. "
                "Sementara itu, Anda tetap bisa memakai tab **Kalkulator & Eksplorasi** tanpa API."
            )
            st.error(pesan_galat)
            st.session_state.riwayat_chat.append({"peran": "assistant", "teks": pesan_galat})
            return

        teks_jawaban = hasil.teks or f"(Tanpa teks; jawaban terstruktur: `{hasil.jawaban}`)"
        tipe = hasil.jawaban.get("tipe", "-")
        nilai_str = hasil.jawaban.get("nilai")
        meta = (
            f"Tipe: `{tipe}`"
            + (f" | Nilai terstruktur: `{nilai_str}`" if nilai_str is not None else "")
            + f" | {hasil.langkah} langkah | {hasil.token_masuk:,} token masuk, "
            f"{hasil.token_keluar:,} token keluar"
        )
        st.markdown(teks_jawaban)
        st.caption(meta)
        _tampilkan_jejak(hasil.jejak)
        st.session_state.riwayat_chat.append(
            {"peran": "assistant", "teks": teks_jawaban, "meta": meta, "jejak": hasil.jejak}
        )


def _tampilkan_hasil_angka(res: dict[str, Any]) -> None:
    if not res.get("tersedia"):
        st.warning(res.get("alasan", "Data tidak tersedia."))
        return
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.metric(f"{res['nama_pos']} ({res['emiten']} {res['tahun']})", res["nilai_teks"])
    c2.metric("Mata Uang", res["mata_uang"] or "-")
    c3.metric("Verifikasi", res["status_verifikasi"])
    st.markdown(f"**Nilai penuh:** `{res['nilai']}`")
    st.markdown(f"**Sumber:** {_format_sumber(res.get('sumber'))}")
    if res.get("peringatan"):
        st.warning(res["peringatan"])
    if res.get("disajikan_kembali"):
        dk = res["disajikan_kembali"]
        st.info(
            f"**Disajikan kembali di laporan {dk['tahun_laporan']}:** "
            f"{dk['nilai_teks']} (`{dk['nilai']}`) — {_format_sumber(dk.get('sumber'))}"
        )


def _tab_kalkulator(db: Path) -> None:
    st.subheader("Kalkulator & Eksplorasi Data (Tanpa LLM)")
    st.caption(
        "Menjalankan fungsi alat di `neraca.alat` secara langsung terhadap `neraca.db` "
        "dengan aritmetika `Decimal` dan sitasi sumber."
    )

    with buka_alat(db) as alat:
        daftar_emiten = alat.daftar_emiten()["emiten"]
    kode_emiten = [e["kode"] for e in daftar_emiten]
    label_emiten = {e["kode"]: f"{e['kode']} — {e['nama']}" for e in daftar_emiten}
    kode_pos = [p.kode for p in POS_STANDAR]
    label_pos = {p.kode: f"{p.kode} ({p.nama})" for p in POS_STANDAR}

    mode = st.radio(
        "Pilih alat",
        ["Ambil Angka", "Pertumbuhan YoY", "Rasio Keuangan", "Bandingkan Emiten", "Cari Baris PDF"],
        horizontal=True,
    )

    if mode == "Ambil Angka":
        c1, c2, c3, c4 = st.columns([2, 3, 1, 1])
        emiten = c1.selectbox("Emiten", kode_emiten, format_func=label_emiten.get, key="aa_emiten")
        pos = c2.selectbox("Pos standar", kode_pos, format_func=label_pos.get, key="aa_pos")
        tahun = c3.selectbox("Tahun buku", [2025, 2024, 2023, 2022, 2021], index=1, key="aa_tahun")
        tl_opsi = c4.selectbox("Laporan sumber", ["Bawaan", 2025, 2024, 2023, 2022], key="aa_tl")
        tl = None if tl_opsi == "Bawaan" else int(tl_opsi)
        with buka_alat(db) as alat:
            res = alat.ambil_angka(emiten, pos, tahun, tl)
        _tampilkan_hasil_angka(res)

    elif mode == "Pertumbuhan YoY":
        c1, c2, c3, c4 = st.columns([2, 3, 1, 1])
        emiten = c1.selectbox("Emiten", kode_emiten, format_func=label_emiten.get, key="pt_emiten")
        pos = c2.selectbox(
            "Pos standar",
            kode_pos,
            index=kode_pos.index("laba_bersih_induk"),
            format_func=label_pos.get,
            key="pt_pos",
        )
        tahun_awal = c3.selectbox("Tahun awal", [2021, 2022, 2023, 2024], index=2, key="pt_awal")
        tahun_akhir = c4.selectbox("Tahun akhir", [2022, 2023, 2024, 2025], index=2, key="pt_akhir")
        with buka_alat(db) as alat:
            res = alat.pertumbuhan(emiten, pos, tahun_awal, tahun_akhir)
        if not res.get("tersedia"):
            st.warning(res.get("alasan", "Data tidak tersedia."))
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric(f"{tahun_awal}", res["awal"]["nilai_teks"])
            m2.metric(f"{tahun_akhir}", res["akhir"]["nilai_teks"], res.get("persen_teks"))
            m3.metric("Selisih", res["selisih_teks"])
            if res.get("catatan"):
                st.info(res["catatan"])
            if res.get("catatan_dasar"):
                st.warning(res["catatan_dasar"])
            st.markdown(f"- **Sumber {tahun_awal}:** {_format_sumber(res['awal'].get('sumber'))}")
            st.markdown(f"- **Sumber {tahun_akhir}:** {_format_sumber(res['akhir'].get('sumber'))}")

    elif mode == "Rasio Keuangan":
        c1, c2, c3 = st.columns([2, 2, 1])
        emiten = c1.selectbox("Emiten", kode_emiten, format_func=label_emiten.get, key="rs_emiten")
        nama_rasio = c2.selectbox(
            "Rasio",
            list(RASIO),
            format_func=lambda k: f"{k} — {RASIO[k].nama}",
            key="rs_nama",
        )
        tahun = c3.selectbox("Tahun", [2025, 2024, 2023, 2022], index=1, key="rs_tahun")
        with buka_alat(db) as alat:
            res = alat.rasio(emiten, nama_rasio, tahun)
        if not res.get("tersedia"):
            st.warning(res.get("alasan", "Data tidak tersedia."))
        else:
            st.metric(f"{res['nama_rasio']} ({emiten} {tahun})", res["nilai_teks"])
            st.markdown(f"**Definisi:** {res['definisi']}")
            k1, k2 = st.columns(2)
            with k1:
                st.markdown("**Pembilang**")
                st.write(f"{res['pembilang']['nama_pos']}: **{res['pembilang']['nilai_teks']}**")
                st.caption(_format_sumber(res["pembilang"].get("sumber")))
            with k2:
                st.markdown("**Penyebut**")
                st.write(f"{res['penyebut']['nama_pos']}: **{res['penyebut']['nilai_teks']}**")
                st.caption(_format_sumber(res["penyebut"].get("sumber")))

    elif mode == "Bandingkan Emiten":
        c1, c2, c3 = st.columns([3, 2, 1])
        pilihan_emiten = c1.multiselect(
            "Pilih minimal 2 emiten",
            kode_emiten,
            default=["BBCA", "BBRI", "BMRI", "BRIS"],
            key="bd_emiten",
        )
        opsi_ukuran = list(RASIO) + kode_pos
        ukuran = c2.selectbox(
            "Pos atau rasio",
            opsi_ukuran,
            index=opsi_ukuran.index("laba_bersih_induk"),
            format_func=lambda u: f"[Rasio] {u} ({RASIO[u].nama})" if u in RASIO else label_pos[u],
            key="bd_ukuran",
        )
        tahun = c3.selectbox("Tahun", [2025, 2024, 2023, 2022], index=1, key="bd_tahun")
        if len(pilihan_emiten) < 2:
            st.info("Pilih minimal 2 emiten untuk dibandingkan.")
        else:
            with buka_alat(db) as alat:
                res = alat.bandingkan(pilihan_emiten, ukuran, tahun)
            if not res.get("tersedia"):
                st.warning(res.get("alasan", "Tidak dapat dibandingkan."))
                if res.get("data"):
                    st.dataframe(
                        [
                            {
                                "Emiten": d["emiten"],
                                "Nilai": d["nilai_teks"],
                                "Mata Uang": d.get("mata_uang", "-"),
                                "Sumber": (d.get("sumber") or {}).get("teks", "-"),
                            }
                            for d in res["data"]
                        ],
                        width="stretch",
                        hide_index=True,
                    )
            else:
                baris_tabel = [
                    {
                        "Peringkat": i,
                        "Emiten": p["emiten"],
                        "Nilai": p["nilai_teks"],
                        "Nilai Numerik": float(Decimal(p["nilai"])),
                        "Sumber": (p.get("sumber") or {}).get("teks", "-"),
                    }
                    for i, p in enumerate(res["peringkat"], start=1)
                ]
                st.dataframe(
                    [{k: v for k, v in b.items() if k != "Nilai Numerik"} for b in baris_tabel],
                    width="stretch",
                    hide_index=True,
                )
                st.bar_chart(
                    {b["Emiten"]: b["Nilai Numerik"] for b in baris_tabel},
                )
                if res.get("tidak_tersedia"):
                    for t in res["tidak_tersedia"]:
                        st.caption(f"Tidak tersedia untuk **{t['emiten']}**: {t['alasan']}")

    else:
        c1, c2, c3 = st.columns([2, 3, 1])
        emiten = c1.selectbox("Emiten", kode_emiten, format_func=label_emiten.get, key="cb_emiten")
        kata = c2.text_input("Kata kunci label PDF", value="kas", key="cb_kata")
        tahun_opsi = c3.selectbox("Tahun", ["Semua", 2024, 2023, 2022], key="cb_tahun")
        tahun = None if tahun_opsi == "Semua" else int(tahun_opsi)
        if kata.strip():
            with buka_alat(db) as alat:
                res = alat.cari_baris(emiten, kata.strip(), tahun=tahun, batas=25)
            st.caption(f"Ditemukan {res['jumlah']} baris.")
            if res["baris"]:
                st.dataframe(
                    [
                        {
                            "Label": b["label"],
                            "Induk": b["induk"],
                            "Laporan": b["laporan"],
                            "Tahun": b["tahun"],
                            "Nilai": b["nilai_teks"],
                            "Sumber": b["sumber"],
                            "Verifikasi": b["status_verifikasi"],
                        }
                        for b in res["baris"]
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("Tidak ada baris PDF yang cocok.")


def _tab_cakupan(db: Path) -> None:
    st.subheader("Cakupan Data Emiten")
    with buka_alat(db) as alat:
        emiten = alat.daftar_emiten()["emiten"]
    st.dataframe(
        [
            {
                "Kode": e["kode"],
                "Nama Emiten": e["nama"],
                "Sektor": e["sektor"],
                "Subsektor": e["subsektor"],
                "Mata Uang": e["mata_uang"],
                "Laporan XBRL": ", ".join(str(t) for t in e["tahun_laporan"]),
                "PDF Audit": ", ".join(str(t) for t in e["pdf"]) or "-",
            }
            for e in emiten
        ],
        width="stretch",
        hide_index=True,
    )

    st.divider()
    st.subheader("Ringkasan Evaluasi")
    if RINGKASAN_EVAL.exists():
        st.markdown(RINGKASAN_EVAL.read_text(encoding="utf-8"))
    else:
        st.info("Belum ada file `eval/hasil/ringkasan.md`. Jalankan `neraca-uji` terlebih dahulu.")


def main() -> None:
    st.set_page_config(page_title="Neraca QA — Laporan Keuangan BEI", page_icon="📊", layout="wide")
    muat_env(AKAR / ".env")

    st.title("📊 Neraca QA")
    st.caption(
        "Tanya-jawab laporan keuangan emiten BEI dengan perhitungan terverifikasi (`Decimal`) "
        "dan sitasi halaman. *Untuk edukasi & portofolio — bukan saran investasi.*"
    )

    with st.sidebar:
        st.header("Pengaturan Groq")
        pilihan = st.selectbox(
            "Model Groq",
            MODEL_PILIHAN + ["Lainnya..."],
            index=0,
            help="qwen/qwen3.8-27b paling cepat & hemat token (~4 detik, aman dari batas 8.000 TPM); "
            "openai/gpt-oss-120b / 20b tersedia sebagai alternatif.",
        )
        model = (
            st.text_input("Nama model kustom", value=MODEL_BAWAAN)
            if pilihan == "Lainnya..."
            else pilihan
        )
        key_ada = bool(os.environ.get("GROQ_API_KEY"))
        if key_ada:
            st.success("`GROQ_API_KEY` terdeteksi dari `.env` / environment.")
        else:
            st.warning(
                "`GROQ_API_KEY` belum ada di `.env`. Masukkan key di bawah atau buat gratis di "
                "[console.groq.com/keys](https://console.groq.com/keys)."
            )
        api_key_override = st.text_input(
            "GROQ_API_KEY (opsional / override)",
            type="password",
            help="Hanya disimpan di sesi ini bila belum diatur di .env.",
        )
        if st.button("Hapus riwayat chat", width="stretch"):
            st.session_state.riwayat_chat = []
            st.rerun()
        st.divider()
        st.markdown(
            "**Database:** `data/processed/neraca.db`\n\n"
            "**Prinsip:** LLM tidak pernah berhitung sendiri. Semua angka diambil dari tabel "
            "terstruktur (PDF audit terverifikasi XBRL)."
        )

    if not DB_BAWAAN.exists():
        st.error(
            f"Database `{DB_BAWAAN}` tidak ditemukan. Jalankan pipeline (`neraca-db`) terlebih dahulu."
        )
        return

    tab_agen, tab_kalkulator, tab_cakupan = st.tabs(
        ["💬 Tanya Agen LLM", "🧮 Kalkulator & Eksplorasi", "📚 Cakupan & Evaluasi"]
    )
    with tab_agen:
        _tab_agen(DB_BAWAAN, model.strip() or MODEL_BAWAAN, api_key_override.strip())
    with tab_kalkulator:
        _tab_kalkulator(DB_BAWAAN)
    with tab_cakupan:
        _tab_cakupan(DB_BAWAAN)


if __name__ == "__main__":
    main()
