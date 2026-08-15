/**
 * Esqueletos de carregamento — o lugar do `Spinner` nas telas inteiras.
 *
 * Um spinner de 22px no meio de uma tela vazia não diz nada além de "espere".
 * Um esqueleto com a forma do que vai chegar faz a tela parecer mais rápida
 * sem ser mais rápida: o olho já encontra a estrutura, e quando os dados
 * entram nada salta de lugar. Numa das prioridades declaradas do produto —
 * reduzir o tempo de resposta percebido — isso vale mais que o spinner.
 *
 * As formas reaproveitam `.oj-card` e `.oj-row` do shell, então o esqueleto
 * acompanha o sistema de design sozinho quando ele for retunado.
 */

import './skeleton.css';

/** Um bloco de brilho. `aria-hidden` porque quem anuncia é o contêiner. */
export function Skeleton({
  width = '100%',
  height = 12,
  radius,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
}) {
  return (
    <div
      className="oj-skel"
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

/** Um cartão de destaque: rótulo curto em cima, número grande embaixo. */
export function SkeletonCard() {
  return (
    <div className="oj-card" aria-hidden="true">
      <Skeleton width="42%" height={9} />
      <div style={{ height: 12 }} />
      <Skeleton width="64%" height={24} />
    </div>
  );
}

/** Linhas de lista com título e subtítulo, mais um valor à direita. */
export function SkeletonRows({ count = 4 }: { count?: number }) {
  // Larguras alternadas: linhas de tamanho idêntico leem como uma tabela
  // travada, não como conteúdo chegando.
  const widths = ['62%', '48%', '71%', '55%', '66%'];
  return (
    <div className="oj-list" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="oj-row">
          <div className="oj-row-body">
            <Skeleton width={widths[index % widths.length]} height={13} />
            <div style={{ height: 7 }} />
            <Skeleton width="34%" height={10} />
          </div>
          <Skeleton width={54} height={13} />
        </div>
      ))}
    </div>
  );
}

/**
 * O substituto direto de `<Spinner />` numa tela inteira.
 *
 * Anuncia-se uma vez como região ocupada; os blocos internos ficam fora da
 * árvore de acessibilidade para o leitor de tela não recitar a mobília.
 */
export function SkeletonScreen({
  card = true,
  rows = 4,
}: {
  card?: boolean;
  rows?: number;
}) {
  return (
    <div role="status" aria-busy="true" aria-label="Carregando">
      {card && <SkeletonCard />}
      <SkeletonRows count={rows} />
    </div>
  );
}
