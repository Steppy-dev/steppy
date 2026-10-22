// ``@steppy/player`` ships JS bundles without ``.d.ts`` today (its npm publish
// story is deferred). This ambient declaration lets ``tsc`` resolve the
// side-effect import that registers the ``<steppy-guide>`` custom element.
declare module '@steppy/player';
