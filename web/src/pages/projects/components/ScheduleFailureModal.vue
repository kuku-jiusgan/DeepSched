<template>
  <div>
    <component :is="failureContent" />
    <div v-if="jobStatus === 'pending' || jobStatus === 'running'" class="schedule-failure-job-status">
      正在通过完整排程约束计算方案C，请稍候，结果会自动更新。
    </div>
    <div v-else-if="jobStatus === 'failed' || jobStatus === 'stale'" class="schedule-failure-job-status is-error">
      方案C暂未生成，请调整计划后重新排程。
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getDeadlineRecommendation } from '@/services/api'
import type { ProjectPlanApplyResult, ScheduleFailureDiagnostic } from '@/types'
import { scheduleFailureContent } from '../planScheduleFailure'

const props = defineProps<{ projectId: number; result: ProjectPlanApplyResult }>()
const diagnostic = ref<ScheduleFailureDiagnostic | null>(props.result.schedule_failure || null)
const jobStatus = ref(props.result.schedule_failure?.recommendation_job?.status || 'completed')
let timer: ReturnType<typeof setTimeout> | undefined

const failureContent = computed(() => scheduleFailureContent({
  ...props.result,
  schedule_failure: diagnostic.value,
}))

function stopPolling() {
  if (timer) { clearTimeout(timer); timer = undefined }
}

async function pollRecommendation() {
  const jobId = props.result.schedule_failure?.recommendation_job?.id
  if (!jobId || !['pending', 'running'].includes(jobStatus.value)) return
  try {
    const job = await getDeadlineRecommendation(props.projectId, jobId)
    jobStatus.value = job.status
    if (job.status === 'completed' && job.recommendation && diagnostic.value) {
      diagnostic.value = {
        ...diagnostic.value,
        recommendations: [...(diagnostic.value.recommendations || []), job.recommendation],
      }
    }
  } catch {
    jobStatus.value = 'failed'
  }
  if (['pending', 'running'].includes(jobStatus.value)) {
    timer = setTimeout(pollRecommendation, 1500)
  }
}

onMounted(pollRecommendation)
onBeforeUnmount(stopPolling)
</script>
