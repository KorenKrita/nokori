import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import { motion } from 'motion/react'
import { CaretDown } from '@phosphor-icons/react'
import { GlassCard } from '@/components/GlassCard'
import { StatusBadge } from '@/components/StatusBadge'
import { PageSkeleton } from '@/components/PageSkeleton'
import { Alert } from '@/components/Alert'
import { ConfigField } from '@/components/ConfigField'
import { ExclusiveVariantPanel } from '@/components/ExclusiveVariantPanel'
import { ConfigSaveBar } from '@/components/ConfigSaveBar'
import { ConfigSectionNav } from '@/components/ConfigSectionNav'
import { ConfigToolbar } from '@/components/ConfigToolbar'
import { ConfigSectionCard } from '@/components/ConfigSectionCard'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { useApi } from '@/hooks/useApi'
import { useLocale } from '@/hooks/useLocale'
import { useHotkey } from '@/hooks/useHotkey'
import { useSectionObserver } from '@/hooks/useSectionObserver'
import { mutateApi } from '@/lib/api'
import { clearOppositeEmbedBranch } from '@/lib/configEmbed'
import {
  DEFAULT_COLLAPSED_SECTIONS,
  fieldMatchesQuery,
  fieldMatchesSetFilter,
  sectionHasVisibleContent,
  visibleFieldsInSection,
} from '@/lib/configFilters'
import type { ConfigEditorData, ConfigFieldSchema, ConfigValues } from '@/lib/configTypes'
import { t } from '@/lib/i18n'

interface HealthData { data: Record<string, { status: string; detail: string }> }

type Snapshot = {
  values: ConfigValues
  embed_mode: 'local' | 'remote'
  set_keys: string[]
}

function healthCounts(data: HealthData['data'] | undefined) {
  const entries = Object.values(data ?? {})
  return {
    ok: entries.filter((e) => e.status === 'ok').length,
    warn: entries.filter((e) => e.status === 'warn').length,
    fail: entries.filter((e) => e.status === 'fail').length,
  }
}

