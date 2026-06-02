export type ConfigFieldType = 'bool' | 'int' | 'string' | 'enum' | 'secret'

export interface ConfigFieldSchema {
  id: string
  type: ConfigFieldType
  default: string | number | boolean | null
  options: string[] | null
  min_value: number | null
  read_only: boolean
  exclusive_group: string | null
  exclusive_variant: 'local' | 'remote' | null
  label: string
  description: string
}

export interface ConfigVariantSchema {
  id: 'local' | 'remote'
  label: string
  fields: ConfigFieldSchema[]
}

export interface ConfigSectionSchema {
  id: string
  label: string
  fields: ConfigFieldSchema[]
  exclusive?: {
    group: string
    variants: ConfigVariantSchema[]
  }
}

export interface ConfigEditorData {
  config_path: string
  locale: string
  schema: { sections: ConfigSectionSchema[] }
  values: Record<string, string | number | boolean | null>
  defaults: Record<string, string | number | boolean | null>
  set_keys: string[]
  secrets_set: string[]
  env_locked: string[]
  embed_mode: 'local' | 'remote'
  exclusive_meta?: {
    embed_backend: {
      local: { clears_on_save: string[] }
      remote: { clears_on_save: string[] }
    }
  }
  effective: Record<string, unknown>
}

export type ConfigValues = Record<string, string | number | boolean | null>
