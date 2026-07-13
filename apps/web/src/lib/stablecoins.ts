/** Pegged USD stablecoins — kept in sync with apps/api/app/config.py STABLECOIN_ASSETS. */
const STABLECOIN_ASSETS = new Set([
  "USDT",
  "USDT0",
  "USDC",
  "DAI",
  "TUSD",
  "USDP",
  "PYUSD",
  "BUSD",
]);

function normalizeAssetTicker(asset: string): string {
  return asset.trim().toUpperCase().replace(/₮/g, "T");
}

export function isStablecoin(asset: string): boolean {
  return STABLECOIN_ASSETS.has(normalizeAssetTicker(asset));
}
