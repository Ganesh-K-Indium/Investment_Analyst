/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  corePlugins: {
    // The app already ships its own global reset (src/index.css) applied
    // consistently across every page. Skipping Tailwind's preflight avoids
    // a second, slightly different reset fighting the existing one.
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        // shadcn/ui contract (bg-background, text-foreground, border-input, ring-ring, ...)
        // — aliased in src/index.css onto the app's own tokens, see the "shadcn/ui bridge" comment there.
        background: 'var(--background)',
        foreground: 'var(--foreground)',
        card: { DEFAULT: 'var(--card)', foreground: 'var(--card-foreground)' },
        popover: { DEFAULT: 'var(--popover)', foreground: 'var(--popover-foreground)' },
        secondary: { DEFAULT: 'var(--secondary)', foreground: 'var(--secondary-foreground)' },
        muted: { DEFAULT: 'var(--muted)', foreground: 'var(--muted-foreground)' },
        accent: { DEFAULT: 'var(--accent)', foreground: 'var(--accent-foreground)' },
        destructive: { DEFAULT: 'var(--destructive)', foreground: 'var(--destructive-foreground)' },
        input: 'var(--input)',
        ring: 'var(--ring)',

        // App-specific tokens (existing palette, usable directly as Tailwind utilities)
        primary: {
          DEFAULT: 'var(--color-primary)',
          hover: 'var(--color-primary-hover)',
          light: 'var(--color-primary-light)',
          foreground: 'var(--primary-foreground)',
        },
        bg: {
          primary: 'var(--color-bg-primary)',
          secondary: 'var(--color-bg-secondary)',
          tertiary: 'var(--color-bg-tertiary)',
          sidebar: 'var(--color-bg-sidebar)',
        },
        text: {
          primary: 'var(--color-text-primary)',
          secondary: 'var(--color-text-secondary)',
          tertiary: 'var(--color-text-tertiary)',
          placeholder: 'var(--color-text-placeholder)',
          inverse: 'var(--color-text-inverse)',
        },
        border: {
          DEFAULT: 'var(--border)',
          light: 'var(--color-border-light)',
        },
        success: { DEFAULT: 'var(--color-success)', dark: 'var(--color-success-dark)' },
        warning: { DEFAULT: 'var(--color-warning)', dark: 'var(--color-warning-dark)' },
        error: { DEFAULT: 'var(--color-error)', dark: 'var(--color-error-dark)' },
        info: {
          DEFAULT: 'var(--color-info)',
          medium: 'var(--color-info-medium)',
          dark: 'var(--color-info-dark)',
          deeper: 'var(--color-info-deeper)',
        },
        'ai-accent': 'var(--color-ai-accent)',
        indigo: { DEFAULT: 'var(--color-indigo)', dark: 'var(--color-indigo-dark)' },
        'brand-secondary': 'var(--color-brand-secondary)',
        'code-muted': 'var(--color-code-muted)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
        full: 'var(--radius-full)',
      },
      spacing: {
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        5: 'var(--space-5)',
        6: 'var(--space-6)',
        8: 'var(--space-8)',
        10: 'var(--space-10)',
        12: 'var(--space-12)',
        16: 'var(--space-16)',
      },
      fontFamily: {
        sans: ['var(--font-family)'],
      },
      fontSize: {
        xs: 'var(--font-size-xs)',
        sm: 'var(--font-size-sm)',
        base: 'var(--font-size-base)',
        lg: 'var(--font-size-lg)',
        xl: 'var(--font-size-xl)',
        '2xl': 'var(--font-size-2xl)',
        '3xl': 'var(--font-size-3xl)',
        '4xl': 'var(--font-size-4xl)',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
        card: 'var(--shadow-card)',
        'card-hover': 'var(--shadow-card-hover)',
        'primary-glow': 'var(--shadow-primary-glow)',
      },
      backgroundImage: {
        'gradient-primary': 'var(--gradient-primary)',
        'gradient-primary-hover': 'var(--gradient-primary-hover)',
      },
      transitionDuration: {
        fast: '150ms',
        normal: '200ms',
        slow: '300ms',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
