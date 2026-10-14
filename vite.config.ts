import { defineConfig } from 'vite';
const apiPort = process.env.VIVI_API_PORT || '8000';
export default defineConfig({ server: { port: 5173, proxy: { '/api': `http://127.0.0.1:${apiPort}` } } });
