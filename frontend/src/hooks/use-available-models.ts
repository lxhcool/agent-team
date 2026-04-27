"use client";

import { useState, useEffect } from "react";

export type ModelOption = {
  provider: string;
  provider_display: string;
  model_id: string;
  model_name: string;
};

export type ModelSettings = {
  default_model: string | null;
};

export function useAvailableModels() {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/settings/models")
      .then((r) => r.json())
      .then((data) => {
        const options: ModelOption[] = [];
        for (const p of data.providers || []) {
          if (!p.has_api_key || !p.enabled) continue;
          const modelList = p.models || [];
          if (modelList.length === 0) {
            // If no models fetched yet, use default_model as option
            if (p.default_model) {
              options.push({
                provider: p.provider_name,
                provider_display: p.display_name || p.provider_name,
                model_id: `${p.provider_name}/${p.default_model}`,
                model_name: p.default_model,
              });
            }
          } else {
            for (const m of modelList) {
              options.push({
                provider: p.provider_name,
                provider_display: p.display_name || p.provider_name,
                model_id: `${p.provider_name}/${m.model_id || m.id}`,
                model_name: m.model_id || m.id || m.name,
              });
            }
          }
        }
        setModels(options);
        setDefaultModel(data.settings?.default_model || null);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return { models, defaultModel, loading };
}
