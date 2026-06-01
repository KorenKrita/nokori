type Theme = 'light' | 'dark' | 'system'

function getSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getStoredTheme(): Theme {
  return (localStorage.getItem('nokori-theme') as Theme) || 'system'
}

function applyTheme(theme: Theme) {
  const resolved = theme === 'system' ? getSystemTheme() : theme
  document.documentElement.classList.toggle('dark', resolved === 'dark')
  document.documentElement.classList.toggle('light', resolved === 'light')
}

export function initTheme() {
  const theme = getStoredTheme()
  applyTheme(theme)

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (getStoredTheme() === 'system') {
      applyTheme('system')
    }
  })
}

export function setTheme(theme: Theme) {
  localStorage.setItem('nokori-theme', theme)
  applyTheme(theme)
}

export function getTheme(): Theme {
  return getStoredTheme()
}

export type { Theme }
