<template>
  <div>
    <div class="page-header"><h2>项目依赖关系</h2></div>
    <a-select placeholder="选择项目查看依赖关系" style="width: 360px; margin-bottom: 20px" v-model:value="selectedProj" allowClear
      :options="projects.map(p=>({label:p.code+' '+p.name,value:p.id}))" @change="loadDAG" />
    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />
    <a-empty v-else-if="!selectedProj" description="请选择一个项目查看依赖关系" />
    <a-card v-else-if="dagData">
      <div style="overflow: auto">
        <svg :width="svgW" :height="svgH" style="display: block">
          <defs><marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#cbd5e1" /></marker></defs>
          <path v-for="(e,i) in layoutEdges" :key="i" :d="'M '+(e.from.x+svgW/2)+' '+(e.from.y+26)+' C '+(e.from.x+svgW/2)+' '+((e.from.y+e.to.y)/2)+', '+(e.to.x+svgW/2)+' '+((e.from.y+e.to.y)/2)+', '+(e.to.x+svgW/2)+' '+(e.to.y-26)" fill="none" :stroke="e.isGateEdge ? '#64748b' : '#cbd5e1'" strokeWidth="1.5" :stroke-dasharray="e.isGateEdge ? '6 4' : undefined" markerEnd="url(#arrowhead)" />
          <g v-for="n in layoutNodes" :key="n.id" :transform="'translate('+(n.x+svgW/2-90)+','+(n.y-26)+')'">
            <rect width="180" height="52" rx="8" :fill="n.is_external_gate ? '#f8fafc' : '#fff'" :stroke="n.is_external_gate ? '#64748b' : n.requires_instrument?'#2563eb':'#e2e8f0'" :strokeWidth="n.requires_instrument || n.is_external_gate ? 1.5 : 1" :stroke-dasharray="n.is_external_gate ? '5 3' : undefined" />
            <rect v-if="n.requires_instrument" x="0" y="0" width="4" height="52" rx="2" fill="#2563eb" />
            <text :x="n.requires_instrument?16:12" y="20" fontSize="12" fontWeight="600" fill="#1e293b">{{ n.name.length>14?n.name.slice(0,14)+'...':n.name }}</text>
            <text :x="n.requires_instrument?16:12" y="38" fontSize="11" fill="#94a3b8">{{ n.is_external_gate ? '方案签批' : n.type==='instrument'?'仪器任务':n.type==='manual'?'人工任务':'等待任务' }} · <tspan :fill="statusColor(n.status)">{{ n.is_external_gate ? gateStatusLabel(n.gate_status) : statusLabel(n.status) }}</tspan></text>
          </g>
        </svg>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import { message } from "ant-design-vue"
import { getProjects, getProjectDAG } from "@/services/api"
import type { Project, DAGData } from "@/types"
import { taskStatusColor, taskStatusLabel } from '@/utils/statusMeta'

const NODE_W = 180; const NODE_H = 52; const LAYER_GAP = 100; const NODE_GAP = 24

const projects = ref<Project[]>([])
const selectedProj = ref<number | null>(null)
const dagData = ref<DAGData | null>(null)
const loading = ref(false)

interface LayoutNode { id: number; name: string; type: string; requires_instrument: boolean; status: string; is_external_gate?: boolean; gate_status?: string; x: number; y: number; layer: number }
interface LayoutEdge { from: LayoutNode; to: LayoutNode; isGateEdge: boolean }

const layout = computed(() => {
  if (!dagData.value || !dagData.value.nodes.length) return { nodes: [], edges: [], svgW: 400, svgH: 200 }
  const nodes = dagData.value.nodes; const edges = dagData.value.edges
  const children = new Map<number, number[]>(); const parents = new Map<number, number[]>()
  nodes.forEach(n => { children.set(n.id, []); parents.set(n.id, []) })
  edges.forEach(e => { children.get(e.from)?.push(e.to); parents.get(e.to)?.push(e.from) })
  const layers: number[][] = []; const assigned = new Set<number>()
  let queue = nodes.filter(n => !parents.get(n.id)?.length).map(n => n.id)
  while (queue.length) {
    const layer: number[] = []
    queue.forEach(id => { if (!assigned.has(id)) { layer.push(id); assigned.add(id) } })
    if (!layer.length) break
    layers.push(layer)
    const next: number[] = []
    layer.forEach(id => children.get(id)?.forEach(c => { if (!assigned.has(c)) next.push(c) }))
    queue = next
  }
  const nodeMap = new Map(nodes.map(n => [n.id, n]))
  const layoutNodes: LayoutNode[] = []
  layers.forEach((layer, li) => {
    const tw = layer.length * NODE_W + (layer.length-1) * NODE_GAP
    const sx = -tw/2 + NODE_W/2
    layer.forEach((nid, ni) => {
      const n = nodeMap.get(nid)!
      layoutNodes.push({ id: n.id, name: n.name, type: n.type, requires_instrument: n.requires_instrument, status: n.status, is_external_gate: n.is_external_gate, gate_status: n.gate_status, x: sx + ni*(NODE_W+NODE_GAP), y: li*(NODE_H+LAYER_GAP)+NODE_H/2, layer: li })
    })
  })
  const nl = new Map(layoutNodes.map(n => [n.id, n]))
  const layoutEdges: LayoutEdge[] = edges.map(e => ({ from: nl.get(e.from)!, to: nl.get(e.to)!, isGateEdge: Boolean(nl.get(e.from)?.is_external_gate || nl.get(e.to)?.is_external_gate) })).filter(e => e.from && e.to)
  const maxX = Math.max(...layoutNodes.map(n => Math.abs(n.x)+NODE_W/2), 400)
  const maxY = layers.length * (NODE_H+LAYER_GAP) + 40
  return { nodes: layoutNodes, edges: layoutEdges, svgW: maxX*2+80, svgH: maxY }
})

const layoutNodes = computed(() => layout.value.nodes)
const layoutEdges = computed(() => layout.value.edges)
const svgW = computed(() => layout.value.svgW)
const svgH = computed(() => layout.value.svgH)

function statusColor(status: string) { return taskStatusColor(status) }
function statusLabel(status: string) { return taskStatusLabel(status) }
function gateStatusLabel(s?: string) { const m: Record<string,string>={not_submitted:"待提交",waiting_approval:"等待客户",approved:"已签批"}; return m[s || 'not_submitted'] }

onMounted(async () => { try { projects.value = await getProjects() } catch { message.error("加载项目失败") } })
async function loadDAG() { if(!selectedProj.value){dagData.value=null;return}; loading.value=true; try{dagData.value=await getProjectDAG(selectedProj.value)}catch{message.error("加载依赖关系失败")} finally{loading.value=false} }
</script>
