<template>
  <div class="changes">
    <header class="changes__head">
      <span class="smallcaps">五张表列变化 COLUMN CHANGES</span>
      <span class="mono changes__headcount">
        新增 {{ summary.columns_added ?? 0 }} · 缺失 {{ summary.columns_missing ?? 0 }} ·
        未读取 {{ summary.tables_unreadable ?? 0 }}
      </span>
    </header>

    <div v-if="!reports.length" class="changes__empty mono">
      该任务未产生逐表报告（历史任务或排队中）。
    </div>

    <ul v-else class="changes__tables">
      <li
        v-for="report in reports"
        :key="report.table_name"
        class="change-table"
        :class="{ 'change-table--failed': report.status === 'unreadable' }"
      >
        <div class="change-table__head">
          <span class="change-table__name mono">{{ report.table_name }}</span>
          <span class="change-table__status" :class="`change-table__status--${report.status}`">
            {{ report.status === 'unreadable' ? '未读取' : '可读取' }}
          </span>
          <span class="change-table__meta mono">
            目录 {{ report.catalog_column_count }} 列
            <template v-if="report.upstream_column_count !== null">
              · 上游 {{ report.upstream_column_count }} 列
            </template>
            <template v-if="report.row_count !== null"> · {{ report.row_count }} 行</template>
          </span>
        </div>

        <p v-if="report.failure_reason" class="change-table__reason mono">
          ⚠ {{ report.failure_reason }}
        </p>

        <div v-else class="change-table__cols">
          <div v-if="report.added.length" class="change-group">
            <p class="change-group__label change-group__label--added">
              新增 {{ report.added.length }}
            </p>
            <ul class="change-group__list">
              <li v-for="col in report.added" :key="col.column_name" class="change-chip change-chip--added">
                <span class="mono">{{ col.column_name }}</span>
                <span class="change-chip__type">{{ col.data_type }}</span>
              </li>
            </ul>
          </div>
          <div v-if="report.missing.length" class="change-group">
            <p class="change-group__label change-group__label--missing">
              缺失 {{ report.missing.length }}
            </p>
            <ul class="change-group__list">
              <li v-for="col in report.missing" :key="col.column_name" class="change-chip change-chip--missing">
                <span class="mono">{{ col.column_name }}</span>
                <span class="change-chip__type">{{ col.data_type }}</span>
              </li>
            </ul>
          </div>
          <p v-if="!report.added.length && !report.missing.length" class="change-group__noop mono">
            列结构无变化（{{ report.unchanged.length }} 列重新采样）
          </p>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import type { IngestJobStats, IngestTableReport } from '../../types/models'

defineProps<{
  reports: IngestTableReport[]
  summary: IngestJobStats
}>()
</script>

<style scoped>
.changes {
  border: 1px solid var(--line);
  background: color-mix(in oklch, var(--sand-raise) 60%, transparent);
}
.changes__head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--ink);
  color: var(--ink-faint);
  font-size: 9.5px;
}
.changes__headcount {
  font-size: 9.5px;
}
.changes__empty {
  padding: 16px 12px;
  color: var(--ink-faint);
  font-size: 10.5px;
}
.changes__tables {
  list-style: none;
  margin: 0;
  padding: 0;
}
.change-table {
  padding: 9px 12px;
  border-bottom: 1px dashed var(--line-soft);
}
.change-table:last-child {
  border-bottom: 0;
}
.change-table--failed {
  background: color-mix(in oklch, var(--brick-wash) 45%, transparent);
}
.change-table__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.change-table__name {
  font-size: 12px;
  color: var(--ink);
}
.change-table__status {
  font-size: 9px;
  letter-spacing: 0.08em;
  padding: 1px 5px;
  border-radius: 2px;
}
.change-table__status--readable {
  color: var(--moss);
  background: var(--moss-wash);
  border: 1px solid var(--moss-soft);
}
.change-table__status--unreadable {
  color: var(--brick);
  background: var(--brick-wash);
  border: 1px solid var(--brick-soft);
}
.change-table__meta {
  font-size: 9.5px;
  color: var(--ink-faint);
}
.change-table__reason {
  margin-top: 6px;
  font-size: 10px;
  color: var(--brick);
  word-break: break-all;
}
.change-table__cols {
  margin-top: 7px;
  display: grid;
  gap: 6px;
}
.change-group__label {
  font-size: 9px;
  letter-spacing: 0.1em;
  margin-bottom: 4px;
}
.change-group__label--added {
  color: var(--moss);
}
.change-group__label--missing {
  color: var(--brick);
}
.change-group__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.change-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  border: 1px solid var(--line);
  border-bottom-width: 2px;
  background: var(--sand-raise);
}
.change-chip--added {
  border-bottom-color: var(--moss);
  background: var(--moss-wash);
}
.change-chip--missing {
  border-bottom-color: var(--brick);
  background: var(--brick-wash);
  text-decoration: line-through;
}
.change-chip__type {
  font-size: 8.5px;
  color: var(--ink-faint);
}
.change-group__noop {
  font-size: 10px;
  color: var(--ink-faint);
}
</style>
