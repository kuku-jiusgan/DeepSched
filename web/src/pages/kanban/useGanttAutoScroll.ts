import { nextTick, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { message } from 'ant-design-vue'


const AUTO_SCROLL_STEP_PX = 1.2
const AUTO_SCROLL_INTERVAL_MS = 30
const AUTO_SCROLL_EDGE_PAUSE_MS = 1200
const AUTO_SCROLL_START_DELAY_MS = 500
const AUTO_SCROLL_START_RETRY_MS = 220
const AUTO_SCROLL_START_MAX_RETRIES = 10
const REFRESH_INTERVAL_MS = 30_000

interface GanttAutoScrollOptions {
  containerRef: Ref<HTMLElement | null>
  isFullscreen: Ref<boolean>
  recalculate: () => void | Promise<void>
  refresh: (silent?: boolean) => void | Promise<void>
}

export function useGanttAutoScroll(options: GanttAutoScrollOptions) {
  const autoScrollEnabled = ref(true)
  const hasVerticalOverflow = ref(false)
  let resizeObserver: ResizeObserver | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let autoScrollTimer: ReturnType<typeof setInterval> | null = null
  let autoScrollRetryTimer: ReturnType<typeof setTimeout> | null = null
  let resizeFrame: number | null = null
  let observedWidth = 0
  let autoScrollDirection = 1
  let autoScrollHoldUntil = 0

  function toggleFullscreen() {
    if (!options.isFullscreen.value) {
      autoScrollEnabled.value = true
      document.documentElement.requestFullscreen()
        .catch(() => message.error('浏览器未允许进入全屏'))
      return
    }
    stopAutoScroll()
    void document.exitFullscreen()
  }

  function scheduleAutoScrollStart(delay = AUTO_SCROLL_START_DELAY_MS, retryCount = 0) {
    if (autoScrollTimer !== null) return
    if (autoScrollRetryTimer) clearTimeout(autoScrollRetryTimer)
    autoScrollRetryTimer = setTimeout(async () => {
      autoScrollRetryTimer = null
      await nextTick()
      await options.recalculate()
      const started = startAutoScroll()
      if (!started && options.isFullscreen.value && autoScrollEnabled.value && retryCount < AUTO_SCROLL_START_MAX_RETRIES) {
        scheduleAutoScrollStart(AUTO_SCROLL_START_RETRY_MS, retryCount + 1)
      }
    }, delay)
  }

  function getMaxVerticalScroll() {
    const container = options.containerRef.value
    if (!container) return 0
    const maxScroll = Math.max(0, container.scrollHeight - container.clientHeight)
    hasVerticalOverflow.value = maxScroll > 2
    return maxScroll
  }

  function startAutoScroll() {
    if (!options.containerRef.value || !options.isFullscreen.value || !autoScrollEnabled.value) {
      getMaxVerticalScroll()
      return false
    }
    if (autoScrollTimer !== null) return true
    if (getMaxVerticalScroll() <= 2) return false
    autoScrollDirection = 1
    autoScrollHoldUntil = performance.now() + 150
    autoScrollTimer = setInterval(runAutoScrollTick, AUTO_SCROLL_INTERVAL_MS)
    return true
  }

  function runAutoScrollTick() {
    const container = options.containerRef.value
    if (!container || !autoScrollEnabled.value || !options.isFullscreen.value) {
      stopAutoScroll()
      return
    }
    const maxScroll = getMaxVerticalScroll()
    if (maxScroll <= 2) {
      stopAutoScroll()
      return
    }
    const now = performance.now()
    if (now < autoScrollHoldUntil) return
    const nextScroll = container.scrollTop + autoScrollDirection * AUTO_SCROLL_STEP_PX
    if (nextScroll >= maxScroll) {
      container.scrollTop = maxScroll
      autoScrollDirection = -1
      autoScrollHoldUntil = now + AUTO_SCROLL_EDGE_PAUSE_MS
    } else if (nextScroll <= 0) {
      container.scrollTop = 0
      autoScrollDirection = 1
      autoScrollHoldUntil = now + AUTO_SCROLL_EDGE_PAUSE_MS
    } else {
      container.scrollTop = nextScroll
    }
  }

  function stopAutoScroll() {
    if (autoScrollTimer !== null) {
      clearInterval(autoScrollTimer)
      autoScrollTimer = null
    }
    if (autoScrollRetryTimer) {
      clearTimeout(autoScrollRetryTimer)
      autoScrollRetryTimer = null
    }
  }

  function onFullscreenChange() {
    options.isFullscreen.value = Boolean(document.fullscreenElement)
    if (options.isFullscreen.value) {
      autoScrollEnabled.value = true
      scheduleAutoScrollStart()
    } else {
      stopAutoScroll()
    }
    nextTick(() => getMaxVerticalScroll())
  }

  function handleResize() {
    if (resizeFrame !== null) return
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = null
      void options.recalculate()
      if (options.isFullscreen.value && autoScrollEnabled.value && autoScrollTimer === null) {
        scheduleAutoScrollStart(300)
      }
    })
  }

  onMounted(() => {
    void options.refresh()
    refreshTimer = setInterval(() => options.refresh(true), REFRESH_INTERVAL_MS)
    window.addEventListener('resize', handleResize)
    document.addEventListener('fullscreenchange', onFullscreenChange)
    nextTick(() => {
      if (!options.containerRef.value) return
      observedWidth = options.containerRef.value.clientWidth
      resizeObserver = new ResizeObserver(entries => {
        const width = entries[0]?.contentRect.width ?? 0
        if (width <= 0 || Math.abs(width - observedWidth) < 1) return
        observedWidth = width
        handleResize()
      })
      resizeObserver.observe(options.containerRef.value)
    })
  })

  watch(autoScrollEnabled, enabled => {
    if (!options.isFullscreen.value) return
    if (enabled) scheduleAutoScrollStart(0)
    else stopAutoScroll()
  })

  onUnmounted(() => {
    if (refreshTimer) clearInterval(refreshTimer)
    window.removeEventListener('resize', handleResize)
    document.removeEventListener('fullscreenchange', onFullscreenChange)
    stopAutoScroll()
    if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
    resizeObserver?.disconnect()
  })

  return {
    autoScrollEnabled,
    hasVerticalOverflow,
    getMaxVerticalScroll,
    scheduleAutoScrollStart,
    toggleFullscreen,
  }
}
