# XIX-SVGConverter

Aplikasi desktop untuk mengubah SVG menjadi EPS atau gambar raster menggunakan Inkscape lokal.

Engine yang tersedia:

- SVG Converter — Inkscape portable

Aplikasi ini bekerja lokal dan tidak menyertakan Tor maupun pengaturan proxy.

Jalankan mode development dari folder ini dengan:

```powershell
npm install
npm run dev
```

## Rilis Windows

Push tag versi seperti `v0.1.0` untuk membuat GitHub Release. Workflow
`.github/workflows/release.yml` akan mengunggah installer versioned, signature
`.sig`, `latest.json`, dan alias stabil `SVGConverter-latest-x64-setup.exe`,
yang dipakai halaman publik SVGConverter dan updater aplikasi.

Release terverifikasi saat ini: `v0.1.1`.

- Metadata updater: `https://github.com/mfahryf/XIX-SVGConverter-release/releases/latest/download/latest.json`
- Installer stabil: `https://github.com/mfahryf/XIX-SVGConverter-release/releases/latest/download/SVGConverter-latest-x64-setup.exe`

Alur release:

1. Selaraskan perubahan dari repository sumber `mfahryf/XIX-SVGConverter`.
2. Naikkan versi pada manifest aplikasi, Cargo, dan konfigurasi Tauri.
3. Pastikan secret GitHub `TAURI_SIGNING_PRIVATE_KEY` tersedia.
4. Commit perubahan, buat tag `v*`, lalu push tag.
5. Tunggu GitHub Actions selesai dan periksa `latest.json`, `.sig`, serta alias
   installer dengan akses tanpa login.

Push ke repository source saja tidak membuat release. Jangan memasukkan API key
Mayar, token webhook, token admin, atau private signing key ke repository ini.
