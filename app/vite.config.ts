import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri expects a fixed dev port and no auto-open.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1430,
    strictPort: true,
    // Vite watches the project root recursively, which reaches into
    // src-tauri/target/ — 2.2 GB of build output. The moment cargo relinks
    // target/debug/deps/publikclip_app.exe, chokidar's watch on that file
    // throws EBUSY and takes the whole dev server down with it ("The
    // beforeDevCommand terminated with a non-zero status code"), so any run
    // that actually rebuilds the Rust side fails. Nothing under src-tauri is
    // ever served by Vite, so watching it is pure cost.
    watch: {
      ignored: ['**/src-tauri/**']
    }
  },
  build: {
    target: 'es2022'
  }
})
