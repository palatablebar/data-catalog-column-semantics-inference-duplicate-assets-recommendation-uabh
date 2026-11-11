<template>
  <span
    class="column-chip"
    :class="[
      `column-chip--${type || 'unknown'}`,
      { 'is-manual': source === 'manual' },
      { 'is-missing': syncStatus === 'missing' },
    ]"
    :title="titleText"
  >
    <span class="column-chip__name mono">{{ columnName }}</span>
    <span class="column-chip__type smallcaps">{{ typeLabel }}</span>
    <span v-if="detectedChange === 'added'" class="column-chip__change column-chip__change--added">新增</span>
    <span v-else-if="detectedChange === 'missing' || syncStatus === 'missing'" class="column-chip__change column-chip__change--missing">
      缺失
    </span>
    <span v-if="source === 'manual'" class="column-chip__flag">人工</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ColumnSemanticType } from '../../types/models'
import { COLUMN_TYPE_LABELS } from '../../constants/enums'

const props = defineProps<{
  columnName: string
  type?: ColumnSemanticType | null
  confidence?: number
  source?: string | null
  syncStatus?: string | null
  detectedChange?: string | null
}>()

const typeLabel = computed(() => COLUMN_TYPE_LABELS[props.type ?? 'unknown'] ?? '未知')
const confidenceText = computed(() => `${Math.round((props.confidence ?? 0) * 100)}%`)
const titleText = computed(() => {
  const base = `${props.columnName} · ${typeLabel.value} · 置信度 ${confidenceText.value}`
  if (props.syncStatus === 'missing') return `${base} · 上游已删除（目录软保留）`
  if (props.detectedChange === 'added') return `${base} · 本次采集新增列`
  return base
})
</script>

<style scoped>
.column-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--sand-raise);
  white-space: nowrap;
}
.column-chip__name {
  font-size: 11.5px;
  color: var(--ink);
}
.column-chip__type {
  font-size: 9px;
  color: var(--ink-faint);
}
.column-chip__flag {
  font-size: 9px;
  color: var(--brick);
  border-left: 1px solid var(--line);
  padding-left: 6px;
}
.column-chip__change {
  font-size: 9px;
  letter-spacing: 0.08em;
  padding: 0 4px;
  border-radius: 2px;
}
.column-chip__change--added {
  color: var(--moss);
  background: var(--moss-wash);
  border: 1px solid var(--moss-soft);
}
.column-chip__change--missing {
  color: var(--brick);
  background: var(--brick-wash);
  border: 1px solid var(--brick-soft);
}
.column-chip.is-missing {
  opacity: 0.62;
  border-style: dashed;
  border-bottom-color: var(--brick);
}
.column-chip.is-missing .column-chip__name {
  text-decoration: line-through;
}
.column-chip--phone,
.column-chip--id_card {
  border-bottom-color: var(--brick);
}
.column-chip--email {
  border-bottom-color: var(--gold);
}
.column-chip--amount {
  border-bottom-color: var(--moss);
}
.column-chip--datetime {
  border-bottom-color: var(--moss-soft);
}
.column-chip--enum {
  border-bottom-color: var(--ink-faint);
}
.column-chip--ambiguous {
  border-bottom-color: var(--gold);
  border-bottom-style: dashed;
}
.column-chip--unknown {
  border-bottom-color: var(--line);
  color: var(--ink-soft);
}
.column-chip--phone .column-chip__type,
.column-chip--id_card .column-chip__type {
  color: var(--brick);
}
.column-chip.is-manual {
  background: var(--brick-wash);
}
</style>
