<script setup lang="ts">
import {
  CategoryScale,
  Chart,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
  type ChartData,
} from 'chart.js'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

interface TrendEntry {
  date: string
  store: string
  price: number
}

const props = defineProps<{ entries: TrendEntry[] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
let chart: Chart<'line'> | null = null

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend)

function renderChart(): void {
  if (!canvas.value) return
  chart?.destroy()
  const data: ChartData<'line'> = {
    labels: props.entries.map((entry) => entry.date),
    datasets: [{
      label: '消費明細單價',
      data: props.entries.map((entry) => entry.price),
      borderColor: '#176b87',
      backgroundColor: '#176b87',
      pointRadius: props.entries.length > 30 ? 2 : 4,
      pointHoverRadius: 6,
      tension: .2,
    }],
  }
  chart = new Chart<'line'>(canvas.value, {
    type: 'line',
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      scales: { y: { beginAtZero: false } },
      plugins: {
        tooltip: {
          callbacks: {
            afterLabel: (context) => props.entries[context.dataIndex]?.store ?? '',
          },
        },
      },
    },
  })
}

watch(() => props.entries, renderChart, { deep: true })
onMounted(renderChart)
onBeforeUnmount(() => chart?.destroy())
</script>

<template>
  <div class="trend-chart"><canvas ref="canvas" aria-label="商品價格趨勢圖" role="img" /></div>
</template>
