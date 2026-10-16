export function accentTokens(hex: string, dark: boolean) {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
  const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const lum = (r: number[]) =>
    r
      .map((v) => {
        const x = v / 255;
        return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      })
      .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
  const l = lum(rgb),
    white = 1.05 / (l + 0.05),
    black = (l + 0.05) / 0.05;
  let ink = [...rgb];
  const bg = dark ? 0.012 : 1;
  for (let i = 0; i < 30; i++) {
    if (
      (Math.max(lum(ink), bg) + 0.05) / (Math.min(lum(ink), bg) + 0.05) >=
      4.5
    )
      break;
    ink = ink.map((v) => Math.round(v * 0.9 + (dark ? 255 : 0) * 0.1));
  }
  return {
    primary: hex,
    foreground: white >= black ? '#ffffff' : '#000000',
    ink: `rgb(${ink.join(' ')})`,
    soft: `color-mix(in srgb, ${hex} ${dark ? '18' : '9'}%, var(--background))`,
  };
}
