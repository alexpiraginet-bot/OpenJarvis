export function buildAppPresentationMotion(
  reduceMotion: boolean,
  appOpen: boolean,
) {
  if (reduceMotion) {
    return {
      shell: {
        animate: { opacity: appOpen ? 0.82 : 1 },
        transition: { duration: 0 },
      },
      sheet: {
        initial: false,
        animate: { opacity: 1 },
        exit: { opacity: 0 },
        transition: { duration: 0 },
      },
    } as const;
  }

  return {
    shell: {
      animate: appOpen
        ? { opacity: 0.72, x: -10, y: 3, scale: 0.975 }
        : { opacity: 1, x: 0, y: 0, scale: 1 },
      transition: {
        type: 'spring',
        stiffness: 360,
        damping: 38,
        mass: 0.8,
      },
    },
    sheet: {
      initial: { opacity: 0, x: '16%', y: 18, scale: 0.965 },
      animate: { opacity: 1, x: 0, y: 0, scale: 1 },
      exit: { opacity: 0, x: '9%', y: 10, scale: 0.982 },
      transition: {
        type: 'spring',
        stiffness: 420,
        damping: 39,
        mass: 0.86,
      },
    },
  } as const;
}
