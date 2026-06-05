/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PRISM_SIDECAR_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
