export interface ProviderHealth {
  configured: boolean;
  ok: boolean;
  error: string | null;
}

export interface ProviderStatusResponse {
  android: ProviderHealth;
  apple: ProviderHealth;
}
