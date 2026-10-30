// Page-wide behavior: theme switch and reveal on scroll.
// The page works without this file. The script only adds to it.

const root = document.documentElement;
root.classList.add('js-ready');

// --- Theme -----------------------------------------------------------------
// With no choice saved, the page follows the system. A click saves a choice.
const dark = window.matchMedia('(prefers-color-scheme: dark)');
const effectiveTheme = (): 'dark' | 'light' => (root.dataset.theme as 'dark' | 'light' | undefined) ?? (dark.matches ? 'dark' : 'light');

function syncToggles(): void {
  const isDark = effectiveTheme() === 'dark';
  document.querySelectorAll<HTMLButtonElement>('[data-theme-toggle]').forEach((button) => {
    const label = isDark ? button.dataset.toLight : button.dataset.toDark;
    if (label) button.setAttribute('aria-label', label);
  });
}

document.addEventListener('click', (event) => {
  const toggle = (event.target as Element | null)?.closest('[data-theme-toggle]');
  if (!toggle) return;
  const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
  root.dataset.theme = next;
  try {
    localStorage.setItem('zycode-theme', next);
  } catch {
    // Private mode can block storage. The choice then lasts until the page closes.
  }
  syncToggles();
});
dark.addEventListener('change', syncToggles);
syncToggles();

// --- Reveal on scroll --------------------------------------------------------
const items = document.querySelectorAll<HTMLElement>('.reveal');
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (reduceMotion || !('IntersectionObserver' in window)) {
  items.forEach((item) => item.classList.add('in'));
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add('in');
        observer.unobserve(entry.target);
      }
    },
    { rootMargin: '0px 0px -8% 0px', threshold: 0.08 },
  );
  items.forEach((item) => observer.observe(item));
}
