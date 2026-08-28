<template>
  <div>
    <component :is="failureContent" />
    <!-- 计算中的提示由「调整方案」区域自己显示，这里不再重复一遍。 -->
    <div v-if="jobStatus === 'failed' || jobStatus === 'stale'" class="schedule-failure-job-status is-error">
      调整方案暂未生成，请调整计划后重新排程。
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
    if (job.status === 'completed' && diagnostic.value) {
      diagnostic.value = {
        ...diagnostic.value,
        recommendations: job.recommendations || (job.recommendation ? [job.recommendation] : []),
        recommendation_job: {
          ...(diagnostic.value.recommendation_job || { id: jobId }),
          status: 'completed',
        },
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
