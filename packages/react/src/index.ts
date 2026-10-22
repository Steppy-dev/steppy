/**
 * @steppy/react — a thin React wrapper around the framework-agnostic
 * ``<steppy-guide>`` Lit web component shipped by ``@steppy/player``.
 *
 * It does three things and nothing more: (1) side-effect import of
 * ``@steppy/player`` so the custom element is registered, (2) reflect its
 * declarative React props onto the element's attributes/properties, and
 * (3) surface the seven ``steppy:*`` DOM events as ``on*`` callbacks.
 *
 * This is a **foundational starter**. It will be extended (SSR guards,
 * richer typing, an inline-manifest prop, etc.) when Steppy is implemented on
 * real OSS React projects.
 */
import '@steppy/player';
import {
  createElement,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type CSSProperties,
  type ForwardedRef,
} from 'react';

export type SteppyTemplate = 'overlay' | 'sidebar' | 'inline';
export type SteppyCursorMode = 'arrow' | 'hotspot' | 'off';

/** Detail of the ``steppy:error`` event (the player's ``ParseError`` shape).
 *  Defined locally so the wrapper stays decoupled from ``@steppy/player``'s
 *  (not-yet-published) type declarations. */
export interface SteppyParseError {
  code: string;
  message: string;
  issues?: unknown;
}

export interface SteppyReadyEvent {
  slug: string;
  total: number;
  locale: string | null;
  variant: string | null;
}
export interface SteppyStepEvent {
  index: number;
  id: string;
  total: number;
  action: string;
  interactive: boolean;
  reason?: string;
}
export interface SteppyHotspotEvent {
  index: number;
  id: string;
  target: 'correct' | 'miss';
}
export interface SteppyChangeEvent {
  from: string | null;
  to: string;
}
export interface SteppyCompleteEvent {
  slug: string;
  locale: string | null;
  variant: string | null;
}

/** Imperative handle exposed via ``ref`` for programmatic navigation. */
export interface SteppyGuideHandle {
  next(): void;
  prev(): void;
  restart(): void;
  goTo(index: number): void;
  readonly element: HTMLElement | null;
}

export interface SteppyGuideProps {
  /** URL of the ``guide.json`` manifest to fetch. */
  src: string;
  locale?: string;
  /** Layout key (the player calls this ``variant``). */
  variant?: string;
  template?: SteppyTemplate;
  cursor?: SteppyCursorMode;
  autoplay?: boolean;
  startIndex?: number;
  hideLayoutSwitcher?: boolean;
  /** Sparse per-locale UI-string overrides (a property-only input on the element). */
  uiStrings?: Record<string, Record<string, string>>;
  className?: string;
  style?: CSSProperties;
  onReady?: (detail: SteppyReadyEvent) => void;
  onStep?: (detail: SteppyStepEvent) => void;
  onHotspot?: (detail: SteppyHotspotEvent) => void;
  onLocaleChange?: (detail: SteppyChangeEvent) => void;
  onVariantChange?: (detail: SteppyChangeEvent) => void;
  onComplete?: (detail: SteppyCompleteEvent) => void;
  onError?: (detail: SteppyParseError) => void;
}

function reflect(el: HTMLElement, name: string, value: string | undefined): void {
  if (value == null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

function reflectBool(el: HTMLElement, name: string, value: boolean | undefined): void {
  if (value) el.setAttribute(name, '');
  else el.removeAttribute(name);
}

// DOM event → callback prop. Listeners are attached once; the current handler
// is read from a ref so changing a callback never re-subscribes.
const EVENT_TO_PROP = {
  'steppy:ready': 'onReady',
  'steppy:step': 'onStep',
  'steppy:hotspot': 'onHotspot',
  'steppy:locale': 'onLocaleChange',
  'steppy:variant': 'onVariantChange',
  'steppy:complete': 'onComplete',
  'steppy:error': 'onError',
} as const;

export const SteppyGuide = forwardRef(function SteppyGuide(
  props: SteppyGuideProps,
  ref: ForwardedRef<SteppyGuideHandle>,
) {
  const elRef = useRef<HTMLElement | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  // Reflect declarative props whenever they change. Attributes are set
  // imperatively (rather than through JSX) so behaviour is identical across
  // React 17/18/19, which differ in how they hand props to custom elements.
  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    reflect(el, 'src', props.src);
    reflect(el, 'locale', props.locale);
    reflect(el, 'variant', props.variant);
    reflect(el, 'template', props.template);
    reflect(el, 'cursor', props.cursor);
    reflect(
      el,
      'start-index',
      props.startIndex != null ? String(props.startIndex) : undefined,
    );
    reflectBool(el, 'autoplay', props.autoplay);
    reflectBool(el, 'hide-layout-switcher', props.hideLayoutSwitcher);
    (el as unknown as { uiStrings?: unknown }).uiStrings = props.uiStrings;
  }, [
    props.src,
    props.locale,
    props.variant,
    props.template,
    props.cursor,
    props.startIndex,
    props.autoplay,
    props.hideLayoutSwitcher,
    props.uiStrings,
  ]);

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    const disposers = Object.entries(EVENT_TO_PROP).map(([evt, propName]) => {
      const listener = (e: Event) => {
        const handler = propsRef.current[propName as keyof SteppyGuideProps] as
          | ((detail: unknown) => void)
          | undefined;
        handler?.((e as CustomEvent).detail);
      };
      el.addEventListener(evt, listener);
      return () => el.removeEventListener(evt, listener);
    });
    return () => disposers.forEach((dispose) => dispose());
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      next: () => (elRef.current as unknown as { next?: () => void })?.next?.(),
      prev: () => (elRef.current as unknown as { prev?: () => void })?.prev?.(),
      restart: () =>
        (elRef.current as unknown as { restart?: () => void })?.restart?.(),
      goTo: (index: number) =>
        (elRef.current as unknown as { goTo?: (i: number) => void })?.goTo?.(index),
      get element() {
        return elRef.current;
      },
    }),
    [],
  );

  return createElement('steppy-guide', {
    ref: elRef,
    className: props.className,
    style: props.style,
  });
});
