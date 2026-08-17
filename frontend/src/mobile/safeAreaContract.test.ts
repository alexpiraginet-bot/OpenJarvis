import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('./mobile.css', import.meta.url), 'utf8');

describe('native safe-area CSS contract', () => {
  it('applies the measured top inset directly to the absolute Home layer', () => {
    expect(css).toMatch(
      /\.oj-home\s*\{[\s\S]*?--oj-safe-area-inset-top[\s\S]*?\}/,
    );
  });

  it('applies measured bottom insets to app and form sheets', () => {
    expect(css).toMatch(
      /\.oj-app-layer \.oj-window\s*\{[\s\S]*?--oj-safe-area-inset-bottom[\s\S]*?\}/,
    );
    expect(css).toMatch(
      /\.oj-sheet\s*\{[\s\S]*?--oj-safe-area-inset-bottom[\s\S]*?\}/,
    );
  });
});
