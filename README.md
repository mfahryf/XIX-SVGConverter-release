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

Push tag versi seperti `v0.1.0` untuk membuat GitHub Release. Workflow akan
mengunggah installer versioned dan alias stabil
`SVGConverter-latest-x64-setup.exe`, yang dipakai halaman publik SVGConverter.

Jangan masukkan API key Mayar atau token rahasia ke repository ini.
