// SPDX-License-Identifier: Apache-2.0

import {defineConfig} from "vite";

export default defineConfig({
  root: "host/dashboard",
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
