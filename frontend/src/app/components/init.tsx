"use client";

import { useEffect } from "react";
import { installFetchInterceptor } from "@/lib/api";

/**
 * Client-side initialization component.
 * Installs the global fetch interceptor for auth headers.
 * Should be rendered once inside the root layout.
 */
export function AppInit() {
  useEffect(() => {
    installFetchInterceptor();
  }, []);

  return null;
}
