import { onBeforeUnmount, onMounted, ref } from 'vue'

export const MOBILE_BREAKPOINT = '(max-width: 768px)'

export function isMobileViewport() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(MOBILE_BREAKPOINT).matches
}

export function useMobileViewport() {
  const isMobile = ref(isMobileViewport())
  const mediaQuery = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(MOBILE_BREAKPOINT)
    : null

  function syncViewport(event: MediaQueryListEvent) {
    isMobile.value = event.matches
  }

  onMounted(() => mediaQuery?.addEventListener('change', syncViewport))
  onBeforeUnmount(() => mediaQuery?.removeEventListener('change', syncViewport))

  return { isMobile }
}
