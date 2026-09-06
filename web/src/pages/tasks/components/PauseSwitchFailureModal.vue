<template>
  <component :is="failureContent" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PauseSwitchFailure } from '@/types/pauseSwitchFailure'
import { pauseSwitchFailureContent } from '../pauseSwitchFailure'

const props = defineProps<{ failure: PauseSwitchFailure }>()

// 诊断在失败响应里一次给全，没有后台作业要等，所以这里不轮询任何东西。
const failureContent = computed(() => pauseSwitchFailureContent(props.failure))
</script>
