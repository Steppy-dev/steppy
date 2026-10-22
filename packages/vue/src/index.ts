/**
 * @steppy/vue — a thin Vue 3 wrapper around the framework-agnostic
 * ``<steppy-guide>`` Lit web component shipped by ``@steppy/player``.
 *
 * It side-effect imports ``@steppy/player`` to register the element, reflects
 * its declarative props onto attributes/properties, and re-emits the seven
 * ``steppy:*`` DOM events as Vue events. A render function (not a template) is
 * used so no host ``compilerOptions.isCustomElement`` config is needed and the
 * colon in ``steppy:*`` event names is never handed to the template compiler.
 *
 * This is a **foundational starter** — it will be extended (SSR guards, an
 * inline-manifest prop, richer typing) when Steppy is implemented on real OSS
 * Vue/PrimeVue projects.
 */
import '@steppy/player';
import {
  defineComponent,
  h,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type PropType,
} from 'vue';

export type SteppyTemplate = 'overlay' | 'sidebar' | 'inline';
export type SteppyCursorMode = 'arrow' | 'hotspot' | 'off';

function reflect(el: HTMLElement, name: string, value: string | undefined): void {
  if (value == null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

function reflectBool(el: HTMLElement, name: string, value: boolean | undefined): void {
  if (value) el.setAttribute(name, '');
  else el.removeAttribute(name);
}

// DOM event → Vue emit name.
const EVENT_TO_EMIT: Record<string, string> = {
  'steppy:ready': 'ready',
  'steppy:step': 'step',
  'steppy:hotspot': 'hotspot',
  'steppy:locale': 'localeChange',
  'steppy:variant': 'variantChange',
  'steppy:complete': 'complete',
  'steppy:error': 'error',
};

export const SteppyGuide = defineComponent({
  name: 'SteppyGuide',
  props: {
    /** URL of the ``guide.json`` manifest to fetch. */
    src: { type: String, required: true },
    locale: String,
    /** Layout key (the player calls this ``variant``). */
    variant: String,
    template: String as PropType<SteppyTemplate>,
    cursor: String as PropType<SteppyCursorMode>,
    autoplay: Boolean,
    startIndex: Number,
    hideLayoutSwitcher: Boolean,
    /** Sparse per-locale UI-string overrides (a property-only input). */
    uiStrings: Object as PropType<Record<string, Record<string, string>>>,
  },
  emits: [
    'ready',
    'step',
    'hotspot',
    'localeChange',
    'variantChange',
    'complete',
    'error',
  ],
  setup(props, { emit, expose }) {
    const elRef = ref<HTMLElement | null>(null);
    const disposers: Array<() => void> = [];

    const apply = () => {
      const el = elRef.value;
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
    };

    onMounted(() => {
      const el = elRef.value;
      if (!el) return;
      apply();
      // ``emit`` is typed to the declared union; the map iteration yields a
      // plain string, so widen the call signature at this one site.
      const emitAny = emit as (event: string, detail: unknown) => void;
      for (const [evt, name] of Object.entries(EVENT_TO_EMIT)) {
        const listener = (e: Event) => emitAny(name, (e as CustomEvent).detail);
        el.addEventListener(evt, listener);
        disposers.push(() => el.removeEventListener(evt, listener));
      }
    });

    onBeforeUnmount(() => {
      disposers.forEach((dispose) => dispose());
      disposers.length = 0;
    });

    watch(() => ({ ...props }), apply, { deep: true });

    const call = (method: string) => (arg?: number) =>
      (elRef.value as unknown as Record<string, ((a?: number) => void) | undefined>)[
        method
      ]?.(arg);
    expose({
      next: call('next'),
      prev: call('prev'),
      restart: call('restart'),
      goTo: call('goTo'),
      get element() {
        return elRef.value;
      },
    });

    return () => h('steppy-guide', { ref: elRef });
  },
});

export default SteppyGuide;