export function Config() {
  const { locale } = useLocale()
  const { data: editorRes, isLoading: l1, error: loadError, refetch } = useApi<{ data: ConfigEditorData }>(
    '/config/editor',
    { locale },
  )
  const { data: health, isLoading: l2, error: healthError } = useApi<HealthData>('/health')

  const editor = editorRes?.data
  const [values, setValues] = useState<ConfigValues>({})
  const [embedMode, setEmbedMode] = useState<'local' | 'remote'>('local')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState(false)
  const [healthOpen, setHealthOpen] = useState(false)
  const [activeSection, setActiveSection] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(DEFAULT_COLLAPSED_SECTIONS))
  const [search, setSearch] = useState('')
  const [onlySetInFile, setOnlySetInFile] = useState(false)
  const [pendingEmbed, setPendingEmbed] = useState<'local' | 'remote' | null>(null)
  const [scrollNavEnabled, setScrollNavEnabled] = useState(true)
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({})
  const jumpTimeouts = useRef<number[]>([])

  useEffect(() => {
    return () => {
      jumpTimeouts.current.forEach(clearTimeout)
    }
  }, [])

  const applyEditor = useCallback((data: ConfigEditorData) => {
    setValues({ ...data.values })
    setEmbedMode(data.embed_mode)
    setSnapshot({
      values: { ...data.values },
      embed_mode: data.embed_mode,
      set_keys: [...data.set_keys],
    })
    setSaveError(null)
  }, [])

  useEffect(() => {
    if (editor && !savingRef.current) {
      applyEditor(editor)
      const first = editor.schema.sections[0]?.id ?? null
      setActiveSection(first)
    }
    savingRef.current = false
  }, [editor, applyEditor])

  const setKeys = useMemo(() => new Set(editor?.set_keys ?? []), [editor])
  const secretsSet = useMemo(() => new Set(editor?.secrets_set ?? []), [editor])
  const envLocked = useMemo(() => new Set(editor?.env_locked ?? []), [editor])

  const clearsOnSave = useMemo(() => {
    const meta = editor?.exclusive_meta?.embed_backend
    if (!meta) return []
    return embedMode === 'local' ? meta.local.clears_on_save : meta.remote.clears_on_save
  }, [editor, embedMode])

  const dirty = useMemo(() => {
    if (!snapshot) return false
    if (embedMode !== snapshot.embed_mode) return true
    return JSON.stringify(values) !== JSON.stringify(snapshot.values)
  }, [values, embedMode, snapshot])

  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])

  const blocker = useBlocker(dirty)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      const confirmed = window.confirm(t('config.leave_unsaved'))
      if (confirmed) {
        blocker.proceed()
      } else {
        blocker.reset()
      }
    }
  }, [blocker])

  const counts = useMemo(() => healthCounts(health?.data), [health])

  const sections = editor?.schema.sections ?? []
  const sectionIds = useMemo(() => sections.map((s) => s.id), [sections])

  const fieldVisible = useCallback(
    (field: ConfigFieldSchema) =>
      fieldMatchesQuery(field, search) && fieldMatchesSetFilter(field.id, onlySetInFile, setKeys),
    [search, onlySetInFile, setKeys],
  )

  const visibleSections = useMemo(
    () => sections.filter((s) => sectionHasVisibleContent(s, search, onlySetInFile, setKeys)),
    [sections, search, onlySetInFile, setKeys],
  )

  const onActiveFromScroll = useCallback((id: string) => {
    if (scrollNavEnabled) setActiveSection(id)
  }, [scrollNavEnabled])

  useSectionObserver(sectionIds, sectionRefs, onActiveFromScroll, scrollNavEnabled && visibleSections.length > 0)

  const onChange = (id: string, value: string | number | boolean | null) => {
    setValues((prev) => ({ ...prev, [id]: value }))
    setSaveOk(false)
  }

  const applyEmbedMode = (mode: 'local' | 'remote') => {
    setEmbedMode(mode)
    if (editor?.defaults) {
      setValues((prev) => clearOppositeEmbedBranch(prev, editor.defaults, mode))
    }
    setSaveOk(false)
  }

  const onCancel = () => {
    if (snapshot) {
      setValues({ ...snapshot.values })
      setEmbedMode(snapshot.embed_mode)
    }
    setSaveError(null)
    setSaveOk(false)
  }

  const onSave = useCallback(async () => {
    if (!snapshot) return
    savingRef.current = true
    setSaving(true)
    setSaveError(null)
    setSaveOk(false)
    try {
      await mutateApi('/config/editor', 'PUT', {
        values,
        embed_mode: embedMode,
        set_keys: snapshot.set_keys,
      })
      await refetch()
      savingRef.current = false
      setSaveOk(true)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed')
      savingRef.current = false
    } finally {
      setSaving(false)
    }
  }, [snapshot, values, embedMode, refetch])

  useHotkey('s', (e) => {
    if (dirty && !savingRef.current) {
      e.preventDefault()
      void onSave()
    }
  }, { enabled: Boolean(snapshot) })

  const jumpToSection = (id: string) => {
    jumpTimeouts.current.forEach(clearTimeout)
    jumpTimeouts.current = []
    setScrollNavEnabled(false)
    setActiveSection(id)
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
    requestAnimationFrame(() => {
      sectionRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      const t1 = window.setTimeout(() => setScrollNavEnabled(true), 500)
      jumpTimeouts.current.push(t1)
    })
  }

  const toggleSection = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const pendingEmbedLabel =
    pendingEmbed === 'local'
      ? editor?.schema.sections.find((s) => s.id === 'embed')?.exclusive?.variants.find((v) => v.id === 'local')?.label
      : editor?.schema.sections.find((s) => s.id === 'embed')?.exclusive?.variants.find((v) => v.id === 'remote')?.label

  if (l1 || l2) return <PageSkeleton />

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
      className="space-y-6 pb-28"
    >
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">{t('config.title')}</h2>
        <p className="text-sm text-text-secondary mt-1">{t('config.editor_subtitle')}</p>
        {editor?.config_path && (
          <p className="text-xs font-mono text-text-tertiary mt-1 truncate" title={editor.config_path}>
            {editor.config_path}
          </p>
        )}
        <p className="text-xs text-text-tertiary mt-2 hidden sm:block">{t('config.save_shortcut')}</p>
      </div>

      {loadError && <Alert variant="error">{loadError}</Alert>}
      {saveError && <Alert variant="error">{saveError}</Alert>}
      {saveOk && <Alert variant="success">{t('config.saved')}</Alert>}

      <GlassCard hover={false}>
        <button
          type="button"
          className="w-full flex items-center justify-between gap-3 text-left"
          onClick={() => setHealthOpen((o) => !o)}
          aria-expanded={healthOpen}
        >
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
              {t('config.health')}
            </h3>
            {healthError && (
              <p className="text-sm text-accent-rose mt-1">{healthError}</p>
            )}
            {!healthError && !healthOpen && (
              <p className="text-sm text-text-secondary mt-1">
                {t('config.health_summary', {
                  ok: String(counts.ok),
                  warn: String(counts.warn),
                  fail: String(counts.fail),
                })}
              </p>
            )}
          </div>
          <span className="flex items-center gap-2 shrink-0 text-xs text-text-tertiary">
            {healthOpen ? t('config.health_collapse') : t('config.health_expand')}
            <CaretDown
              size={16}
              weight="bold"
              className={`transition-transform duration-300 ${healthOpen ? 'rotate-180' : ''}`}
            />
          </span>
        </button>
        {healthOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            className="mt-4 space-y-2 border-t border-[var(--color-border-subtle)] pt-4"
          >
            {Object.entries(health?.data ?? {}).map(([key, check]) => (
              <div
                key={key}
                className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between py-2 border-b border-[var(--color-border-subtle)] last:border-0"
              >
                <span className="text-sm text-text-secondary">{key}</span>
                <div className="flex items-center gap-2 min-w-0 sm:justify-end">
                  <span className="text-xs font-mono text-text-tertiary sm:max-w-[280px] truncate">
                    {check.detail}
                  </span>
                  <StatusBadge status={check.status} />
                </div>
              </div>
            ))}
          </motion.div>
        )}
      </GlassCard>

      <ConfigToolbar
        search={search}
        onSearchChange={setSearch}
        onlySetInFile={onlySetInFile}
        onOnlySetInFileChange={setOnlySetInFile}
      />

      {visibleSections.length > 0 && (
        <ConfigSectionNav
          sections={visibleSections.map((s) => ({ id: s.id, label: s.label }))}
          activeId={activeSection}
          onSelect={jumpToSection}
        />
      )}

      {search.trim() && visibleSections.length === 0 && (
        <p className="text-sm text-text-tertiary text-center py-8">{t('config.no_results')}</p>
      )}

      <div>
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-tertiary mb-3">
          {t('config.sections_title')}
        </h3>
        <div className="space-y-4">
          {visibleSections.map((section, i) => {
            const fields = visibleFieldsInSection(section, search, onlySetInFile, setKeys)
            const showExclusive = section.exclusive && (
              section.exclusive.variants.some((v) =>
                v.fields.some((f) => fieldVisible(f)),
              )
            )
            const fieldCount = fields.length + (showExclusive
              ? section.exclusive!.variants.flatMap((v) => v.fields).filter((f) => fieldVisible(f)).length
              : 0)

            return (
              <motion.div
                key={section.id}
                ref={(el) => {
                  sectionRefs.current[section.id] = el
                }}
                id={`config-section-${section.id}`}
                className="scroll-mt-16"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04, duration: 0.35, ease: [0.32, 0.72, 0, 1] }}
              >
                <ConfigSectionCard
                  id={section.id}
                  label={section.label}
                  collapsed={collapsed.has(section.id)}
                  onToggle={() => toggleSection(section.id)}
                  hover={section.id !== 'embed'}
                  fieldCount={fieldCount}
                >
                  {fields.map((field) => (
                    <ConfigField
                      key={field.id}
                      field={field}
                      value={values[field.id]}
                      onChange={onChange}
                      envLocked={envLocked.has(field.id)}
                      secretConfigured={secretsSet.has(field.id)}
                      setInFile={setKeys.has(field.id)}
                    />
                  ))}
                  {showExclusive && section.exclusive && (
                    <ExclusiveVariantPanel
                      variants={section.exclusive.variants.map((v) => ({
                        ...v,
                        fields: v.fields.filter((f) => fieldVisible(f)),
                      }))}
                      active={embedMode}
                      onRequestActiveChange={setPendingEmbed}
                      values={values}
                      onChange={onChange}
                      setKeys={setKeys}
                      secretsSet={secretsSet}
                      envLocked={envLocked}
                      clearsOnSave={clearsOnSave}
                    />
                  )}
                </ConfigSectionCard>
              </motion.div>
            )
          })}
        </div>
      </div>

      {/* left-60 must match sidebar width in Layout.tsx */}
      <div className="fixed bottom-0 left-60 right-0 p-4 border-t border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)]/95 backdrop-blur-xl flex justify-end z-20 shadow-[var(--color-card-shadow)]">
        <ConfigSaveBar dirty={dirty} saving={saving} onCancel={onCancel} onSave={onSave} />
      </div>

      <ConfirmDialog
        open={pendingEmbed !== null}
        title={t('config.embed_switch_title')}
        message={t('config.embed_switch_message', { mode: pendingEmbedLabel ?? pendingEmbed ?? '' })}
        confirmLabel={t('config.embed_switch_confirm')}
        cancelLabel={t('config.cancel')}
        variant="danger"
        onConfirm={() => {
          if (pendingEmbed) applyEmbedMode(pendingEmbed)
          setPendingEmbed(null)
        }}
        onCancel={() => setPendingEmbed(null)}
      />
    </motion.div>
  )
}
