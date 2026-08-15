import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Skeleton, SkeletonRows, SkeletonScreen } from './Skeleton';

describe('esqueletos de carregamento', () => {
  it('anuncia a região ocupada uma única vez', () => {
    const html = renderToStaticMarkup(<SkeletonScreen rows={3} />);
    // Um leitor de tela deve ouvir "Carregando" uma vez — não uma vez por bloco.
    expect(html.match(/aria-label="Carregando"/g)).toHaveLength(1);
    expect(html).toContain('aria-busy="true"');
  });

  it('mantém os blocos decorativos fora da árvore de acessibilidade', () => {
    const html = renderToStaticMarkup(<SkeletonScreen rows={2} />);
    const blocos = html.match(/class="oj-skel"/g) ?? [];
    const escondidos = html.match(/aria-hidden="true"/g) ?? [];
    // Cada bloco de brilho precisa estar escondido, senão o leitor recita a mobília.
    expect(blocos.length).toBeGreaterThan(0);
    expect(escondidos.length).toBeGreaterThanOrEqual(blocos.length);
  });

  it('rende a quantidade de linhas pedida', () => {
    const html = renderToStaticMarkup(<SkeletonRows count={5} />);
    expect(html.match(/class="oj-row"/g)).toHaveLength(5);
  });

  it('usa as classes do shell para acompanhar o sistema de design', () => {
    const html = renderToStaticMarkup(<SkeletonScreen />);
    expect(html).toContain('oj-card');
    expect(html).toContain('oj-list');
  });

  it('deixa o cartão de fora quando a tela é só lista', () => {
    const html = renderToStaticMarkup(<SkeletonScreen card={false} rows={2} />);
    expect(html).not.toContain('oj-card');
  });

  it('aceita medidas explícitas', () => {
    const html = renderToStaticMarkup(<Skeleton width={54} height={13} radius={4} />);
    expect(html).toContain('width:54px');
    expect(html).toContain('height:13px');
    expect(html).toContain('border-radius:4px');
  });
});
